@echo off
setlocal EnableExtensions DisableDelayedExpansion

title sunnysecure - update from GitHub

:: SHIFT also overwrites %0, so capture the script folder before parsing args.
for %%I in ("%~dp0.") do set "REPO_ROOT=%%~fI"

set "EXITCODE=0"
set "UNATTENDED=0"
set "CHECKONLY=0"

:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="/y" set "UNATTENDED=1"
if /I "%~1"=="-y" set "UNATTENDED=1"
if /I "%~1"=="--yes" set "UNATTENDED=1"
if /I "%~1"=="/check" set "CHECKONLY=1"
if /I "%~1"=="--check" set "CHECKONLY=1"
if /I "%~1"=="/h" goto usage
if /I "%~1"=="--help" goto usage
if /I "%~1"=="/?" goto usage
shift
goto parse_args
:args_done

echo ============================================================
echo  sunnysecure - GitHub updater
echo  folder: %REPO_ROOT%
echo ============================================================
echo.

cd /d "%REPO_ROOT%" 2>nul
if errorlevel 1 (
    echo [ERROR] Could not open the project folder:
    echo         %REPO_ROOT%
    set "EXITCODE=1"
    goto end
)

:: ---------------------------------------------------------------------------
:: Locate git (PATH first, then the usual Windows install locations)
:: ---------------------------------------------------------------------------
set "GIT=git"
where git >nul 2>&1
if not errorlevel 1 goto git_ready
set "GIT="
if exist "%ProgramFiles%\Git\cmd\git.exe" set GIT="%ProgramFiles%\Git\cmd\git.exe"
if not defined GIT if exist "%ProgramFiles(x86)%\Git\cmd\git.exe" set GIT="%ProgramFiles(x86)%\Git\cmd\git.exe"
if not defined GIT if exist "%LocalAppData%\Programs\Git\cmd\git.exe" set GIT="%LocalAppData%\Programs\Git\cmd\git.exe"
:git_ready

if not defined GIT (
    echo [ERROR] Git is not installed or not on PATH.
    echo         Install it from https://git-scm.com/download/win and run this again.
    set "EXITCODE=2"
    goto end
)

echo [1/6] Checking the repository...
if not exist ".git" (
    echo [ERROR] This folder is not a git checkout.
    echo         Download the project with:
    echo             git clone https://github.com/waitno01/sunnysecure.git
    echo         and run update_from_github.bat from inside that folder.
    set "EXITCODE=2"
    goto end
)

set "BRANCH="
for /f "delims=" %%B in ('%GIT% rev-parse --abbrev-ref HEAD 2^>nul') do set "BRANCH=%%B"
if not defined BRANCH set "BRANCH=main"
if /I "%BRANCH%"=="HEAD" set "BRANCH=main"

set "REMOTE_URL="
for /f "delims=" %%U in ('%GIT% config --get remote.origin.url 2^>nul') do set "REMOTE_URL=%%U"
if not defined REMOTE_URL (
    echo [ERROR] No git remote named "origin" is configured.
    echo         Fix it with:
    echo             git remote add origin https://github.com/waitno01/sunnysecure.git
    set "EXITCODE=2"
    goto end
)

echo       git:     %GIT%
echo       branch:  %BRANCH%
echo       origin:  %REMOTE_URL%

:: ---------------------------------------------------------------------------
:: Fetch
:: ---------------------------------------------------------------------------
echo.
echo [2/6] Fetching the latest version from GitHub...
%GIT% fetch origin --prune --tags
if errorlevel 1 (
    echo.
    echo [ERROR] Could not fetch from GitHub.
    echo         Common reasons:
    echo           - no internet connection / proxy blocking GitHub
    echo           - the repository was renamed, deleted or made private
    echo           - HTTPS credentials are needed but none are stored
    echo         Check your network, then run this file again.
    set "EXITCODE=3"
    goto end
)

set "UPSTREAM=origin/%BRANCH%"
%GIT% rev-parse --verify --quiet "%UPSTREAM%" >nul 2>&1
if errorlevel 1 set "UPSTREAM=origin/main"
%GIT% rev-parse --verify --quiet "%UPSTREAM%" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Neither origin/%BRANCH% nor origin/main exists on the remote.
    set "EXITCODE=3"
    goto end
)

set "LOCAL_COMMIT="
for /f "delims=" %%H in ('%GIT% rev-parse HEAD 2^>nul') do set "LOCAL_COMMIT=%%H"
set "REMOTE_COMMIT="
for /f "delims=" %%H in ('%GIT% rev-parse "%UPSTREAM%" 2^>nul') do set "REMOTE_COMMIT=%%H"

echo.
echo [3/6] Comparing your copy with GitHub...
echo       local:   %LOCAL_COMMIT%
echo       remote:  %REMOTE_COMMIT%

if /I "%LOCAL_COMMIT%"=="%REMOTE_COMMIT%" (
    echo.
    echo [UP TO DATE] You already have the newest version. Nothing was changed.
    goto report_version
)

set "BEHIND="
for /f "delims=" %%N in ('%GIT% rev-list --count HEAD..%UPSTREAM% 2^>nul') do set "BEHIND=%%N"
if not defined BEHIND set "BEHIND=0"

if "%BEHIND%"=="0" (
    echo.
    echo [AHEAD] Your copy has local commits that are not on GitHub.
    echo         Nothing to download - push your work instead of updating.
    goto report_version
)

echo.
echo       %BEHIND% new commit^(s^) available:
echo ------------------------------------------------------------
%GIT% --no-pager log --oneline --no-decorate HEAD..%UPSTREAM%
echo ------------------------------------------------------------

if "%CHECKONLY%"=="1" (
    echo.
    echo [CHECK ONLY] An update is available. Run this file without /check to install it.
    set "EXITCODE=10"
    goto end
)

