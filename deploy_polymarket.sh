#!/bin/bash

# ============================================================================
# POLYMARKET PAPER TRADING BOT - SCRIPT DEPLOY COMPLETO
# ============================================================================
# Uso: bash deploy_polymarket.sh
# 
# Questo script:
# 1. Installa tutte le dipendenze necessarie
# 2. Crea il progetto in /root/polymarket-bot
# 3. Configura virtual environment
# 4. Avvia bot + dashboard in screen separati
# 5. Fornisce URL accesso dashboard
# ============================================================================

set -e  # Esce su errore

# Colori per output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}"
echo "=========================================="
echo "  POLYMARKET BOT - DEPLOY AUTOMATICO"
echo "=========================================="
echo -e "${NC}"

# Configurazione
PROJECT_DIR="/root/polymarket-bot"
DASHBOARD_PORT=5000
SCREEN_BOT="polymarket_bot"
SCREEN_DASHBOARD="polymarket_dashboard"

# 1. Verifica dipendenze sistema
echo -e "${YELLOW}[1/7] Verifica dipendenze sistema...${NC}"
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Python3 non installato! Installazione...${NC}"
    apt update && apt install -y python3 python3-pip python3-venv
else
    echo -e "${GREEN}✅ Python3 già installato: $(python3 --version)${NC}"
fi

if ! command -v screen &> /dev/null; then
    echo -e "${YELLOW}📦 Installazione screen...${NC}"
    apt update && apt install -y screen
else
    echo -e "${GREEN}✅ screen già installato${NC}"
fi

# 2. Crea directory progetto
echo -e "${YELLOW}[2/7] Creazione directory progetto...${NC}"
mkdir -p "$PROJECT_DIR"
cd "$PROJECT_DIR"
echo -e "${GREEN}✅ Progetto creato in: $PROJECT_DIR${NC}"

# 3. Crea struttura directory
echo -e "${YELLOW}[3/7] Creazione struttura file...${NC}"
mkdir -p src templates data logs

# 4. Crea virtual environment
echo -e "${YELLOW}[4/7] Creazione virtual environment...${NC}"
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo -e "${GREEN}✅ Virtual environment creato${NC}"
else
    echo -e "${GREEN}✅ Virtual environment già esistente${NC}"
fi

# Attiva venv e installa dipendenze
source venv/bin/activate
pip install --upgrade pip
pip install flask requests python-dotenv
echo -e "${GREEN}✅ Dipendenze Python installate${NC}"

# 5. Ferma screen esistenti (se ci sono)
echo -e "${YELLOW}[5/7] Pulizia screen esistenti...${NC}"
if screen -list | grep -q "$SCREEN_BOT"; then
    screen -S "$SCREEN_BOT" -X quit
    echo -e "${GREEN}✅ Screen bot fermato${NC}"
fi
if screen -list | grep -q "$SCREEN_DASHBOARD"; then
    screen -S "$SCREEN_DASHBOARD" -X quit
    echo -e "${GREEN}✅ Screen dashboard fermato${NC}"
fi

# 6. Crea script di avvio
echo -e "${YELLOW}[6/7] Creazione script di avvio...${NC}"

# Script avvio bot
cat > start_bot.sh << 'EOF'
#!/bin/bash
cd /root/polymarket-bot
source venv/bin/activate
cd src
python3 main.py
EOF
chmod +x start_bot.sh

# Script avvio dashboard
cat > start_dashboard.sh << EOF
#!/bin/bash
cd /root/polymarket-bot
source venv/bin/activate
cd src
python3 dashboard.py --port $DASHBOARD_PORT
EOF
chmod +x start_dashboard.sh

echo -e "${GREEN}✅ Script di avvio creati${NC}"

# 7. Avvia servizi
echo -e "${YELLOW}[7/7] Avvio servizi...${NC}"

# Avvia bot in screen
echo -e "${BLUE}🚀 Avvio bot in screen '$SCREEN_BOT'...${NC}"
screen -dmS "$SCREEN_BOT" ./start_bot.sh
sleep 2

# Avvia dashboard in screen
echo -e "${BLUE}🚀 Avvio dashboard in screen '$SCREEN_DASHBOARD'...${NC}"
screen -dmS "$SCREEN_DASHBOARD" ./start_dashboard.sh
sleep 3

# Verifica che siano attivi
echo ""
echo -e "${BLUE}📊 Verifica servizi attivi:${NC}"
screen -list | grep -E "$SCREEN_BOT|$SCREEN_DASHBOARD" || echo -e "${RED}❌ Errore avvio servizi${NC}"

# ============================================================================
# RIEPILOGO FINALE
# ============================================================================
echo ""
echo -e "${GREEN}"
echo "=========================================="
echo "  ✅ DEPLOY COMPLETATO CON SUCCESSO!"
echo "=========================================="
echo -e "${NC}"

echo -e "${BLUE}📍 INFORMAZIONI ACCESSO:${NC}"
echo ""
echo -e "🌐 ${GREEN}Dashboard Web:${NC}"
echo -e "   URL: ${YELLOW}http://217.154.205.44:$DASHBOARD_PORT${NC}"
echo ""
echo -e "🖥️  ${GREEN}Screen Sessions:${NC}"
echo -e "   Bot:        ${YELLOW}screen -r $SCREEN_BOT${NC}"
echo -e "   Dashboard:  ${YELLOW}screen -r $SCREEN_DASHBOARD${NC}"
echo ""
echo -e "📁 ${GREEN}Directory Progetto:${NC}"
echo -e "   ${YELLOW}$PROJECT_DIR${NC}"
echo ""
echo -e "📋 ${GREEN}Comandi Utili:${NC}"
echo -e "   Lista screen:  ${YELLOW}screen -list${NC}"
echo -e "   Vedi log bot:  ${YELLOW}tail -f $PROJECT_DIR/logs/bot.log${NC}"
echo -e "   Ferma bot:     ${YELLOW}screen -S $SCREEN_BOT -X quit${NC}"
echo -e "   Ferma dash:    ${YELLOW}screen -S $SCREEN_DASHBOARD -X quit${NC}"
echo -e "   Riavvia tutto: ${YELLOW}bash $PROJECT_DIR/deploy_polymarket.sh${NC}"
echo ""
echo -e "${BLUE}🔥 ${YELLOW}Apri nel browser: http://217.154.205.44:$DASHBOARD_PORT${NC}"
echo ""
echo -e "${GREEN}✅ Sistema operativo e pronto!${NC}"
echo ""

# Test connessione dashboard
echo -e "${BLUE}🧪 Test connessione dashboard...${NC}"
sleep 2
if curl -s -o /dev/null -w "%{http_code}" "http://localhost:$DASHBOARD_PORT/api/status" | grep -q "200"; then
    echo -e "${GREEN}✅ Dashboard risponde correttamente!${NC}"
else
    echo -e "${YELLOW}⚠️  Dashboard potrebbe richiedere qualche secondo per avviarsi...${NC}"
    echo -e "   Controlla con: ${YELLOW}curl http://localhost:$DASHBOARD_PORT/api/status${NC}"
fi

echo ""
echo -e "${GREEN}🎉 FINE DEPLOY${NC}"
