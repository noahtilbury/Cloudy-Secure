@echo off
setlocal EnableExtensions DisableDelayedExpansion
title Cloudy Secure - setup

rem ---------------------------------------------------------------------------
rem One-time (and repeatable) environment setup for Cloudy Secure.
rem
rem Flow:
rem   1. Find a Python 3.11+ interpreter and report its version.
rem   2. Create the local .venv when it does not exist yet.
rem   3. Install/upgrade the dependencies from requirements.txt.
rem   4. Validate config\config.json and config\bot.json (creating them from
rem      the .example files when they are missing) and say exactly what still
rem      has to be filled in.
rem
rem Every path is relative to this file, so the project folder can live
rem anywhere on the machine and can be copied to another PC.
rem
rem NOTE: this script deliberately uses goto-based control flow only.
rem Parenthesised if (...) blocks are avoided because they break when an
rem echo line contains parentheses, and because %ERRORLEVEL% inside a block
rem is expanded at parse time rather than at runtime.
rem ---------------------------------------------------------------------------

chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "ROOT=%~dp0"
set "VENV_DIR=%ROOT%.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

rem --- arguments -------------------------------------------------------------
set "PAUSE_AT_END=1"
if /i "%~1"=="/nopause" set "PAUSE_AT_END=0"
if /i "%~1"=="--no-pause" set "PAUSE_AT_END=0"

rem --- working dir -----------------------------------------------------------
cd /d "%ROOT%"

echo ============================================================
echo  Cloudy Secure - setup
echo ============================================================
echo.

rem --- 1. python ------------------------------------------------------------
echo [1/4] Looking for Python 3.11 or newer...

set "PY_CMD="
set "PY_VERSION="
call :try_python "py -3.13"
if defined PY_CMD goto :py_found
call :try_python "py -3.12"
if defined PY_CMD goto :py_found
call :try_python "py -3.11"
if defined PY_CMD goto :py_found
call :try_python "py -3"
if defined PY_CMD goto :py_found
call :try_python "python"
if defined PY_CMD goto :py_found
call :try_python "python3"
if defined PY_CMD goto :py_found

echo.
echo [X] Python 3.11 or newer was not found on this machine.
call :show_versions
echo.
echo     Install Python from https://www.python.org/downloads/
echo     and tick "Add python.exe to PATH" in the installer,
echo     then run this script again.
call :finish 1
goto :eof

:py_found
echo       using %PY_CMD% (%PY_VERSION%)
echo.

rem --- 2. virtual environment ------------------------------------------------
echo [2/4] Preparing the virtual environment (.venv)...
if exist "%VENV_PY%" goto :have_venv

%PY_CMD% -m venv "%VENV_DIR%" >nul 2>&1
if exist "%VENV_PY%" goto :have_venv

echo.
echo [X] Could not create the virtual environment in "%VENV_DIR%".
echo     Check that you can write to the project folder, then run this again.
call :finish 1
goto :eof

:have_venv
echo       .venv ready.
echo.

rem --- 3. dependencies -------------------------------------------------------
echo [3/4] Installing the dependencies from requirements.txt...
"%VENV_PY%" -m pip install --upgrade pip --quiet --disable-pip-version-check >nul 2>&1
"%VENV_PY%" -m pip install -r "%ROOT%requirements.txt" --quiet --disable-pip-version-check
if errorlevel 1 goto :pip_fail
echo       dependencies installed.
echo.

rem --- 4. configuration ------------------------------------------------------
echo [4/4] Checking the configuration...
"%VENV_PY%" "%ROOT%setup_check.py"
if errorlevel 2 goto :config_created
if errorlevel 1 goto :config_invalid
echo.
echo ============================================================
echo  Setup finished - the bot is ready to run.
echo  Start it with start.bat
echo ============================================================
call :finish 0
goto :eof

:pip_fail
echo.
echo [X] Installing the dependencies failed. Run this to see why:
echo     "%VENV_PY%" -m pip install -r "%ROOT%requirements.txt"
echo.
echo     The most common cause is no internet connection.
call :finish 1
goto :eof

:config_invalid
echo.
echo [X] The configuration is not usable yet - see the messages above.
call :finish 1
goto :eof

:config_created
echo.
echo [X] The config files were created from the examples.
echo     Fill in your bot token and your Discord user ID, then run start.bat.
call :finish 2
goto :eof

rem ---------------------------------------------------------------------------
rem :try_python <command>
rem   Sets PY_CMD / PY_VERSION when <command> is a Python 3.11+ interpreter.
rem ---------------------------------------------------------------------------
:try_python
set "PY_CAND_VERSION="
for /f "delims=" %%V in ('%~1 --version 2^>^&1') do set "PY_CAND_VERSION=%%V"
%~1 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 exit /b 1
set "PY_CMD=%~1"
if not defined PY_VERSION set "PY_VERSION=%PY_CAND_VERSION%"
exit /b 0

rem ---------------------------------------------------------------------------
rem :show_versions - list the Python interpreters that are installed.
rem ---------------------------------------------------------------------------
:show_versions
call :show_one "py"
call :show_one "python"
call :show_one "python3"
exit /b 0

:show_one
for /f "delims=" %%V in ('%~1 --version 2^>^&1') do echo       %~1 -> %%V
exit /b 0

rem ---------------------------------------------------------------------------
rem :finish <exit code> - pause (unless /nopause) and exit with the code.
rem ---------------------------------------------------------------------------
:finish
if "%PAUSE_AT_END%"=="1" pause
exit /b %~1
