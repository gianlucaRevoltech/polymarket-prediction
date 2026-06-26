# Polymarket Paper Trading Bot

Sistema di copy trading intelligente che replica automaticamente le strategie dei wallet più profittevoli su Polymarket, con budget virtuale di $300.

## 🚀 Quick Start

### Windows
```bash
# Setup iniziale
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# Avvia sistema completo (bot + dashboard)
start_all.bat

# Oppure separatamente:
start_bot.bat          # Solo il bot
start_dashboard.bat    # Solo la dashboard
```

### Linux/Mac
```bash
# Setup iniziale
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Avvia sistema completo
./start_all.sh

# Oppure separatamente:
./start_bot.sh
./start_dashboard.sh

# Per VPS con gestione avanzata:
./vps_manager.sh start
./vps_manager.sh status
./vps_manager.sh logs
```

## 📊 Dashboard Web

Una volta avviato, accedi alla dashboard su:
- **Windows**: http://localhost:5000
- **VPS**: http://TUO_IP_VPS:5000

La dashboard mostra:
- ✅ Stato del bot (running/stopped)
- 💰 Portfolio summary (capitale, P&L, posizioni)
- 📈 Posizioni aperte con P&L real-time
- 🎯 Wallet monitorati con ROI
- 📝 Trade recenti copiati

Si aggiorna automaticamente ogni 10 secondi.

## 📁 Struttura Progetto

```
polymarket-prediction/
├── src/
│   ├── main.py              # Bot principale (orchestratore)
│   ├── dashboard.py         # Dashboard web Flask
│   ├── scanner.py           # Scanner wallet profittevoli
│   ├── analyzer.py          # Analisi qualità wallet
│   ├── tracker.py           # Monitoraggio real-time activity
│   ├── simulator.py         # Paper trading engine
│   ├── models.py            # Data models (Wallet, Trade, Position)
│   ├── config.py            # Configurazione parametri
│   └── templates/
│       └── index.html       # Dashboard UI
├── data/                    # Dati runtime (creato automaticamente)
│   ├── scan_results.json    # Risultati scansione wallet
│   ├── portfolio_state.json # Stato portfolio
│   └── trades_log.json      # Log trade eseguiti
├── logs/                    # Log file (creato automaticamente)
├── docs/
│   └── deployment.md        # Guida completa deploy VPS
├── vps_manager.sh           # Script gestione VPS (Linux)
├── start_bot.sh/bat         # Avvia solo bot
├── start_dashboard.sh/bat   # Avvia solo dashboard
├── start_all.sh/bat         # Avvia tutto
└── requirements.txt         # Dipendenze Python
```

## 🎯 Come Funziona

### Fase 1: Scansione
Il bot scansiona la leaderboard di Polymarket e identifica i wallet più profittevoli:
- ROI minimo: 10%
- Win rate: > 55%
- Trade minimi: 10
- Drawdown massimo: 30%

### Fase 2: Analisi
Per ogni wallet qualificato, calcola metriche dettagliate:
- **ROI**: Return on Investment
- **Win Rate**: Percentuale mercati vinti
- **Sharpe Ratio**: Rischio/rendimento
- **Consistency**: Stabilità nel tempo
- **Score Qualificazione**: 0-100

### Fase 3: Monitoraggio
Monitora l'activity dei wallet qualificati ogni 60 secondi:
- Rileva nuovi trade in tempo reale
- Valuta se copiare il trade
- Verifica budget disponibile

### Fase 4: Copy Trading
Quando un wallet qualificato fa un trade:
- Calcola position sizing proporzionale (max 10% del budget)
- Copia il trade con il tuo budget virtuale
- Traccia la posizione e il P&L

## ⚙️ Configurazione

Modifica `src/config.py` per personalizzare:

### Budget
```python
BUDGET = {
    "initial_capital": 300.0,      # Budget virtuale
    "max_position_size": 0.10,     # Max 10% per trade
    "min_position_size": 5.0,      # Minimo $5
    "max_open_positions": 10,      # Max posizioni aperte
}
```

### Filtri Wallet
```python
ANALYZER = {
    "min_roi": 0.10,               # ROI minimo 10%
    "min_win_rate": 0.55,          # Win rate minimo 55%
    "max_drawdown": 0.30,          # Drawdown massimo 30%
}
```

### Tracking
```python
TRACKING = {
    "poll_interval": 60,           # Controlla ogni 60s
    "activity_limit": 100,         # Ultime 100 activity
}
```

## 📈 Risultati Test

```
✅ Sistema completo e funzionante!

Budget: $300 (paper trading)
Wallet monitorati: 5
Primo trade copiato: ✅ "Will Japan win on 2026-06-25?"
  - Outcome: No @ $0.615
  - Size: $28.50
  - Cash rimanente: $271.50
```

## 🖥️ Deploy su VPS

Per esecuzione 24/7 su VPS Linux, vedi la guida completa: **[docs/deployment.md](docs/deployment.md)**

### Quick Deploy VPS
```bash
# 1. Clona su VPS
scp -r . user@vps-ip:~/polymarket-bot/

# 2. Setup su VPS
ssh user@vps-ip
cd ~/polymarket-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Avvia con systemd (persistente)
sudo systemctl start polymarket-bot
sudo systemctl enable polymarket-bot

# 4. Monitora
./vps_manager.sh status
./vps_manager.sh logs
```

## 🔍 API Endpoints

La dashboard espone API REST:

- `GET /` - Dashboard UI
- `GET /api/status` - Stato completo (JSON)
- `GET /api/portfolio` - Solo portfolio summary

Esempio risposta `/api/status`:
```json
{
  "summary": {
    "initial_capital": 300.0,
    "current_value": 271.50,
    "total_pnl": -28.50,
    "open_positions": 1
  },
  "positions": [...],
  "recent_trades": [...],
  "bot_status": "running"
}
```

## 🐛 Troubleshooting

### Dashboard non parte
```bash
# Verifica Flask installato
pip install flask

# Controlla porta 5000 libera
# Windows:
netstat -ano | findstr :5000
# Linux:
sudo netstat -tulpn | grep 5000
```

### Bot non trova wallet
```bash
# Resetta dati
rm data/scan_results.json

# Aumenta limite in config.py
SCANNER = {
    "max_age_days": 180,  # Cerca wallet più vecchi
}
```

### Errori API Polymarket
```bash
# Verifica connessione
curl https://gamma-api.polymarket.com/markets?limit=1

# Aumenta timeout in config.py
REQUESTS_TIMEOUT = 30
```

## 📚 Documentazione

- **[docs/deployment.md](docs/deployment.md)** - Guida completa deploy VPS con systemd
- **README.md** - Questo file (quick start)
- **docs/architecture.md** - Architettura sistema (TODO)

## ⚠️ Disclaimer

Questo è un sistema di **paper trading** per scopi educativi. **NON usa denaro reale**.

- Le performance passate non garantiscono risultati futuri
- Il copy trading ha rischi intrinseci
- Usa a tuo rischio e pericolo
- Fai sempre la tua ricerca prima di investire

## 📄 License

MIT License - Uso personale e educativo

## 🆘 Supporto

Per problemi:
1. Controlla `logs/bot.log`
2. Verifica dashboard: `http://localhost:5000`
3. Leggi `docs/deployment.md`

---

**Versione**: 1.0  
**Ultimo aggiornamento**: 2026-06-26
