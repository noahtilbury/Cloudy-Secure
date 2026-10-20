@echo off
setlocal EnableExtensions DisableDelayedExpansion
title sunnysecure - launcher

rem ---------------------------------------------------------------------------
rem Double-click launcher for sunnysecure.
rem
rem Flow:
rem   1. Install/upgrade Python + virtualenv prerequisites (idempotent).
rem   2. On first run, prompt the buyer for a license key issued by the
rem      developer.  The key is accepted once, used to activate the machine,
rem      then discarded.  Only public activation state is stored in
rem      %LOCALAPPDATA%\Autosecure\License (outside this project folder).
rem   3. Every subsequent run validates the stored activation before starting
rem      the bot.  If the license is invalid, expired, revoked, or tampered
rem      with, the bot does not connect to Discord.
rem
rem The private license-generation key and the key-generation tool (dev_keygen.py)
rem are NEVER shipped in this file, in the project, or in the installed package.
rem ---------------------------------------------------------------------------

chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

rem --- config ----------------------------------------------------------------
set "ROOT=%~dp0"
set "VENV_PY=%ROOT%.venv\Scripts\python.exe"
set "LICENSE_TTL_DAYS=7"
set "LICENSE_GATE=1"

rem Honour an explicit TTL override from the environment (buyer-facing only via
rem the developer re-packing the zip).  This is intentionally NOT a config file
rem buyers can edit; it is only useful if you re-zip with a different default.
if defined AUTOSECURE_LICENSE_TTL_DAYS set "LICENSE_TTL_DAYS=%AUTOSECURE_LICENSE_TTL_DAYS%"
if defined AUTOSECURE_LICENSE_GATE set "LICENSE_GATE=%AUTOSECURE_LICENSE_GATE%"

rem Export for the Python licensing code so LICENSE_TTL_DAYS and the gate switch
rem are honoured by license_gate.py and the license package.
set "AUTOSECURE_LICENSE_TTL_DAYS=%LICENSE_TTL_DAYS%"
set "AUTOSECURE_LICENSE_GATE=%LICENSE_GATE%"

rem --- working dir ---------------------------------------------------------
cd /d "%ROOT%"

rem --- Python detection -----------------------------------------------------
set "PY_CMD="
py -3 --version >nul 2>&1 && set "PY_CMD=py -3"
if not defined PY_CMD (
    python --version >nul 2>&1 && set "PY_CMD=python"
)

rem --- venv ------------------------------------------------------------------
if exist "%VENV_PY%" goto :have_venv

echo.
echo [1/5] Creating the virtual environment...
if not defined PY_CMD (
    echo [X] Python was not found. Install Python 3.11+ from python.org and tick
    echo     "Add python.exe to PATH" on the installer, then run start.bat again.
    pause
    exit /b 1
)
%PY_CMD% -m venv "%ROOT%.venv" 2>&1
if not exist "%VENV_PY%" (
    echo [X] Could not create .venv with "%PY_CMD%".
    pause
    exit /b 1
)

:have_venv
echo [1/5] Virtual environment ready.

rem --- pip upgrade + requirements ------------------------------------------
echo [2/5] Installing prerequisites...
"%VENV_PY%" -m pip install --upgrade pip --quiet --disable-pip-version-check 2>&1
"%VENV_PY%" -m pip install -r "%ROOT%requirements.txt" --quiet --disable-pip-version-check 2>&1
if errorlevel 1 (
    echo.
    echo [X] Prerequisite install failed. Run this to see why:
    echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)
echo [2/5] Prerequisites installed.

rem --- config.json check ----------------------------------------------------
echo [3/5] Checking config\config.json...
if not exist "%ROOT%config\config.json" (
    if exist "%ROOT%config\config.json.example" (
        copy /y "%ROOT%config\config.json.example" "%ROOT%config\config.json" >nul
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
    ) else (
        echo [X] config\config.json is missing and there is no config\config.json.example
        echo     to copy. Restore that file from the repository and run start.bat again.
        pause
        exit /b 1
    )
    pause
    exit /b 0
)
echo [3/5] config\config.json found.

rem --- license gate ----------------------------------------------------------
echo [4/5] Checking license...
echo       Activation state is stored in %LOCALAPPDATA%\Autosecure\License
echo       (outside this project folder).  The original license key is never
echo       stored on disk.
echo.

set "LICENSE_CHECK_PY=%ROOT%license_gate.py"
set "LICENSE_PKG_DIR=%ROOT%license"

if not exist "%LICENSE_CHECK_PY%" (
    echo [X] license_gate.py is missing — this build is incomplete.
    pause
    exit /b 1
)

rem Run the gate check.  If it exits 0, the verdict printed is the bot may start.
rem If it exits non-zero, the bot must not connect.
"%VENV_PY%" "%LICENSE_CHECK_PY%" check --key "" <nul 2>&1 | "%VENV_PY%" -c "import sys,json; d=json.load(sys.stdin); print(d.get('detail',''))" >nul 2>&1
set "LICENSE_HAS_STATE=0"
"%VENV_PY%" "%LICENSE_CHECK_PY%" status >nul 2>&1
if errorlevel 1 (
    set "LICENSE_HAS_STATE=0"
) else (
    set "LICENSE_HAS_STATE=1"
)

if "%LICENSE_HAS_STATE%"=="1" (
    echo       Activation found — validating...
    "%VENV_PY%" "%LICENSE_CHECK_PY%" check --key "" <nul 2>nul
    set "LICENSE_RC=%ERRORLEVEL%"
    if not "%LICENSE_RC%"=="0" (
        echo.
        echo [X] License validation failed. The bot will not start.
        echo     Run:  license_gate.py status
        echo     for details, or contact the developer for a new license.
        pause
        exit /b 2
    )
    echo       License valid.
    goto :start_bot
)

rem --- first run: prompt for key --------------------------------------------
echo.
echo ============================================================
echo  FIRST RUN — license activation
echo ============================================================
echo.
echo  This copy of sunnysecure has not been activated yet.
echo.
echo  You need a license key issued by the developer.
echo  Enter the key below when prompted (it is shown once, then discarded).
echo.

:license_prompt
set "LICENSE_KEY="
set /p "LICENSE_KEY=Enter license key: "
if not defined LICENSE_KEY (
    echo [X] No key entered. The bot will not start.
    pause
    exit /b 1
)

rem Basic format check before spending time on activation.
"%VENV_PY%" "%LICENSE_CHECK_PY%" check --key "%LICENSE_KEY%" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [X] That key was rejected. Check the key and try again.
    echo     (사립키 생성기는 구매자에게 배포되지 않습니다 — 개발자에게 문의하세요.)
    goto :license_prompt
)

rem One-time activation.
echo.
echo       Activating...
"%VENV_PY%" "%LICENSE_CHECK_PY%" activate --key "%LICENSE_KEY%" >nul 2>&1
set "ACTIVATE_RC=%ERRORLEVEL%"
if not "%ACTIVATE_RC%"=="0" (
    echo.
    echo [X] Activation failed.
    echo     Run:  license_gate.py status
    echo     for details, then contact the developer.
    pause
    exit /b 2
)

echo       Activation complete.
echo       License expires after %LICENSE_TTL_DAYS% days.
echo.

rem --- start the bot --------------------------------------------------------
:start_bot
echo [5/5] Starting the bot...
echo       Close this window or press Ctrl+C to stop it.
echo.

"%VENV_PY%" -u "%ROOT%bot.py"
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo.
    echo [X] The bot exited with code %RC% — the error above says why.
    pause
)
exit /b %RC%
