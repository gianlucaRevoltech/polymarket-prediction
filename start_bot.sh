#!/bin/bash
# Avvia il bot di paper trading

echo "========================================="
echo "  Polymarket Paper Trading Bot"
echo "========================================="

# Verifica ambiente virtuale
if [ ! -d "venv" ]; then
    echo "❌ Ambiente virtuale non trovato!"
    echo "   Esegui: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

# Attiva ambiente
source venv/bin/activate

# Crea directory necessarie
mkdir -p data logs

# Avvia bot
echo "Avvio bot in $(pwd)/src..."
cd src
python3 main.py
