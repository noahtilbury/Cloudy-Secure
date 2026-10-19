@echo off
setlocal EnableExtensions
title sunnysecure - start

rem Double-click launcher. It does not use PowerShell on purpose: Windows ships
rem with the execution policy set to Restricted, which makes start.ps1 fail with
rem "running scripts is disabled on this system".

set "ROOT=%~dp0"
cd /d "%ROOT%"

set "VENV_PY=%ROOT%.venv\Scripts\python.exe"

if exist "%VENV_PY%" goto have_venv

echo [1/3] Creating the virtual environment...
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
echo [1/3] Virtual environment found.

:deps
echo [2/3] Installing requirements...
"%VENV_PY%" -m pip install --upgrade pip --quiet --disable-pip-version-check
"%VENV_PY%" -m pip install -r "%ROOT%requirements.txt" --quiet --disable-pip-version-check
if errorlevel 1 (
    echo [X] Dependency install failed. Run this to see why:
    echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

echo [3/3] Starting the bot...
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
