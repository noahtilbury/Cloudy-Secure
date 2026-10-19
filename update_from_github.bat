@echo off
setlocal EnableExtensions DisableDelayedExpansion

title sunnysecure - update from GitHub

:: The repository this project is published from. Every clone/remote hint below
:: uses it, so moving the project only needs changing these two lines.
set "REPO_URL=https://github.com/noahtilbury/Discord-Minecraft-AutoSecure.git"
set "REPO_SHORT=noahtilbury/Discord-Minecraft-AutoSecure"
set "REPO_OWNER=noahtilbury"

:: SHIFT also overwrites %0, so capture the script folder before parsing args.
for %%I in ("%~dp0.") do set "REPO_ROOT=%%~fI"

set "EXITCODE=0"
set "UNATTENDED=0"
set "CHECKONLY=0"
set "ZIP_MODE=0"

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

echo [1/6] Checking the repository...
if not exist ".git" goto zip_mode

if not defined GIT (
    echo [ERROR] This is a git checkout but Git is not installed or on PATH.
    echo         Install it from https://git-scm.com/download/win and run this again.
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
    echo             git remote add origin %REPO_URL%
    set "EXITCODE=2"
    goto end
)

echo       git:     %GIT%
echo       branch:  %BRANCH%
echo       origin:  %REMOTE_URL%

:: ---------------------------------------------------------------------------
:: Wrong repository guard: the code used to be published at waitno01/sunnysecure.
:: A copy still pointing there would download someone else's repository instead of
:: this project, so offer to repoint it before fetching.
:: ---------------------------------------------------------------------------
echo %REMOTE_URL% | findstr /I /C:"waitno01/sunnysecure" >nul
if errorlevel 1 goto origin_ok
echo.
echo [WARN] This folder tracks waitno01/sunnysecure, not where this project is
echo        published. New versions come from %REPO_SHORT%.
if "%UNATTENDED%"=="1" (
    echo        Fix it once, then run this file again:
    echo            git remote set-url origin %REPO_URL%
    goto origin_ok
)
choice /c YN /n /m "Switch origin to %REPO_SHORT% now? [Y/N] "
if errorlevel 2 goto origin_ok
%GIT% remote set-url origin %REPO_URL%
if errorlevel 1 (
    echo [WARN] Could not change the remote. Run this by hand:
    echo            git remote set-url origin %REPO_URL%
    goto origin_ok
)
set "REMOTE_URL=%REPO_URL%"
echo        [OK] origin is now %REPO_SHORT%.
:origin_ok

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
    echo [UP TO DATE] You already have the newest version.
    echo           No updates needed - your copy is current.
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

:: ZIP installs jump here after copying the downloaded archive.
:after_pull

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
if exist "%REPO_ROOT%\update_from_github.bat.new" (
    echo  A newer updater was downloaded as update_from_github.bat.new.
    echo  After closing this window, swap it in with:
    echo      move /y update_from_github.bat.new update_from_github.bat
    echo ------------------------------------------------------------
)
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
echo      start.bat
echo  or  .venv\Scripts\python.exe bot.py
:end_summary
echo ============================================================
goto end

:: ---------------------------------------------------------------------------
:: ZIP install (no .git) - download the latest source archive and copy it over
:: ---------------------------------------------------------------------------
:zip_mode
set "ZIP_MODE=1"
set "REMOTE_COMMIT=latest main (ZIP install)"

echo       no .git folder - this copy was extracted from a ZIP, not cloned.
echo       The latest source archive will be downloaded from %REPO_SHORT%
echo       and copied over this folder instead.
echo.
echo [2/6] Checking for the latest version on GitHub...

if "%CHECKONLY%"=="1" (
    echo.
    echo [CHECK ONLY] A ZIP install cannot be compared commit-by-commit.
    echo              Run this file without /check to download the latest main.
    set "EXITCODE=10"
    goto end
)

if "%UNATTENDED%"=="0" (
    echo.
    echo Ready to download the latest version and copy it over this folder.
    echo config\config.json, the database, logs\ and .venv\ are NOT touched.
    choice /c YN /n /m "Continue? [Y/N] "
    if errorlevel 2 (
        echo Update cancelled - nothing was changed.
        set "EXITCODE=0"
        goto end
    )
)

set "ZIP_FILE=%TEMP%\autosecure-update-%RANDOM%%RANDOM%.zip"
set "ZIP_DIR=%TEMP%\autosecure-update-%RANDOM%%RANDOM%"
set "ZIP_URL=https://codeload.github.com/%REPO_SHORT%/zip/refs/heads/main"

echo.
echo [3/6] Downloading the source archive...
where curl >nul 2>&1
if errorlevel 1 goto zip_download_ps
curl -L -s -S -o "%ZIP_FILE%" "%ZIP_URL%"
goto zip_download_done

:zip_download_ps
powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -UseBasicParsing -Uri '%ZIP_URL%' -OutFile '%ZIP_FILE%'"

:zip_download_done
if not exist "%ZIP_FILE%" goto zip_failed
for %%F in ("%ZIP_FILE%") do if %%~zF LSS 1000 goto zip_failed
echo       [OK] archive downloaded.

echo [4/6] Extracting and copying the new files...
mkdir "%ZIP_DIR%" >nul 2>&1
rem Use the Windows tar (bsdtar, zip-capable). A GNU tar earlier on PATH cannot
rem read ZIP at all, so it is never used here.
set "BSD_TAR=%SystemRoot%\System32\tar.exe"
if not exist "%BSD_TAR%" goto zip_extract_ps
"%BSD_TAR%" -xf "%ZIP_FILE%" -C "%ZIP_DIR%"
if errorlevel 1 goto zip_extract_ps
goto zip_extract_done

:zip_extract_ps
powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath '%ZIP_FILE%' -DestinationPath '%ZIP_DIR%' -Force"

:zip_extract_done
set "SRC="
for /d %%D in ("%ZIP_DIR%\*") do (
    set "SRC=%%~fD"
    goto zip_src_found
)
:zip_src_found
if not defined SRC goto zip_failed
if not exist "%SRC%\bot.py" goto zip_failed

rem Do NOT redownload update_from_github.bat - keep the current version so it can
rem apply future updates itself. Only source files move.
robocopy "%SRC%" "%REPO_ROOT%" /E /XD .git logs .venv node_modules __pycache__ /XF config.json refresh_posts_state.json *.db update_from_github.bat /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto zip_copy_failed
echo       [OK] files updated.
echo       [OK] config\config.json, database, logs and .venv were kept.

call :zip_cleanup
echo.
echo [OK] Updated to the latest main.
goto after_pull

:zip_failed
call :zip_cleanup
echo.
echo [ERROR] Could not download or extract the latest version.
echo         Common reasons:
echo           - no internet connection, or a proxy blocking github.com
echo           - the download was interrupted
echo         Check your network, then run this file again.
set "EXITCODE=7"
goto end

:zip_copy_failed
call :zip_cleanup
echo.
echo [WARN] Some files could not be copied - they are probably open.
echo        Close the bot window (and any editor holding these files), then
echo        run this file again.
set "EXITCODE=7"
goto end

:zip_cleanup
rd /s /q "%ZIP_DIR%" >nul 2>&1
del /q "%ZIP_FILE%" >nul 2>&1
goto :eof

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
echo A folder without .git (a ZIP download) is updated by downloading the latest
echo source archive and copying it over the folder. config\config.json, the
echo database, logs\ and .venv\ are kept either way.
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
