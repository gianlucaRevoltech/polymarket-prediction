@echo off
REM Avvia la dashboard web (Windows)

echo =========================================
echo   Polymarket Dashboard
echo =========================================

REM Verifica ambiente virtuale
if not exist "venv" (
    echo [X] Ambiente virtuale non trovato!
    echo    Esegui: python -m venv venv ^&^& venv\Scripts\activate ^&^& pip install -r requirements.txt
    exit /b 1
)

REM Attiva ambiente
call venv\Scripts\activate.bat

REM Porta (default 5000)
set PORT=%1
if "%PORT%"=="" set PORT=5000

REM Crea directory necessarie
if not exist "data" mkdir data
if not exist "logs" mkdir logs

echo.
echo Dashboard disponibile su: http://localhost:%PORT%
echo    Premi Ctrl+C per fermare
echo.

REM Avvia dashboard
cd src
python dashboard.py --port %PORT%
