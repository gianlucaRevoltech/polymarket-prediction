# Polymarket Paper Trading Bot - Guida al Deploy

## 📋 Panoramica

Sistema di copy trading intelligente che replica automaticamente le strategie dei wallet più profittevoli su Polymarket, con budget virtuale di $300.

### Componenti
- **Scanner**: Trova wallet profittevoli dalla leaderboard
- **Analyzer**: Valuta qualità wallet (ROI, win rate, Sharpe, drawdown)
- **Tracker**: Monitora activity real-time ogni 60s
- **Simulator**: Copia trade con position sizing proporzionale
- **Dashboard**: Interfaccia web per monitoraggio (porta 5000)

---

## 🚀 Deploy su VPS Linux

### 1. Prerequisiti

```bash
# Aggiorna sistema
sudo apt update && sudo apt upgrade -y

# Installa Python 3 e pip
sudo apt install -y python3 python3-pip python3-venv

# Installa git
sudo apt install -y git

# (Opzionale) Installa nginx per reverse proxy
sudo apt install -y nginx
```

### 2. Clona e Prepara il Progetto

```bash
# Crea directory progetto
mkdir -p ~/polymarket-bot
cd ~/polymarket-bot

# Copia i file dal tuo computer (usa scp o git)
# Esempio con scp:
scp -r /path/to/polymarket-prediction/* user@your-vps-ip:~/polymarket-bot/

# Oppure con git:
git clone <your-repo-url> .
```

### 3. Setup Ambiente Virtuale

```bash
# Crea ambiente virtuale
python3 -m venv venv

# Attiva ambiente
source venv/bin/activate

# Installa dipendenze
pip install -r requirements.txt

# Verifica installazione
python -c "import requests, flask; print('OK')"
```

### 4. Struttura Directory

```
polymarket-prediction/
├── src/
│   ├── main.py              # Bot principale
│   ├── dashboard.py         # Dashboard web
│   ├── scanner.py           # Scanner wallet
│   ├── analyzer.py          # Analisi qualità
│   ├── tracker.py           # Monitoraggio real-time
│   ├── simulator.py         # Paper trading engine
│   ├── models.py            # Data models
│   ├── config.py            # Configurazione
│   └── templates/
│       └── index.html       # Dashboard UI
├── data/                    # Dati runtime (creato automaticamente)
├── logs/                    # Log file (creato automaticamente)
├── vps_manager.sh           # Script gestione bot
├── start_bot.sh             # Script avvio bot
├── start_dashboard.sh       # Script avvio dashboard
├── start_all.sh             # Script avvio completo
├── requirements.txt
└── README.md
```

---

## 🎮 Utilizzo

### Avvio Manuale

```bash
# Attiva ambiente virtuale
cd ~/polymarket-bot
source venv/bin/activate

# Avvia il bot
cd src
python main.py

# In un altro terminale, avvia la dashboard
cd src
python dashboard.py
```

### Avvio con Script

```bash
# Avvia solo il bot
./start_bot.sh

# Avvia solo la dashboard
./start_dashboard.sh

# Avvia entrambi
./start_all.sh

# Usa vps_manager.sh per controllo completo
./vps_manager.sh start    # Avvia bot
./vps_manager.sh stop     # Ferma bot
./vps_manager.sh restart  # Riavvia bot
./vps_manager.sh status   # Mostra stato
./vps_manager.sh logs     # Mostra log
```

### Accesso Dashboard

Una volta avviata, la dashboard è accessibile via browser:

```
http://YOUR_VPS_IP:5000
```

La dashboard mostra:
- Stato del bot (running/stopped)
- Portfolio summary (capitale, P&L, posizioni)
- Posizioni aperte con P&L real-time
- Wallet monitorati con ROI
- Trade recenti copiati

Si aggiorna automaticamente ogni 10 secondi.

---

## 🔧 Configurazione

### File `src/config.py`

Parametri principali modificabili:

```python
# Budget e Risk Management
BUDGET = {
    "initial_capital": 300.0,      # Budget virtuale
    "max_position_size": 0.10,     # Max 10% per trade
    "min_position_size": 5.0,      # Minimo $5
    "max_open_positions": 10,      # Max posizioni aperte
    "reserve_ratio": 0.20          # 20% riserva
}

# Criteri Qualità Wallet
ANALYZER = {
    "min_roi": 0.10,               # ROI minimo 10%
    "min_win_rate": 0.55,          # Win rate minimo 55%
    "max_drawdown": 0.30,          # Drawdown massimo 30%
    "min_consistency": 0.60,       # 60% trade profittevoli
}

# Scanner
SCANNER = {
    "min_profit": 1000,            # Profitto minimo $1K
    "min_trades": 10,              # Minimo 10 trade
    "check_interval": 300          # Controlla ogni 5 min
}
```

### Porta Dashboard

Per cambiare la porta della dashboard, modifica `start_dashboard.sh`:

```bash
python3 dashboard.py --port 8080
```

Oppure modifica direttamente `dashboard.py`:

```python
if __name__ == "__main__":
    run_dashboard(host="0.0.0.0", port=8080)
```

---

## 🔐 Sicurezza

### Firewall

```bash
# Apri solo le porte necessarie
sudo ufw allow 22/tcp      # SSH
sudo ufw allow 5000/tcp    # Dashboard (solo se necessario)
sudo ufw enable

# Per accesso dashboard solo da IP specifico
sudo ufw allow from YOUR_IP to any port 5000
```

### Reverse Proxy con Nginx (Opzionale)