if "%UNATTENDED%"=="0" (
    echo.
    echo Ready to update to the version above.
    echo Your config files in config\ and your database are NOT touched.
    choice /c YN /n /m "Continue? [Y/N] "
    if errorlevel 2 (
        echo Update cancelled - nothing was changed.
        set "EXITCODE=0"
        goto end
    )
)

:: ---------------------------------------------------------------------------
:: Pull
:: ---------------------------------------------------------------------------
echo.
echo [4/6] Applying the update...
%GIT% pull --ff-only origin "%BRANCH%"
if errorlevel 1 goto pull_failed

echo [OK] Updated to %REMOTE_COMMIT%.

:: ---------------------------------------------------------------------------
:: Python environment, dependencies, database
:: ---------------------------------------------------------------------------
echo.
echo [5/6] Checking the Python environment...
if exist ".venv\Scripts\python.exe" goto have_venv

echo       No .venv found - creating one...
python -m venv ".venv" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Could not create the virtual environment.
    echo         Install Python 3.11+ from https://www.python.org/downloads/
    echo         and make sure "Add python.exe to PATH" is ticked.
    set "EXITCODE=4"
    goto end
)
echo       [OK] .venv created.
:have_venv

if not exist "requirements.txt" goto skip_deps
echo       Installing requirements...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>&1
".venv\Scripts\python.exe" -m pip install -r "requirements.txt"
if errorlevel 1 (
    echo [WARN] Some dependencies failed to install.
    echo        Re-run this updater, or run:
    echo            .venv\Scripts\python.exe -m pip install -r requirements.txt
) else (
    echo       [OK] Dependencies installed.
)
:skip_deps

echo       Updating the database schema...
".venv\Scripts\python.exe" -c "from database.database import DBConnection as D; D().setup_tables()"
if errorlevel 1 (
    echo [WARN] Database setup reported a problem.
    echo        The bot will retry it on the next start.
) else (
    echo       [OK] Database ready.
)

:: ---------------------------------------------------------------------------
:: First-run config bootstrap (never overwrites existing config)
:: ---------------------------------------------------------------------------
echo.
echo [6/6] Checking your configuration...
if exist "config\config.json" goto have_config
if not exist "config\config.json.example" goto missing_config
copy /y "config\config.json.example" "config\config.json" >nul
echo       [ACTION] Created config\config.json from the example.
echo                Open it and add your bot token, owners and channel IDs
echo                before starting the bot.
goto check_bot_json
:missing_config
echo       [ACTION] config\config.json is missing and there is no example to copy.
echo                Create it with your bot token before starting the bot.
:have_config

:check_bot_json
if exist "config\bot.json" goto have_bot_json
if not exist "config\bot.json.example" goto have_bot_json
copy /y "config\bot.json.example" "config\bot.json" >nul
echo       [ACTION] Created config\bot.json from the example.
:have_bot_json

:report_version
echo.
echo ============================================================
echo  Done. Your copy is at:
echo  %REMOTE_COMMIT%
echo ------------------------------------------------------------
set "RUNNING_PID="
if exist "logs\bot.lock" set /p RUNNING_PID=<"logs\bot.lock"
if not defined RUNNING_PID goto no_bot_running
tasklist /FI "PID eq %RUNNING_PID%" /NH 2>nul | findstr /I "python" >nul
if errorlevel 1 goto no_bot_running
echo  The bot is still running as PID %RUNNING_PID%.
echo  Close that window (or stop pm2) and start the bot again so
echo  the new code is loaded.
goto end_summary
:no_bot_running
echo  Start the bot with:
echo      start.ps1
echo  or  .venv\Scripts\python.exe bot.py
:end_summary
echo ============================================================
goto end

:: ---------------------------------------------------------------------------
:: Failure paths
:: ---------------------------------------------------------------------------
:pull_failed
echo.
echo [ERROR] The update could not be applied cleanly.
echo.
echo         This usually means you edited files that the update also changes.
echo         Options:
echo           1. Keep your changes: commit them, then run this again.
echo                git add -A
echo                git commit -m "my local changes"
echo           2. Discard the local code changes and take GitHub's version:
echo                git reset --hard %UPSTREAM%
echo.
echo         Your config\ files and database are untracked, so both options
echo         keep them as they are.
if "%UNATTENDED%"=="1" (
    set "EXITCODE=5"
    goto end
)
set "ANSWER="
set /p "ANSWER=Type RESET to discard local code changes and force the update, or press Enter to stop: "
if /I not "%ANSWER%"=="RESET" (
    echo Nothing was changed.
    set "EXITCODE=0"
    goto end
)
echo Forcing %UPSTREAM%...
%GIT% reset --hard "%UPSTREAM%"
if errorlevel 1 (
    echo [ERROR] git reset failed. Run the command above manually.
    set "EXITCODE=5"
    goto end
)
echo [OK] Forced to %REMOTE_COMMIT%.
echo Re-running the post-update steps is not automatic - start this file again
echo to install dependencies and migrate the database.
set "EXITCODE=0"
goto end

:usage
echo Usage: update_from_github.bat [/check] [/y]
echo.
echo   /check   Only check whether an update exists.
echo            Exit code 10 = update available, 0 = up to date.
echo   /y       Unattended: no confirmation prompt and no final pause.
echo.
echo Run it with no arguments for the normal interactive updater.
set "EXITCODE=0"
goto end

:: ---------------------------------------------------------------------------
:: End - the window always stays open unless /y was passed
:: ---------------------------------------------------------------------------
:end
echo.
if "%UNATTENDED%"=="0" (
    echo Press any key to close this window...
    pause >nul
)
endlocal & exit /b %EXITCODE%
