"""Pre-flight checks for a fresh Cloudy Secure install.

``setup.bat`` (and therefore ``start.bat``) runs this after the virtual
environment and the dependencies are in place.  It never touches the network,
never prints a secret and never starts the bot: it only says what is missing or
wrong and how to fix it.

Exit codes:
    0   the installation is ready to run
    1   something is missing or invalid (the message says exactly what)
    2   the config file was just created from the example and still has to be
        filled in before the bot can start
"""

from __future__ import annotations

import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "config", "config.json")
CONFIG_EXAMPLE = CONFIG_PATH + ".example"
BOT_CONFIG_PATH = os.path.join(ROOT, "config", "bot.json")
BOT_CONFIG_EXAMPLE = BOT_CONFIG_PATH + ".example"

MIN_PYTHON = (3, 11)

# Values that mean "the example was copied but nobody edited it yet".
_PLACEHOLDER_TOKENS = {
    "",
    "TOKEN",
    "BOT_TOKEN",
    "YOUR_BOT_TOKEN",
    "PASTE_TOKEN_HERE",
    "NONE",
    "NULL",
}


def _fail(message: str) -> int:
    print(f"[X] {message}", flush=True)
    return 1


def _warn(message: str) -> None:
    print(f"[!] {message}", flush=True)


def check_python() -> int:
    """The interpreter must be new enough for the code and the dependencies."""
    if sys.version_info < MIN_PYTHON:
        wanted = ".".join(str(part) for part in MIN_PYTHON)
        print(
            f"[X] Python {wanted}+ is required, this is "
            f"{sys.version_info.major}.{sys.version_info.minor}.",
            flush=True,
        )
        print(
            "    Install a newer Python from https://www.python.org/downloads/"
            " and tick \"Add python.exe to PATH\".",
            flush=True,
        )
        return 1
    return 0


def _load_json(path: str, label: str) -> tuple[dict | None, str | None]:
    """Return ``(data, error)`` for a JSON object file."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return None, f"{label} is missing: {path}"
    except json.JSONDecodeError as exc:
        return None, f"{label} is not valid JSON ({os.path.basename(path)}): {exc}"
    except OSError as exc:
        return None, f"{label} could not be read ({path}): {exc}"
    if not isinstance(data, dict):
        return None, f"{label} must contain a JSON object: {path}"
    return data, None


def ensure_config_files() -> int:
    """Make sure both config files exist; create them from the examples."""
    created = False

    if not os.path.isfile(CONFIG_PATH):
        if not os.path.isfile(CONFIG_EXAMPLE):
            return _fail(
                "config\\config.json is missing and there is no "
                "config\\config.json.example to copy it from."
            )
        try:
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            shutil.copyfile(CONFIG_EXAMPLE, CONFIG_PATH)
        except OSError as exc:
            return _fail(f"config\\config.json could not be created: {exc}")
        created = True

    if not os.path.isfile(BOT_CONFIG_PATH):
        if not os.path.isfile(BOT_CONFIG_EXAMPLE):
            return _fail(
                "config\\bot.json is missing and there is no "
                "config\\bot.json.example to copy it from."
            )
        try:
            os.makedirs(os.path.dirname(BOT_CONFIG_PATH), exist_ok=True)
            shutil.copyfile(BOT_CONFIG_EXAMPLE, BOT_CONFIG_PATH)
        except OSError as exc:
            return _fail(f"config\\bot.json could not be created: {exc}")
        created = True

    return 2 if created else 0


def check_config() -> int:
    """Validate the two config files and report what still has to be filled in."""
    config, error = _load_json(CONFIG_PATH, "config/config.json")
    if error:
        return _fail(error)

    bot_config, error = _load_json(BOT_CONFIG_PATH, "config/bot.json")
    if error:
        return _fail(error)

    problems = 0

    tokens = config.get("tokens")
    token = ""
    if isinstance(tokens, dict):
        token = str(tokens.get("bot_token") or "").strip()
    if token.upper() in _PLACEHOLDER_TOKENS or token.startswith("YOUR_"):
        print("[X] config/config.json has no bot token yet.", flush=True)
        print(
            "    Set tokens.bot_token to your bot's token "
            "(Discord Developer Portal -> your application -> Bot -> Reset Token).",
            flush=True,
        )
        problems += 1

    owners = config.get("owners")
    valid_owners = [
        owner
        for owner in (owners if isinstance(owners, list) else [])
        if str(owner).strip() not in ("", "0", "None")
    ]
    if not valid_owners:
        _warn(
            "config/config.json has no owners - the admin commands "
            "(/secure, /email, ...) will refuse everyone."
        )
        print(
            "    Add your Discord user ID to owners and restart "
            "(Developer Mode -> right-click yourself -> Copy User ID).",
            flush=True,
        )

    discord = config.get("discord")
    if not isinstance(discord, dict):
        _warn("config/config.json has no \"discord\" section - no logs will be posted.")
    else:
        for key in ("logs_channel", "accounts_channel"):
            value = str(discord.get(key) or "").strip()
            if not value:
                _warn(
                    f"config/config.json: discord.{key} is empty - the bot has "
                    f"nowhere to post that content."
                )

    enabled = bot_config.get("enabled_commands", True)
    if isinstance(enabled, dict):
        aliases = enabled.get("aliases")
        if not isinstance(aliases, dict) or not aliases:
            _warn(
                "config/bot.json: enabled_commands.aliases is empty - the slash "
                "commands will use their default names."
            )

    if problems:
        return 1
    return 0


def main() -> int:
    if check_python():
        return 1

    created = ensure_config_files()
    if created == 1:
        return 1

    # check_config() has already printed exactly what is missing.
    result = check_config()

    if created == 2:
        print("", flush=True)
        print("-" * 60, flush=True)
        print("  The config files were created from the examples.", flush=True)
        print("", flush=True)
        print("  Open config\\config.json and fill in:", flush=True)
        print("    tokens.bot_token    your bot token", flush=True)
        print("    owners              your Discord user ID", flush=True)
        print("    discord.*_channel   the channel IDs the bot posts in", flush=True)
        print("", flush=True)
        print("  Save the file and run start.bat again.", flush=True)
        print("-" * 60, flush=True)
        return 2

    if result != 0:
        return result

    print("[ok] configuration looks good.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
