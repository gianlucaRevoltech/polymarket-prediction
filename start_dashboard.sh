#!/bin/bash
# Avvia la dashboard web

echo "========================================="
echo "  Polymarket Dashboard"
echo "========================================="

# Verifica ambiente virtuale
if [ ! -d "venv" ]; then
    echo "❌ Ambiente virtuale non trovato!"
    echo "   Esegui: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

# Attiva ambiente
source venv/bin/activate

# Porta (default 5000, modificabile con argomento)
PORT=${1:-5000}

# Crea directory necessarie
mkdir -p data logs

echo ""
echo "🌐 Dashboard disponibile su: http://0.0.0.0:$PORT"
echo "   Premi Ctrl+C per fermare"
echo ""

# Avvia dashboard
cd src
python3 dashboard.py --port $PORT
