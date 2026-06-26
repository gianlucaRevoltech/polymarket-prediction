#!/bin/bash

# Polymarket Paper Trading Bot - Startup Script
# Usage: ./start.sh

set -e

echo "================================================"
echo "  POLYMARKET PAPER TRADING BOT"
echo "  Starting system..."
echo "================================================"

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 not found"
    exit 1
fi

echo "Python version: $(python3 --version)"

# Install dependencies
echo ""
echo "Installing dependencies..."
pip3 install -r requirements.txt

# Create directories
echo ""
echo "Creating data directories..."
mkdir -p data logs

# Check if config exists
if [ ! -f src/config.py ]; then
    echo "ERROR: config.py not found"
    exit 1
fi

echo ""
echo "Configuration check: OK"
echo ""

# Start bot
echo "Starting bot..."
echo "Press Ctrl+C to stop"
echo ""

cd src
python3 main.py