Per accesso tramite dominio e HTTPS:

```nginx
# /etc/nginx/sites-available/polymarket-bot
server {
    listen 80;
    server_name bot.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

```bash
# Attiva configurazione
sudo ln -s /etc/nginx/sites-available/polymarket-bot /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx

# (Opzionale) Installa SSL con Let's Encrypt
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d bot.yourdomain.com
```

---

## 🔄 Esecuzione Persistente con Systemd

Per far partire il bot automaticamente al boot:

### 1. Crea servizio systemd per il bot

```bash
sudo nano /etc/systemd/system/polymarket-bot.service
```

```ini
[Unit]
Description=Polymarket Paper Trading Bot
After=network.target

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/home/YOUR_USERNAME/polymarket-bot/src
ExecStart=/home/YOUR_USERNAME/polymarket-bot/venv/bin/python main.py
Restart=always
RestartSec=10
StandardOutput=append:/home/YOUR_USERNAME/polymarket-bot/logs/bot.log
StandardError=append:/home/YOUR_USERNAME/polymarket-bot/logs/bot.error.log

[Install]
WantedBy=multi-user.target
```

### 2. Crea servizio systemd per la dashboard

```bash
sudo nano /etc/systemd/system/polymarket-dashboard.service
```

```ini
[Unit]
Description=Polymarket Dashboard
After=network.target

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/home/YOUR_USERNAME/polymarket-bot/src
ExecStart=/home/YOUR_USERNAME/polymarket-bot/venv/bin/python dashboard.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### 3. Abilita e avvia servizi

```bash
# Ricarica systemd
sudo systemctl daemon-reload

# Abilita avvio automatico
sudo systemctl enable polymarket-bot
sudo systemctl enable polymarket-dashboard

# Avvia ora
sudo systemctl start polymarket-bot
sudo systemctl start polymarket-dashboard

# Verifica stato
sudo systemctl status polymarket-bot
sudo systemctl status polymarket-dashboard
```

### 4. Comandi utili

```bash
# Logs in real-time
sudo journalctl -u polymarket-bot -f
sudo journalctl -u polymarket-dashboard -f

# Riavvia servizi
sudo systemctl restart polymarket-bot
sudo systemctl restart polymarket-dashboard

# Ferma servizi
sudo systemctl stop polymarket-bot
sudo systemctl stop polymarket-dashboard
```

---

## 📊 Monitoraggio

### Log File

```bash
# Log del bot
tail -f logs/bot.log

# Errori
tail -f logs/bot.error.log

# Trade log (JSON)
cat data/trades_log.json | jq

# Stato portfolio
cat data/portfolio_state.json | jq
```

### Dashboard Web

Accedi a `http://YOUR_VPS_IP:5000` per:
- Visualizzare stato bot in real-time
- Monitorare posizioni aperte
- Vedere wallet tracciati
- Controllare trade copiati

### Controllo Processi

```bash
# Verifica processi attivi
ps aux | grep python

# Uso risorse
top -p $(pgrep -f "main.py")
```

---

## 🐛 Troubleshooting

### Bot non parte

```bash
# Verifica errori
cd ~/polymarket-bot
source venv/bin/activate
cd src
python main.py 2>&1 | tee ../logs/startup.log

# Controlla permessi
chmod +x *.sh
```

### Dashboard non accessibile

```bash
# Verifica che la porta sia aperta
sudo netstat -tulpn | grep 5000

# Testa localmente
curl http://localhost:5000

# Controlla firewall
sudo ufw status
```

### Dati non si aggiornano

```bash
# Verifica connessione API
curl https://gamma-api.polymarket.com/markets?limit=1

# Resetta stato
rm data/portfolio_state.json
rm data/scan_results.json
```

### Consumo risorse elevato

```bash
# Riduci frequenza polling in config.py
TRACKING = {
    "poll_interval": 120,  # Da 60 a 120 secondi
}

# Limita wallet monitorati
# Modifica main.py: top_wallets = self.qualified_wallets[:3]
```

---

## 📈 Performance e Ottimizzazione

### Consigli

1. **Inizia con pochi wallet**: Monitora 3-5 wallet top, non tutti
2. **Aumenta intervallo polling**: 120-300 secondi sono sufficienti
3. **Monitora risorse**: Il bot usa ~100MB RAM
4. **Backup regolari**: Salva `data/` e `logs/` periodicamente

### Backup

```bash
# Backup manuale
tar -czf backup-$(date +%Y%m%d).tar.gz data/ logs/

# Cron job per backup automatico (ogni giorno alle 3 AM)
crontab -e
# Aggiungi:
0 3 * * * cd ~/polymarket-bot && tar -czf ~/backups/backup-$(date +\%Y\%m\%d).tar.gz data/ logs/
```

---

## 📝 Note Importanti

- **Paper Trading**: Questo sistema NON usa denaro reale
- **Scopo Educativo**: Solo per test e apprendimento
- **Non Garantisce Profitti**: Le performance passate non garantiscono risultati futuri
- **Rischio**: Anche il paper trading può avere bug o comportamenti inaspettati
- **API Polymarket**: Usa API pubbliche, nessuna autenticazione richiesta per lettura

---

## 🆘 Supporto

Per problemi o domande:
1. Controlla i log: `logs/bot.log`
2. Verifica la dashboard: `http://YOUR_VPS_IP:5000`
3. Controlla stato servizi: `systemctl status polymarket-bot`

---

**Versione**: 1.0  
**Ultimo aggiornamento**: 2026-06-26
