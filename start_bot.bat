@echo off
REM Avvia il bot di paper trading (Windows)

echo =========================================
echo   Polymarket Paper Trading Bot
echo =========================================

REM Verifica ambiente virtuale
if not exist "venv" (
    echo [X] Ambiente virtuale non trovato!
    echo    Esegui: python -m venv venv ^&^& venv\Scripts\activate ^&^& pip install -r requirements.txt
    exit /b 1
)

REM Attiva ambiente
call venv\Scripts\activate.bat

REM Crea directory necessarie
if not exist "data" mkdir data
if not exist "logs" mkdir logs

REM Avvia bot
echo Avvio bot in %CD%\src...
cd src
python main.py
