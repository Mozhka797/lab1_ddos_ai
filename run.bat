@echo off
rem Lab 1 launcher: installs Python / PostgreSQL if missing (via winget),
rem creates database ddos_lab, installs libraries and starts the app.
rem Diagnostics are written to setup_log.txt next to this file.
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"
set "LOG=%~dp0setup_log.txt"
echo ==== %date% %time% ==== > "%LOG%"

rem ================= 1. Python =================
call :find_python
if not defined PY (
    echo PYTHON: not found, installing via winget >> "%LOG%"
    where winget >nul 2>&1 || goto :no_winget
    echo.
    echo === Installing Python 3.12 (1-3 minutes^) ===
    winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements >> "%LOG%" 2>&1
    call :find_python
)
if not defined PY (
    echo PYTHON: STILL NOT FOUND >> "%LOG%"
    echo [!] Python install failed. See setup_log.txt
    pause & exit /b 1
)
for /f "delims=" %%v in ('"!PY!" --version 2^>^&1') do echo PYTHON: %%v at !PY! >> "%LOG%"

rem ================= 2. PostgreSQL =================
call :find_pg
if not defined PGBIN (
    echo POSTGRES: not found, installing via winget >> "%LOG%"
    where winget >nul 2>&1 || goto :no_winget
    echo.
    echo === Installing PostgreSQL 16 (3-5 minutes^). Click YES in the Windows admin prompt. ===
    echo     Password for user postgres will be: root
    winget install -e --id PostgreSQL.PostgreSQL.16 --accept-package-agreements --accept-source-agreements --override "--mode unattended --unattendedmodeui minimal --superpassword root --serverport 5432" >> "%LOG%" 2>&1
    call :find_pg
    if defined PGBIN echo root> "%~dp0.pg_password"
)
if not defined PGBIN (
    echo POSTGRES: STILL NOT FOUND >> "%LOG%"
    echo [!] PostgreSQL install failed. See setup_log.txt
    pause & exit /b 1
)
echo POSTGRES BIN: !PGBIN! >> "%LOG%"
rem only CHECK the PostgreSQL service, never start/stop anything
rem (/c: = literal phrase; without it findstr treats spaces as OR and matches every service)
set "PGRUN="
sc query state= all | findstr /i /c:"SERVICE_NAME: postgresql" >> "%LOG%"
sc query state= all | findstr /i /c:"SERVICE_NAME: postgresql" >nul && (
    for /f "tokens=2 delims=:" %%s in ('sc query state^= all ^| findstr /i /c:"SERVICE_NAME: postgresql"') do (
        for /f "tokens=*" %%t in ("%%s") do sc query "%%t" | findstr /i /c:"RUNNING" >nul && set "PGRUN=1"
    )
)
if not defined PGRUN (
    echo POSTGRES SERVICE: NOT RUNNING >> "%LOG%"
    echo [!] PostgreSQL service is not running. Start it: Win+R - services.msc - postgresql-x64-NN - Start
    pause & exit /b 1
)
echo POSTGRES SERVICE: RUNNING >> "%LOG%"

rem password: the one we installed with, or ask the user once
if exist "%~dp0.pg_password" (
    set /p PGPASSWORD=<"%~dp0.pg_password"
) else (
    echo.
    set /p PGPASSWORD=Enter password of PostgreSQL user "postgres":
    echo !PGPASSWORD!> "%~dp0.pg_password"
)
set PGUSER=postgres
set PGHOST=localhost
set PGDATABASE=ddos_lab

rem create database (ignore "already exists")
"!PGBIN!\psql.exe" -h localhost -U postgres -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='ddos_lab'" > "%TEMP%\ddos_chk.txt" 2>> "%LOG%"
if errorlevel 1 (
    echo DB CHECK: FAILED, wrong password? >> "%LOG%"
    del "%~dp0.pg_password" >nul 2>&1
    echo [!] Cannot connect to PostgreSQL ^(wrong password?^). Run this file again.
    pause & exit /b 1
)
findstr "1" "%TEMP%\ddos_chk.txt" >nul || "!PGBIN!\psql.exe" -h localhost -U postgres -d postgres -c "CREATE DATABASE ddos_lab" >> "%LOG%" 2>&1
echo DB: ddos_lab ready >> "%LOG%"

rem ================= 3. venv + libraries =================
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    "!PY!" -m venv .venv >> "%LOG%" 2>&1
)
echo Installing libraries (first run 1-2 minutes)...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt >> "%LOG%" 2>&1
if errorlevel 1 (
    echo PIP: FAILED >> "%LOG%"
    echo [!] Library installation failed, see setup_log.txt
    pause & exit /b 1
)
echo PIP: OK >> "%LOG%"

rem ================= 4. Run =================
echo APP: starting >> "%LOG%"
".venv\Scripts\python.exe" main.py >> "%LOG%" 2>&1
echo APP: exited with code %errorlevel% >> "%LOG%"
exit /b 0

rem ---------------------------------------------------------------
:find_python
set "PY="
for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%d\python.exe" set "PY=%%d\python.exe"
if not defined PY for /d %%d in ("%ProgramFiles%\Python3*") do if exist "%%d\python.exe" set "PY=%%d\python.exe"
if not defined PY (
    for /f "delims=" %%p in ('where python 2^>nul ^| findstr /v /i "WindowsApps"') do if not defined PY set "PY=%%p"
)
exit /b 0

:find_pg
set "PGBIN="
for /d %%d in ("%ProgramFiles%\PostgreSQL\*") do if exist "%%d\bin\psql.exe" set "PGBIN=%%d\bin"
exit /b 0

:no_winget
echo WINGET: NOT FOUND >> "%LOG%"
echo [!] winget is not available. Install manually:
echo     Python 3.12+  https://www.python.org/downloads/windows/  (tick "Add Python to PATH")
echo     PostgreSQL    https://www.postgresql.org/download/windows/
pause
exit /b 1
