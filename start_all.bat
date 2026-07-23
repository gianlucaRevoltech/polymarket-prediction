@echo off
REM ============================================================
REM  Polymarket Paper Trading Bot - gestione servizi (Windows)
REM
REM  Uso:
REM    start_all.bat [start|stop|restart|new-run|install|reset|status|scan] [scan] [--force]
REM
REM    start    (default) installa deps se serve, ferma istanze e avvia
REM    start scan  forza scan wallet prima dell'avvio
REM    scan     aggiorna data\scan_results.json (wallet specialisti per categoria)
REM    stop     ferma bot + dashboard (via PID file)
REM    restart  stop + start, conserva sempre stato e storico
REM    new-run  archivia ledger/config, azzera il run corrente e riavvia
REM    install  crea/aggiorna virtualenv e installa requirements
REM    reset --force  archivia e poi azzera (senza riavviare)
REM    status   mostra stato dei servizi
REM
REM    Env: set SCAN=1 forza scan
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "VENV=venv"
set "PY=%VENV%\Scripts\python.exe"
set "PORT=5000"
set "SCAN_TOP=20"
set "SCAN_RESULTS=data\scan_results.json"

set "ACTION=%~1"
if "%ACTION%"=="" set "ACTION=start"

set "SCAN_FORCE=0"
set "FORCE_FLAG=0"
call :parse_opt %2
if errorlevel 1 exit /b %errorlevel%
call :parse_opt %3
if errorlevel 1 exit /b %errorlevel%
call :parse_opt %4
if errorlevel 1 exit /b %errorlevel%
if "%SCAN%"=="1" set "SCAN_FORCE=1"
if "%FORCE_SCAN%"=="1" set "SCAN_FORCE=1"

if /i "%ACTION%"=="start"   goto :start
if /i "%ACTION%"=="stop"    goto :stop_only
if /i "%ACTION%"=="restart" goto :restart
if /i "%ACTION%"=="new-run" goto :new_run
if /i "%ACTION%"=="install" goto :install_only
if /i "%ACTION%"=="reset"   goto :reset
if /i "%ACTION%"=="status"  goto :status
if /i "%ACTION%"=="scan"    goto :scan_only

echo Azione sconosciuta: %ACTION%
echo Uso: start_all.bat [start^|stop^|restart^|new-run^|install^|reset^|status^|scan] [scan] [--force]
exit /b 1

:parse_opt
if "%~1"=="" exit /b 0
if /i "%~1"=="scan" set "SCAN_FORCE=1"
if /i "%~1"=="--force" set "FORCE_FLAG=1"
if /i "%~1"=="reset" (
  echo [ERRORE] 'restart reset' rimosso. Usa new-run oppure reset --force.
  exit /b 2
)
if /i "%~1"=="fresh" (
  echo [ERRORE] 'fresh' rimosso. Usa new-run.
  exit /b 2
)
exit /b 0

:clear_state
"%PY%" tools\run_state.py clear --force
exit /b %errorlevel%

:archive_state
"%PY%" tools\run_state.py archive
exit /b %errorlevel%

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
call :run_scan
exit /b 0

:run_scan
echo [SCAN] Wallet specialisti per categoria (top %SCAN_TOP%) ...
echo [SCAN] Output anche in logs\scan_categories.log
"%PY%" -u src\scanner.py --mode categories --top %SCAN_TOP% > logs\scan_categories.log 2>&1
type logs\scan_categories.log
if not exist "%SCAN_RESULTS%" (
  echo [ERRORE] Scanner completato ma %SCAN_RESULTS% non trovato.
  exit /b 1
)
echo [SCAN] Fatto: %SCAN_RESULTS%
exit /b 0

:ensure_scan
if "%SCAN_FORCE%"=="1" goto :run_scan_before_start
if not exist "%SCAN_RESULTS%" (
  echo [SCAN] %SCAN_RESULTS% assente: avvio scanner automatico ...
  call :run_scan
  exit /b 0
)
echo [SCAN] %SCAN_RESULTS% presente (salto; usa 'start_all.bat scan' per aggiornare)
exit /b 0

:run_scan_before_start
echo [SCAN] Refresh forzato ...
call :run_scan
exit /b 0

:scan_only
call :ensure_venv
if errorlevel 1 exit /b 1
if not exist data mkdir data
if not exist logs mkdir logs
call :run_scan
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
if not "%FORCE_FLAG%"=="1" (
  echo [ERRORE] reset richiede --force. Stato invariato.
  exit /b 2
)
call :stop
call :ensure_venv
if errorlevel 1 exit /b 1
call :archive_state
if errorlevel 1 exit /b 1
call :clear_state
if errorlevel 1 exit /b 1
goto :status

REM ----------------------------------------------------------------
:restart
call :stop
goto :start

:new_run
call :stop
call :ensure_venv
if errorlevel 1 exit /b 1
call :archive_state
if errorlevel 1 exit /b 1
call :clear_state
if errorlevel 1 exit /b 1
goto :start

REM ----------------------------------------------------------------
:start
call :ensure_venv
if errorlevel 1 exit /b 1
if not exist data mkdir data
if not exist logs mkdir logs
call :ensure_scan
if errorlevel 1 exit /b 1
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
