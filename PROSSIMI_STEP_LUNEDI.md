# PROSSIMI STEP — Lunedì 21 Luglio 2025

## Situazione attuale (venerdì 17, ~9:30 UTC)

- 3 servizi UP su VPS: bot, dashboard, latency_arb
- Latency-arb validator: 8 trade resolved, WR 25%, P&L virt -$2.198
- Bot copy-trading: baseline registrata, 0 aperture (wallet fermi / weekend)
- Scan: 8 wallet specialisti (3 sport, 5 politics)

> 8 trade sono troppi pochi per concludere. Lasciamo girare tutto il weekend.

---

## STEP 1 — Verifica che i servizi siano ancora vivi

```bash
cd ~/polymarket-prediction
./start_all.sh status
```

**Output atteso:**
```
  bot: IN ESECUZIONE (PID xxxxx)
  dashboard: IN ESECUZIONE (PID xxxxx)
  latency_arb: IN ESECUZIONE (PID xxxxx)
  Dashboard: http://localhost:5000
```

Se qualcuno è fermo:
```bash
./start_all.sh restart
```
(niente reset/scan, manteniamo lo storico accumulato nel weekend)

---

## STEP 2 — Leggi i risultati del latency-arb (più importante)

Dopo 2+ giorni di weekend dovresti avere tra 50 e 200+ trade resolved.

```bash
# conteggio trade resolved
grep -c "\[RESOLVE\]" logs/latency_arb.log

# ultime 5 righe di stats
grep "LATENCY-ARB STATS" logs/latency_arb.log | tail -5

# bucket breakdown (ultimi)
grep -A3 "LATENCY-ARB STATS" logs/latency_arb.log | tail -20

# tutte le resolution (per analisi manuale, salvalo in file)
grep "\[RESOLVE\]" logs/latency_arb.log > /tmp/resolves.txt
wc -l /tmp/resolves.txt
head -20 /tmp/resolves.txt
tail -20 /tmp/resolves.txt
```

**Output atteso / decisioni:**

| resolved | WR tot | Bucket 10-20 WR | Bucket 20+ WR | Decisione |
|----------|--------|-----------------|---------------|-----------|
| < 30     | qualunque | — | — | Troppo poco, lascia girare altri 2 giorni |
| 30-50    | > 52%   | > 50%           | > 55%         | **C'è edge!** Passa a Step 1 (paper trading con ordini virtualifriendlier) |
| 30-50    | 40-52%  | 40-50%          | 50-55%        | Borderline, allunga a 100 trade |
| 30-50    | < 40%   | < 40%           | < 50%         | **No edge.** Leggi Step 5 (alternative) |
| 50-100   | > 52%   | —               | —             | Edge confermato. Valuta soglia 0.15 per pulire rumore |
| 50-100   | < 45%   | —               | —             | Strategia non funziona. Vai a Step 5 |

**Analisi aggiuntiva obbligatoria** (dopo 30+ trade):

```bash
# Split per asset (BTC vs ETH)
echo "=== BTC ==="
grep "\[RESOLVE\].*Bitcoin" logs/latency_arb.log | wc -l
grep "\[RESOLVE\].*Bitcoin.*WIN" logs/latency_arb.log | wc -l
echo "=== ETH ==="
grep "\[RESOLVE\].*Ethereum" logs/latency_arb.log | wc -l
grep "\[RESOLVE\].*Ethereum.*WIN" logs/latency_arb.log | wc -l

# Split per direzione (LONG_YES vs LONG_NO)
echo "=== LONG_YES ==="
grep "\[RESOLVE\] LONG_YES" logs/latency_arb.log | wc -l
grep "\[RESOLVE\] LONG_YES WIN" logs/latency_arb.log | wc -l
echo "=== LONG_NO ==="
grep "\[RESOLVE\] LONG_NO" logs/latency_arb.log | wc -l
grep "\[RESOLVE\] LONG_NO WIN" logs/latency_arb.log | wc -l
```

**Cosa far emerge:**
- Se BTC fa 60% e ETH 10% → bug di calibrazione ETH (fee / liquidity / timestamp)
- Se LONG_YES fa 60% e LONG_NO 10% → la direzione "short" è invertita nel codice
- Se entrambi ~25% → l'intera tesi del latency arb su Polymarket non regge (Polymarket è già efficiente, il feed Binance non anticipa)

---

## STEP 3 — Controlla il bot copy-trading

```bash
# Ultime 30 righe
tail -30 logs/bot.log

# Conta snapshot (deve incrementare nel tempo)
grep "Snapshot" logs/bot.log | tail -5

# Conta aperture
grep -c "APERTA" logs/bot.log   # o come si chiama la riga di apertura trade

# Equity attuale
grep "Equity:" logs/bot.log | tail -5
```

**Output atteso:**
```
Equity: $300.00 (+0.00%) | Aperte: 0 | Chiuse: 0 (WR 0%) | tier 3% dd 0.0%
```
…oppure se i wallet si sono mossi nel weekend:
```
Equity: $300.00 (+0.5%) | Aperte: 2 | Chiuse: 0 (WR 0%) | tier 3% dd 0.0%
```

