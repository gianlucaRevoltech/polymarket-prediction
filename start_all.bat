@echo off
REM ============================================================
REM  Polymarket Paper Trading Bot - gestione servizi (Windows)
REM
REM  Uso:
REM    start_all.bat [start|stop|restart|install|reset|status|scan] [scan] [reset]
REM
REM    start    (default) installa deps se serve, ferma istanze e avvia
REM    start scan|reset  forza scan wallet e/o azzera storico prima dell'avvio
REM    scan     aggiorna data\scan_results.json (wallet specialisti per categoria)
REM    stop     ferma bot + dashboard (via PID file)
REM    restart  stop + (chiede se azzerare storico) + start
REM    restart reset|scan  riavvio con opzioni esplicite (no prompt se reset)
REM    install  crea/aggiorna virtualenv e installa requirements
REM    reset    ferma tutto e azzera lo stato della simulazione (senza riavviare)
REM    status   mostra stato dei servizi
REM
REM    Env: set SCAN=1 forza scan | set RESET=1 azzera storico senza prompt
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
set "RESET_FLAG=0"
call :parse_opt %2
call :parse_opt %3
call :parse_opt %4
if "%SCAN%"=="1" set "SCAN_FORCE=1"
if "%FORCE_SCAN%"=="1" set "SCAN_FORCE=1"
if "%RESET%"=="1" set "RESET_FLAG=1"

if /i "%ACTION%"=="start"   goto :start
if /i "%ACTION%"=="stop"    goto :stop_only
if /i "%ACTION%"=="restart" goto :restart
if /i "%ACTION%"=="install" goto :install_only
if /i "%ACTION%"=="reset"   goto :reset
if /i "%ACTION%"=="status"  goto :status
if /i "%ACTION%"=="scan"    goto :scan_only

echo Azione sconosciuta: %ACTION%
echo Uso: start_all.bat [start^|stop^|restart^|install^|reset^|status^|scan] [scan] [reset]
exit /b 1

:parse_opt
if "%~1"=="" exit /b 0
if /i "%~1"=="scan" set "SCAN_FORCE=1"
if /i "%~1"=="reset" set "RESET_FLAG=1"
if /i "%~1"=="fresh" set "RESET_FLAG=1"
exit /b 0

:clear_state
echo [RESET] Azzero lo stato della simulazione...
del /q "data\portfolio_state.json" >nul 2>&1
del /q "data\trades_log.json" >nul 2>&1
del /q "data\equity_curve.json" >nul 2>&1
echo [RESET] Stato azzerato (scan_results.json mantenuto).
exit /b 0

:prompt_clear_history
if exist "data\portfolio_state.json" goto :ask_reset
if exist "data\trades_log.json" goto :ask_reset
if exist "data\equity_curve.json" goto :ask_reset
echo [INFO] Nessuno storico da azzerare.
exit /b 0

:ask_reset
echo.
echo Trovato storico operazioni (portfolio, trade, equity).
choice /C SN /M "Azzerare lo storico e ripartire da budget iniziale"
if errorlevel 2 (
  echo [INFO] Storico mantenuto.
  exit /b 0
)
call :clear_state
exit /b 0

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
call :stop
call :clear_state
goto :status

REM ----------------------------------------------------------------
:restart
call :stop
if "%RESET_FLAG%"=="1" (
  call :clear_state
) else (
  call :prompt_clear_history
)
goto :start

REM ----------------------------------------------------------------
:start
call :ensure_venv
if errorlevel 1 exit /b 1
if not exist data mkdir data
if not exist logs mkdir logs
if "%RESET_FLAG%"=="1" call :clear_state
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
