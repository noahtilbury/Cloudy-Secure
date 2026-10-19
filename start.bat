@echo off
setlocal EnableExtensions
title sunnysecure - start

rem Double-click launcher. It does not use PowerShell on purpose: Windows ships
rem with the execution policy set to Restricted, which makes start.ps1 fail with
rem "running scripts is disabled on this system".

rem UTF-8 console. Log lines carry emoji and guild names ("┃", "™") that a
rem cp1252/cp850 console cannot encode, which used to print a full
rem "--- Logging error ---" traceback per line. bot.py also forces UTF-8 on its own
rem streams; this keeps the console window in the same code page.
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "ROOT=%~dp0"
cd /d "%ROOT%"

set "VENV_PY=%ROOT%.venv\Scripts\python.exe"

if exist "%VENV_PY%" goto have_venv

echo [1/4] Creating the virtual environment...
set "PY_CMD="
py -3 --version >nul 2>&1 && set "PY_CMD=py -3"
if not defined PY_CMD (
    python --version >nul 2>&1 && set "PY_CMD=python"
)
if not defined PY_CMD (
    echo [X] Python was not found. Install Python 3.11+ from python.org and tick
    echo     "Add python.exe to PATH", then run this file again.
    pause
    exit /b 1
)
%PY_CMD% -m venv "%ROOT%.venv"
if not exist "%VENV_PY%" (
    echo [X] Could not create .venv with "%PY_CMD%".
    pause
    exit /b 1
)
goto deps

:have_venv
echo [1/4] Virtual environment found.

:deps
echo [2/4] Installing requirements...
"%VENV_PY%" -m pip install --upgrade pip --quiet --disable-pip-version-check
"%VENV_PY%" -m pip install -r "%ROOT%requirements.txt" --quiet --disable-pip-version-check
if errorlevel 1 (
    echo [X] Dependency install failed. Run this to see why:
    echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

rem A fresh download has no config\config.json (it holds the bot token, so it is
rem gitignored). Create it from the shipped example and stop here with instructions
rem instead of letting the bot crash on a missing file.
:config
echo [3/4] Checking config\config.json...
if exist "config\config.json" goto start_bot
if not exist "config\config.json.example" (
    echo [X] config\config.json is missing and there is no config\config.json.example
    echo     to copy. Restore that file from the repository and run this again.
    pause
    exit /b 1
)
copy /y "config\config.json.example" "config\config.json" >nul
echo.
echo ------------------------------------------------------------
echo  config\config.json was created from the example.
echo.
echo  Open it and fill in:
echo    tokens.bot_token    your bot token from
echo                        https://discord.com/developers/applications
echo    owners              your Discord user ID (Developer Mode on,
echo                        right-click yourself, "Copy User ID")
echo    discord.*_channel   the channel IDs the bot posts in
echo.
echo  Save the file, then run start.bat again.
echo  config\bot.json (commands, embeds, presence) already works as shipped.
echo ------------------------------------------------------------
echo.
pause
exit /b 0

:start_bot
echo       config\config.json found.
echo [4/4] Starting the bot...
echo       Close this window or press Ctrl+C to stop it.
echo.
"%VENV_PY%" -u "%ROOT%bot.py"
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo.
    echo [X] The bot exited with code %RC% - the error above says why.
    pause
)
exit /b %RC%
