#!/bin/bash

# Script per raccogliere informazioni VPS per deploy Polymarket Bot
# Uso: bash check_vps.sh

echo "=========================================="
echo "  CHECK VPS - Polymarket Bot Deploy Info"
echo "=========================================="
echo ""

# 1. Informazioni Sistema
echo "📋 INFORMAZIONI SISTEMA"
echo "------------------------"
echo "Hostname: $(hostname)"
echo "OS: $(cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d'"' -f2 || echo 'Non disponibile')"
echo "Kernel: $(uname -r)"
echo "Architettura: $(uname -m)"
echo "Uptime: $(uptime -p 2>/dev/null || uptime)"
echo ""

# 2. Python
echo "🐍 PYTHON"
echo "----------"
if command -v python3 &> /dev/null; then
    echo "Python3: $(python3 --version)"
    echo "Path: $(which python3)"
else
    echo "Python3: NON INSTALLATO"
fi

if command -v pip3 &> /dev/null; then
    echo "pip3: $(pip3 --version)"
else
    echo "pip3: NON INSTALLATO"
fi
echo ""

# 3. Spazio Disco
echo "💾 SPAZIO DISCO"
echo "----------------"
df -h / | tail -1 | awk '{print "Totale: "$2" | Usato: "$3" | Disponibile: "$4" | Uso: "$5}'
echo ""

# 4. Memoria
echo "🧠 MEMORIA"
echo "-----------"
free -h | grep Mem | awk '{print "Totale: "$2" | Usata: "$3" | Disponibile: "$7}'
echo ""

# 5. Porte in uso (comuni)
echo "🔌 PORTE IN USO"
echo "-----------------"
echo "Porte comuni controllate:"
for port in 80 443 5000 8000 8080 8888 3000 5432 6379 27017; do
    if netstat -tuln 2>/dev/null | grep -q ":$port "; then
        echo "  ❌ Porta $port: IN USO"
    else
        echo "  ✅ Porta $port: disponibile"
    fi
done
echo ""

# 6. Servizi Python attivi
echo "🔄 SERVIZI PYTHON ATTIVI"
echo "-------------------------"
ps aux | grep -E "python|flask|gunicorn" | grep -v grep | awk '{print "  PID: "$2" | CPU: "$3"% | MEM: "$4"% | Comando: "$11" "$12}'
if ! ps aux | grep -E "python|flask|gunicorn" | grep -v grep > /dev/null; then
    echo "  Nessun servizio Python attivo"
fi
echo ""

# 7. Utente corrente
echo "👤 UTENTE"
echo "----------"
echo "Utente: $(whoami)"
echo "Home: $HOME"
echo ""

# 8. Connessione Internet
echo "🌐 CONNESSIONE INTERNET"
echo "------------------------"
if ping -c 1 8.8.8.8 &> /dev/null; then
    echo "✅ Connessione: OK"
    echo "IP Pubblico: $(curl -s ifconfig.me 2>/dev/null || echo 'Non rilevabile')"
else
    echo "❌ Connessione: FALLITA"
fi
echo ""

# 9. Directory corrente
echo "📁 DIRECTORY"
echo "-------------"
echo "Directory attuale: $(pwd)"
echo "Spazio in directory: $(df -h . | tail -1 | awk '{print $4" disponibile"}')"
echo ""

# 10. Riepilogo per deploy
echo "=========================================="
echo "  RIEPILOGO PER DEPLOY"
echo "=========================================="
echo ""
echo "Copia queste informazioni e mandale al bot:"
echo ""
echo "---"
echo "IP Pubblico: $(curl -s ifconfig.me 2>/dev/null || echo 'Non rilevabile')"
echo "Python: $(python3 --version 2>&1 || echo 'Non installato')"
echo "Porta 5000: $(netstat -tuln 2>/dev/null | grep -q ':5000 ' && echo 'IN USO' || echo 'disponibile')"
echo "Spazio disponibile: $(df -h / | tail -1 | awk '{print $4}')"
echo "Memoria disponibile: $(free -h | grep Mem | awk '{print $7}')"
echo "Utente: $(whoami)"
echo "---"
echo ""
echo "✅ Script completato!"
