#!/bin/bash

# Script per creare pacchetto del progetto da caricare su VPS
# Uso: bash package_for_vps.sh

set -e

echo "📦 Creazione pacchetto Polymarket Bot..."

# Directory progetto
PROJECT_DIR="/c/Users/gianl/Desktop/ProgettiVari/polymarket-prediction"
PACKAGE_NAME="polymarket-bot-$(date +%Y%m%d-%H%M%S).tar.gz"

cd "$PROJECT_DIR"

# Crea archivio escludendo file non necessari
tar -czf "$PACKAGE_NAME" \
    --exclude='venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.git' \
    --exclude='*.log' \
    --exclude='check_vps.sh' \
    src/ templates/ docs/ requirements.txt README.md

# Calcola dimensione
SIZE=$(du -h "$PACKAGE_NAME" | cut -f1)

echo ""
echo "✅ Pacchetto creato: $PACKAGE_NAME ($SIZE)"
echo ""
echo "📤 COMANDI PER CARICARE SU VPS:"
echo ""
echo "1. Carica il pacchetto:"
echo "   scp $PACKAGE_NAME root@217.154.205.44:/root/"
echo ""
echo "2. Carica lo script di deploy:"
echo "   scp deploy_polymarket.sh root@217.154.205.44:/root/"
echo ""
echo "3. Collegati alla VPS:"
echo "   ssh root@217.154.205.44"
echo ""
echo "4. Estrai il pacchetto:"
echo "   cd /root"
echo "   tar -xzf $PACKAGE_NAME"
echo "   mv polymarket-bot-*/ polymarket-bot/"
echo ""
echo "5. Esegui il deploy:"
echo "   bash deploy_polymarket.sh"
echo ""
echo "6. Accedi alla dashboard:"
echo "   http://217.154.205.44:5000"
echo ""
