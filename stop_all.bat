@echo off
REM ========================================
REM Polymarket Paper Trading Bot - STOP ALL
REM Kills all Python processes
REM ========================================

echo.
echo ========================================
echo   Stopping all Python processes...
echo ========================================
echo.

REM Get all Python PIDs and kill them forcefully
for /f "tokens=2" %%a in ('tasklist ^| findstr /i "python"') do (
    echo Killing PID: %%a
    taskkill /F /PID %%a 2>nul
)

REM Wait and verify
timeout /t 2 /nobreak >nul

REM Check if any Python processes remain
tasklist | findstr /i "python" >nul
if %errorlevel% == 0 (
    echo.
    echo [WARNING] Some processes could not be killed.
    echo Trying alternative method...
    taskkill /F /IM python.exe /T 2>nul
    taskkill /F /IM pythonw.exe /T 2>nul
    timeout /t 2 /nobreak >nul
)

REM Final check
tasklist | findstr /i "python" >nul
if %errorlevel% == 0 (
    echo.
    echo [ERROR] Could not kill all Python processes.
    echo Please close them manually from Task Manager.
) else (
    echo.
    echo [OK] All Python processes terminated.
)

echo.
pause
