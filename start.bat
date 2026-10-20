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
rem
rem NOTE: this script deliberately uses goto-based control flow only.
rem Parenthesised if (...) blocks are avoided because they break when an
rem echo line contains parentheses, and because %ERRORLEVEL% inside a block
rem is expanded at parse time rather than at runtime.
rem ---------------------------------------------------------------------------

chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

rem --- config ---------------------------------------------------------------
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

rem --- working dir ----------------------------------------------------------
cd /d "%ROOT%"

rem --- Python detection -----------------------------------------------------
set "PY_CMD="
py -3 --version >nul 2>&1
if not errorlevel 1 set "PY_CMD=py -3"
if defined PY_CMD goto :py_found
python --version >nul 2>&1
if not errorlevel 1 set "PY_CMD=python"
:py_found

rem --- venv -----------------------------------------------------------------
if exist "%VENV_PY%" goto :have_venv

echo.
echo [1/5] Creating the virtual environment...
if not defined PY_CMD goto :no_python
%PY_CMD% -m venv "%ROOT%.venv" 2>&1
if exist "%VENV_PY%" goto :have_venv
echo [X] Could not create .venv with "%PY_CMD%".
pause
exit /b 1

:no_python
echo [X] Python was not found. Install Python 3.11+ from python.org and tick
echo     "Add python.exe to PATH" on the installer, then run start.bat again.
pause
exit /b 1

:have_venv
echo [1/5] Virtual environment ready.

rem --- pip upgrade + requirements -------------------------------------------
echo [2/5] Installing prerequisites...
"%VENV_PY%" -m pip install --upgrade pip --quiet --disable-pip-version-check 2>&1
"%VENV_PY%" -m pip install -r "%ROOT%requirements.txt" --quiet --disable-pip-version-check 2>&1
if errorlevel 1 goto :pip_fail
echo [2/5] Prerequisites installed.
goto :config_check

:pip_fail
echo.
echo [X] Prerequisite install failed. Run this to see why:
echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
pause
exit /b 1

rem --- config.json check ----------------------------------------------------
:config_check
echo [3/5] Checking config\config.json...
if exist "%ROOT%config\config.json" goto :config_ok
if exist "%ROOT%config\config.json.example" goto :copy_example
echo [X] config\config.json is missing and there is no config\config.json.example
echo     to copy. Restore that file from the repository and run start.bat again.
pause
exit /b 1

:copy_example
copy /y "%ROOT%config\config.json.example" "%ROOT%config\config.json" >nul
echo.
echo ------------------------------------------------------------
echo  config\config.json was created from the example.
echo.
echo  Open it and fill in:
echo    tokens.bot_token    your bot token from
echo                        https://discord.com/developers/applications
echo    owners              your Discord user ID.  Enable Developer Mode in
echo                        Discord settings, right-click yourself, and
echo                        choose Copy User ID.
echo    discord.*_channel   the channel IDs the bot posts in
echo.
echo  Save the file, then run start.bat again.
echo  config\bot.json commands, embeds, and presence already work as shipped.
echo ------------------------------------------------------------
pause
exit /b 0

:config_ok
echo [3/5] config\config.json found.

rem --- license gate ---------------------------------------------------------
echo [4/5] Checking license...
echo       Activation state is stored in %LOCALAPPDATA%\Autosecure\License
echo       outside this project folder.  The original license key is never
echo       stored on disk.
echo.

set "LICENSE_CHECK_PY=%ROOT%license_gate.py"

if not exist "%LICENSE_CHECK_PY%" goto :missing_gate

rem Detect whether activation state already exists on this machine.
"%VENV_PY%" "%LICENSE_CHECK_PY%" status >nul 2>&1
if errorlevel 1 goto :first_run

rem Activation state exists: validate it before starting the bot.
rem ``<nul`` guarantees the check never blocks waiting on stdin.
echo       Activation found - validating...
"%VENV_PY%" "%LICENSE_CHECK_PY%" check --key "" <nul >nul 2>&1
if errorlevel 1 goto :license_fail
echo       License valid.
goto :start_bot

:license_fail
echo.
echo [X] License validation failed. The bot will not start.
echo     Run:  license_gate.py status
echo     for details, or contact the developer for a new license.
pause
exit /b 2

:missing_gate
echo [X] license_gate.py is missing - this build is incomplete.
pause
exit /b 1

rem --- first run: prompt for key --------------------------------------------
:first_run
echo.
echo ============================================================
echo  FIRST RUN - license activation
echo ============================================================
echo.
echo  This copy of sunnysecure has not been activated yet.
echo.
echo  You need a license key issued by the developer.
echo  Enter the key below when prompted.  It is shown once, then discarded.
echo.

:license_prompt
set "LICENSE_KEY="
set /p "LICENSE_KEY=Enter license key: "
if not defined LICENSE_KEY goto :no_key

rem Basic format check before spending time on activation.
"%VENV_PY%" "%LICENSE_CHECK_PY%" check --key "%LICENSE_KEY%" <nul >nul 2>&1
if errorlevel 1 goto :bad_key

rem One-time activation.
echo.
echo       Activating...
"%VENV_PY%" "%LICENSE_CHECK_PY%" activate --key "%LICENSE_KEY%" <nul >nul 2>&1
if errorlevel 1 goto :activate_fail

echo       Activation complete.
echo       License expires after %LICENSE_TTL_DAYS% days.
echo.
goto :start_bot

:no_key
echo [X] No key entered. The bot will not start.
pause
exit /b 1

:bad_key
echo.
echo [X] That key was rejected. Check the key and try again.
echo     Contact the developer if you think this is an error.
goto :license_prompt

:activate_fail
echo.
echo [X] Activation failed.
echo     Run:  license_gate.py status
echo     for details, then contact the developer.
pause
exit /b 2

rem --- start the bot --------------------------------------------------------
:start_bot
echo [5/5] Starting the bot...
echo       Close this window or press Ctrl+C to stop it.
echo.

"%VENV_PY%" -u "%ROOT%bot.py"
set "RC=%ERRORLEVEL%"

if "%RC%"=="0" exit /b 0
echo.
echo [X] The bot exited with code %RC% - the error above says why.
pause
exit /b %RC%
