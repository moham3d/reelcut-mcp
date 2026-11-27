@echo off
setlocal EnableExtensions
chcp 65001 >nul
title ReelCut - install
color 0E

rem ============================================================
rem  ReelCut installer (Windows)
rem  1. finds Python 3.11+
rem  2. creates .venv and installs the tool
rem  3. runs the guided first setup (Gemini key, Google sign-in,
rem     credit balance, Flow project)
rem
rem  Every failure prints a plain-language next step, not a
rem  traceback, so a non-technical creator can finish alone.
rem ============================================================

cd /d "%~dp0"
echo.
echo ============================================================
echo   ReelCut - install
echo   folder: %CD%
echo ============================================================
echo.

set "PY="
py -3.12 -c "import sys" >nul 2>&1 && set "PY=py -3.12"
if not defined PY ( py -3.11 -c "import sys" >nul 2>&1 && set "PY=py -3.11" )
if not defined PY ( py -3 -c "import sys;raise SystemExit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1 && set "PY=py -3" )
if not defined PY ( python -c "import sys;raise SystemExit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1 && set "PY=python" )
if not defined PY (
    echo [x] Python 3.11+ not found.
    echo     Install it from https://www.python.org/downloads/windows/
    echo     Tick "Add python.exe to PATH", then run install.bat again.
    goto :fail
)
for /f "delims=" %%v in ('%PY% -c "import sys;print(sys.version.split()[0])"') do set "PYVER=%%v"
echo [1/4] Python %PYVER%

set "VPY=%CD%\.venv\Scripts\python.exe"
if exist "%VPY%" (
    echo [2/4] virtual env: already present
) else (
    echo [2/4] creating .venv ...
    %PY% -m venv ".venv"
    if not exist "%VPY%" (
        echo [x] Could not create .venv. Check disk space and permissions.
        goto :fail
    )
)

set "PYTHONIOENCODING=utf-8"
echo [3/4] installing dependencies (first run downloads a few hundred MB) ...
"%VPY%" -m pip install --upgrade pip --quiet
"%VPY%" -m pip install -e "."
if errorlevel 1 (
    echo [x] Dependency install failed. Check the internet connection,
    echo     then run install.bat again. If it keeps failing, copy the
    echo     last 20 lines of this window into an issue.
    goto :fail
)
echo       installed

echo [4/4] first setup
"%VPY%" -m reelcut.firstrun
if errorlevel 3 (
    echo.
    echo [!] Setup needs one more step - the notes above say which.
    echo     Run this any time:  .venv\Scripts\reelcut.exe setup
)
echo.
echo ============================================================
echo   Done. Useful commands:
echo     .venv\Scripts\reelcut.exe status
echo     .venv\Scripts\reelcut.exe transcribe-gemini "path\to\project"
echo     .venv\Scripts\reelcut.exe brolls "path\to\project"
echo ============================================================
echo.
pause
endlocal & exit /b 0

:fail
echo.
echo Installer stopped. Fix the item above, then run install.bat again.
echo.
pause
endlocal & exit /b 2