**Decisioni:**
- Se Equity = $300 / 0 aperture anche dopo 2 giorni → normale se i mercati erano fermi (weekend, sport/politics hanno poco movimento)
- Se Equity è sceso / c'è una chiusura LOSS → guarda il dettaglio del trade:
```bash
grep -B2 -A5 "CHIUS\|LOSS\|WIN" logs/bot.log | tail -40
```

---

## STEP 4 — Controlla la dashboard

In tunnel SSH dalla tua macchina:
```bash
ssh -L 5000:localhost:5000 root@<ip-vps>
```
Poi browser → `http://localhost:5000`

**Verifica:**
- Pagina carica senza errori
- Mostra 8 wallet specialisti
- Mostra equity $300
- Sezione "latency arb" presente con stats aggiornate

Se la dashboard non carica (errore 500):
```bash
tail -30 logs/dashboard.log
```

---

## STEP 5 — Se il validator dice "no edge" (WR < 45% a 30+ trade)

La tesi originale del latency arb su Polymarket non regge. Prospettive da discutere:

### 5a. Alzare la soglia edge
Se `win_20_plus` ha WR > 55% ma `win_10_20` tira sotto, l'edge esiste ma solo su signal forti.

Modifica `src/latency_arb.py`:
```bash
# trova la riga (cerca edge_threshold)
grep -n "edge_threshold" src/latency_arb.py
```
Cambia 0.10 → 0.15 o 0.20, poi:
```bash
./start_all.sh restart reset scan
```
e confronta WR nei 50 trade successivi.

### 5b. Provare finestre più lunghe
Se l'edge con Δ5m non c'è, prova Δ15m o Δ30m. Significato: il prezzo Binance 15-30min prima prevede meglio l'esito Polymarket del prezzo 5min prima. Più tempo per Polymarket di assorbire l'info.

### 5c. Aggiungere un filtro di liquidità
Solo signal su mercati con volume > X (es. top-50 per liquidity). I mercati piccoli sono casinofee e slippage毋庸dont trail.

### 5d. Strategia completamente diversa
Abbandonare latency arb. Lascia il bot copy-trading girare (che è basato su wallets specialisti verificati, altra tesi) e vedi come performa dopo 1-2 settimane di dati reali.

### 5e. Aggiungi un control panel realtime
Crea una pagina separata /latency che mostri:
- Lista dei resolved in real-time
- Grafico del cumulative P&L virt
- WR bucket 10/15/20/25
- Split per asset

Così vedi l'evolversi senza tailare il log.

---

## STEP 6 — Tasks manutentivi (sempre utili)

### 6a. Commit del chmod +x
Non ripetere il "Permission denied":
```bash
cd ~/polymarket-prediction
git update-index --chmod=+x start_all.sh
git commit -m "chmod +x start_all.sh"
git push
```

### 6b. Aggiorna il codice
Se durante il weekend hai cambiato qualcosa in locale:
```bash
# dalla tua macchina locale
cd ~/Desktop/ProgettiVari/polymarket-prediction
git status
git pull
# edita se serve...
git add -A
git commit -m "descrizione cambiamento"
git push

# poi sulla VPS
cd ~/polymarket-prediction
git pull
./start_all.sh restart   # NO reset, NO scan — manteniamo dati del weekend
```

### 6c. Guardia se la VPS ha problemi di memoria
```bash
free -h
df -h
ps aux --sort=-%mem | head -10
ps aux --sort=-%cpu | head -10
```
Latency-arb + bot + dashboard dovrebbero usare complessivamente < 500MB RAM.

---

## Riassunto — cosa incollare in chat lunedì

Quando apri chat lunedì, copia-incolla questo blocco dopo averlo eseguito:

```bash
cd ~/polymarket-prediction

echo "=== STATUS ==="
./start_all.sh status

echo ""
echo "=== LATENCY-ARB ==="
echo "resolved total:"
grep -c "\[RESOLVE\]" logs/latency_arb.log
echo "ultime stats:"
grep "LATENCY-ARB STATS" logs/latency_arb.log | tail -1
echo "bucket:"
grep -A3 "LATENCY-ARB STATS" logs/latency_arb.log | tail -4

echo ""
echo "=== BOT ==="
grep "Equity:" logs/bot.log | tail -3

echo ""
echo "=== SYSTEM ==="
free -h | head -2
df -h / | tail -1
```

Vediamo insieme i numeri e decidiamo se passare a Step 1 (paper trading con ordini virtuali), alzare la soglia, o cambiare strategia.

---

## File di riferimento (da leggere prima della decisione)

- `ARBITRAGE_LATENCY_PLAN.md` — il piano originario del validator
- `progress.md` / `findings.md` — appunti di analisi pregressa
- `task_plan.md` — breakdown dei task avanzamento Phase
- `data/latency_arb_stats.json` — stats grezze del validator (per analisi custom)

---

**Procedura riavvio pulito (se serve ripartire da zero lunedì):**

```bash
cd ~/polymarket-prediction
git pull
./start_all.sh restart reset scan
```

**Procedura riavvio CONSERVATIVO (mantiengo dati weekend — default lunedì):**
```bash
cd ~/polymarket-prediction
git pull
./start_all.sh restart
```