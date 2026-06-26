@echo off
REM ============================================================
REM  Polymarket Paper Trading Bot - gestione servizi (Windows)
REM
REM  Uso:
REM    start_all.bat [start|stop|restart|install|reset|status]
REM
REM    start    (default) installa deps se serve, ferma istanze e avvia
REM    stop     ferma bot + dashboard (via PID file)
REM    restart  stop + start
REM    install  crea/aggiorna virtualenv e installa requirements
REM    reset    ferma tutto e azzera lo stato della simulazione
REM    status   mostra stato dei servizi
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "VENV=venv"
set "PY=%VENV%\Scripts\python.exe"
set "PORT=5000"

set "ACTION=%~1"
if "%ACTION%"=="" set "ACTION=start"

if /i "%ACTION%"=="start"   goto :start
if /i "%ACTION%"=="stop"    goto :stop_only
if /i "%ACTION%"=="restart" goto :restart
if /i "%ACTION%"=="install" goto :install_only
if /i "%ACTION%"=="reset"   goto :reset
if /i "%ACTION%"=="status"  goto :status

echo Azione sconosciuta: %ACTION%
echo Uso: start_all.bat [start^|stop^|restart^|install^|reset^|status]
exit /b 1

REM ----------------------------------------------------------------
:ensure_venv
if not exist "%PY%" (
  echo [SETUP] Creo virtualenv...
  python -m venv "%VENV%"
  if errorlevel 1 ( echo [ERRORE] Creazione venv fallita. & exit /b 1 )
  call :install
)
exit /b 0

:install
echo [SETUP] Installo/aggiorno dipendenze...
"%PY%" -m pip install --upgrade pip >nul
"%PY%" -m pip install -r requirements.txt
exit /b 0

:install_only
call :ensure_venv
call :install
exit /b 0

REM ----------------------------------------------------------------
:killpid
set "PIDFILE=%~1"
if exist "%PIDFILE%" (
  set /p KPID=<"%PIDFILE%"
  if defined KPID taskkill /F /PID !KPID! >nul 2>&1
  del "%PIDFILE%" >nul 2>&1
)
exit /b 0

:stop
echo [STOP] Arresto servizi...
call :killpid "data\bot.pid"
call :killpid "data\dashboard.pid"
REM Fallback per finestre con titolo noto
taskkill /F /FI "WINDOWTITLE eq Polymarket Bot*" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq Polymarket Dashboard*" >nul 2>&1
echo [STOP] Fatto.
exit /b 0

:stop_only
call :stop
goto :status

REM ----------------------------------------------------------------
:reset
call :stop
echo [RESET] Azzero lo stato della simulazione...
del /q "data\portfolio_state.json" >nul 2>&1
del /q "data\trades_log.json" >nul 2>&1
del /q "data\equity_curve.json" >nul 2>&1
echo [RESET] Stato azzerato (scan_results.json mantenuto).
goto :status

REM ----------------------------------------------------------------
:restart
call :stop
goto :start

REM ----------------------------------------------------------------
:start
call :ensure_venv
if errorlevel 1 exit /b 1
if not exist data mkdir data
if not exist logs mkdir logs
call :stop

echo [START] Dashboard su http://localhost:%PORT% ...
start "Polymarket Dashboard" /MIN "%PY%" -u src\dashboard.py
timeout /t 2 /nobreak >nul

echo [START] Bot (mirroring copy/consenso) ...
start "Polymarket Bot" /MIN "%PY%" -u src\main.py
timeout /t 3 /nobreak >nul

start http://localhost:%PORT%
goto :status

REM ----------------------------------------------------------------
:status
echo.
echo =================== STATO ===================
if exist "data\bot.pid" (
  set /p BPID=<"data\bot.pid"
  echo   bot: PID !BPID!
) else (
  echo   bot: fermo
)
if exist "data\dashboard.pid" (
  set /p DPID=<"data\dashboard.pid"
  echo   dashboard: PID !DPID!
) else (
  echo   dashboard: fermo
)
echo   Dashboard: http://localhost:%PORT%
echo   Output: finestre minimizzate "Polymarket Bot" / "Polymarket Dashboard"
echo =============================================
exit /b 0
