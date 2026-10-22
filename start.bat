@echo off
setlocal EnableExtensions DisableDelayedExpansion
title Cloudy Secure - launcher

rem ---------------------------------------------------------------------------
rem Double-click launcher for Cloudy Secure.
rem
rem Flow:
rem   1. setup.bat - finds a Python 3.11+ interpreter, creates .venv, installs
rem      requirements.txt and validates the config files.  It prints exactly
rem      what is missing or wrong and stops with a non-zero exit code.
rem   2. The bot itself is started with the virtual environment's Python.
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
set "VENV_PY=%ROOT%.venv\Scripts\python.exe"

rem --- working dir -----------------------------------------------------------
cd /d "%ROOT%"

rem --- setup -----------------------------------------------------------------
call "%ROOT%setup.bat" /nopause
if errorlevel 2 goto :need_config
if errorlevel 1 goto :setup_failed

title Cloudy Secure - launcher

rem --- start the bot ---------------------------------------------------------
echo.
echo ============================================================
echo  Starting the bot...
echo  Close this window or press Ctrl+C to stop it.
echo ============================================================
echo.

"%VENV_PY%" -u "%ROOT%bot.py"
set "RC=%ERRORLEVEL%"

if "%RC%"=="0" exit /b 0
echo.
echo [X] The bot exited with code %RC% - the error above says why.
pause
exit /b %RC%

rem ---------------------------------------------------------------------------
rem Failure paths
rem ---------------------------------------------------------------------------
:need_config
echo.
echo [X] The bot cannot start until config\config.json is filled in.
echo     The steps above list what is missing.
pause
exit /b 2

:setup_failed
echo.
echo [X] Setup did not finish - the messages above say why.
echo     Fix the problem and run start.bat again.
pause
exit /b 1
