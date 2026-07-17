"""
Step 0 — Latency Arbitrage Detector (validazione PAPER su feed REALI)

Modulo standalone di validazione: NON piazza ordini, NON tocca il portfolio.
Scopo: capire se l'edge descritto dalla Guida 2 (Polymarket lagga di ~2.7s vs
Binance sui contratti crypto 5/15-min) è reale e sfruttabile PRIMA di investire
un euro in infra/trading reale.

Meccanismo:
  1) fetch ogni 1s i contratti Polymarket "BTC/ETH up or down Nmin" ATTIVI e non
     ancora risolti con endTime nei prossimi 15 min.
  2) fetch ogni 1s il prezzo Binance BTC/ETH via REST `/ticker/price` + un sample
     dei candle 1m per stimare il momentum recente (5min).
  3) confronta p_yes(Polymarket) con p_attesa(UP) ricavata da momentum Binance.
     expected_p(UP) = clamp(0.5 + K * delta_5min, 0.05, 0.95) con K regolabile.
     edge = expected_p(UP) - p_yes.
     Se |edge| > THRESHOLD → SIGNAL loggato.
  4) a scadenza del contratto (resolved), chiude il signal: controlla l'outcome
     vincitore di Polymarket e calcola P&L virtuale su un sizing fisso $1.
  5) stat WR / P&L virtuale stampate periodicamente.

NESSUN ordine viene piazzato. NESSUN capitale toccato. Solo scienza.

Lancio (su VPS Linux, non da Windows locale — gamma geo-blockatta):
    python src/latency_arb.py                 # isolation loop, durata infinita
oppure (background):
    nohup python -u src/latency_arb.py > logs/latency_arb.log 2>&1 &

Per smoke test da Windows locale con trust problematico:
    POLYMARKET_INSECURE=1 python src/latency_arb.py    # ATTENZIONE: verify=False

Output:
    data/latency_arb_signals.jsonl  - 1 evento per signal aperto/risolto
    data/latency_arb_stats.json     - stats rollup (WR, P&L virt, n_signal)

Requisiti: requests (già installato), nessun websocket. pip install certifi
consigliato per trust store VPS affidabile."
"""
import json
import os
import time
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from config import POLYMARKET_API, DATA_DIR

# Opt-in per truststore problematico (Windows locale o cloudflare geo-block):
# default ON (safe). Set POLYMARKET_INSECURE=1 per smoke testing da locale.
_SSL_VERIFY = not (os.environ.get("POLYMARKET_INSECURE", "0") == "1")
if not _SSL_VERIFY:
    try:
        import urllib3; urllib3.disable_warnings()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Config Step 0 (validazione) — tweakabili senza restart per tuning veloce
# ---------------------------------------------------------------------------
LATENCY_ARB = {
    "enabled": True,
    # loop cadenza 1s per VALIDATION. In real Step 1 diverrebbe 100-200ms WebSocket.
    # Notes latenz: Gamma (Cloudflare) può essere geo-blockatto da Windows locale;
    # runnare su VPS Linux (IONOS Germania per Step 1). Binance è publicato globalmente.
    "poll_interval_sec": 1.0,
    # contratti Polymarket di interesse: pattern titolo/slug + max min alla scadenza
    "contract_patterns": [
        "bitcoin up or down", "btc up or down",
        "ethereum up or down", "eth up or down",
        "bitcoin price up or down", "ethereum price up or down",
    ],
    "max_minutes_to_expiry": 15,
    "min_minutes_to_expiry": 0.5,   # no trade agli ultimi secondi (fill rischio)
    # edge threshold per flag signal
    "edge_threshold_pct": 0.10,    # 10 punti % sopra/sotto 0.5 (currency paper
                                     # stavolta, siamo su prob non prezzo)
    # sensibilità model: p(UP) = 0.5 + K * delta_5min_pct
    # K piu alto = più convinto che momentum persista. K=2 conservativo (storico
    # BTC persistenza 5min ~ 60-70% su move > 0.3%, implica K tra 1.5 e 3).
    "momentum_k": 2.0,
    # virtual sizing per calcolo P&L fittizio ($1 per share entry at ask)
    "virtual_size_usdc": 1.0,
    # log file paths
    "signals_file": "data/latency_arb_signals.jsonl",
    "stats_file": "data/latency_arb_stats.json",
    # binance endpoints (public, no key)
    "binance_base": "https://api.binance.com",
    "binance_symbols": ["BTCUSDT", "ETHUSDT"],
    # perciò fetch Binance ogni N cicli per klines (costoso), tickers ogni ciclo
    "klines_every_sec": 5,
}


# ---------------------------------------------------------------------------
# Binance feed: prezzo live + momentum 5min via REST
# ---------------------------------------------------------------------------
class BinanceFeed:
    """REST polling Binance public market data. No API key required."""

    def __init__(self, base: str = LATENCY_ARB["binance_base"]):
        self.base = base
        self.symbols = list(LATENCY_ARB["binance_symbols"])
        self.s = requests.Session()
        self.s.verify = _SSL_VERIFY
        self.s.headers.update({"User-Agent": "latency-arb-validator/0.1"})
        self._last_klines: Dict[str, Tuple[float, float, float]] = {}  # sym -> (t0_5m, price_5m_ago, last)
        self._last_klines_ts: float = 0.0

    def last_prices(self) -> Dict[str, float]:
        """Tick size prezzi istantanei (una sola chiamata ticker/price per tutti)."""
        try:
            r = self.s.get(f"{self.base}/api/v3/ticker/price", timeout=3)
            if not r.ok:
                return {}
            data = r.json()
            out = {d["symbol"]: float(d["price"]) for d in data
                   if d["symbol"] in self.symbols}
            return out
        except Exception as e:
            print(f"[BINANCE] ticker error: {e}")
            return {}

    def _refresh_klines(self):
        """Aggiorna i candle 1m degli ultimi 5min per symbol. Return: dict sym->(price_5min_ago, last)."""
        now_ts = time.time()
        if now_ts - self._last_klines_ts < LATENCY_ARB["klines_every_sec"]:
            return
        self._last_klines_ts = now_ts
        for sym in self.symbols:
            try:
                # 1m klines, limit=6 (5min indietro + corrente)
                r = self.s.get(f"{self.base}/api/v3/klines",
                               params={"symbol": sym, "interval": "1m", "limit": 6},
                               timeout=4)
                if not r.ok:
                    continue
                rows = r.json()
                if len(rows) >= 2:
                    # open del primo candle 5min fa = price ~5min fa
                    price_5m_ago = float(rows[0][1])
                    price_now = float(rows[-1][4])  # close candle corrente
                    self._last_klines[sym] = (price_5m_ago, price_now)
            except Exception:
                continue

    def momentum(self, symbol: str) -> Optional[Tuple[float, float]]:
        """Return (delta_5min_pct, last_price) o None."""
        self._refresh_klines()
        entry = self._last_klines.get(symbol)
        if entry is None:
            # fallback: usa solo last price, delta=0
            prices = self.last_prices()
            p = prices.get(symbol)
            if p is not None:
                return (0.0, p)
            return None
        p_5ago, p_now = entry
        delta_pct = (p_now - p_5ago) / p_5ago if p_5ago > 0 else 0.0
        return (delta_pct, p_now)

    def symbol_for_contract(self, contract_title: str) -> Optional[str]:
        """Mappa titolo contratto Polymarket a symbol Binance."""
        t = (contract_title or "").lower()
        if "bitcoin" in t or "btc" in t:
            return "BTCUSDT"
        if "ethereum" in t or "eth" in t:
            return "ETHUSDT"
        return None


# ---------------------------------------------------------------------------
# Polymarket detector: trova contratti crypto up/down attivi
# ---------------------------------------------------------------------------
CONTRACTS_CACHE_TTL = 30.0  # re-fetch lista mercati ogni 30s


class PolymarketContractFeed:
    """Recupera contratti crypto up/down attivi via gamma API."""

    def __init__(self):
        self.gamma = POLYMARKET_API["gamma"]
        self.clob = POLYMARKET_API["clob"]
        self.s = requests.Session()
        self.s.verify = _SSL_VERIFY
        # User-Agent completa mitigando filtri geo/anti-bot di Cloudflare su gamma
        self.s.headers.update({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
                                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                                          "Chrome/120.0.0.0 Safari/537.36"})
        self._contracts: List[Dict] = []
        self._fetch_ts: float = 0.0

    def _matches_pattern(self, title: str) -> bool:
        t = (title or "").lower()
        for p in LATENCY_ARB["contract_patterns"]:
            if p in t:
                return True
        return False

    def _refresh_active(self) -> List[Dict]:
        """Fetch gamma markets attive, filtra pattern crypto up/down + scadenza breve.

        Usa end_date_min/max (filtro per scadenza ravvicinata) invece di
        ordering per volume: i contratti 5/15min hanno volume basso e non
        compaiono nei top-100 per volumeNum (gamma hard-cappa a 100 risultati).
        """
        now_ts = time.time()
        if now_ts - self._fetch_ts < CONTRACTS_CACHE_TTL:
            return self._contracts
        self._fetch_ts = now_ts
        try:
            # Filtro per scadenza: now -> now + max_minutes + margine.
            # gamma ritorna solo mercati entro questa finestra, bypassando
            # l'ordering per volume che nasconde i contratti niche.
            now = datetime.now(timezone.utc)
            end_min = now.isoformat()
            end_max = (now + timedelta(
                minutes=LATENCY_ARB["max_minutes_to_expiry"] + 5)).isoformat()
            r = self.s.get(f"{self.gamma}/markets",
                           params={"closed": "false", "active": "true",
                                   "end_date_min": end_min,
                                   "end_date_max": end_max,
                                   "limit": 500}, timeout=15)
            if not r.ok:
                return self._contracts
            out = []
            for m in r.json():
                title = (m.get("question") or m.get("title") or "")
                if not self._matches_pattern(title):
                    continue
                # parse tokens + outcomes
                outcomes = _parse_json_list(m.get("outcomes"))
                tokens = _parse_json_list(m.get("clobTokenIds"))
                if len(tokens) < 2 or len(outcomes) < 2:
                    continue
                end = m.get("endDate", "")
                days = _days_to_expiry(end)
                if days is None:
                    continue
                mins = days * 1440.0
                if mins > LATENCY_ARB["max_minutes_to_expiry"] or mins < LATENCY_ARB["min_minutes_to_expiry"]:
                    continue
                events = m.get("events") or []
                event_slug = events[0].get("slug", "") if events else ""
                out.append({
                    "condition_id": m.get("conditionId", ""),
                    # gamma "id" (intero interno) — serve per resolve_contract:
                    # gamma NON supporta filtro condition_ids, ma ?id=<int> funziona.
                    "market_id": m.get("id"),
                    "title": title,
                    "slug": m.get("slug", ""),
                    "event_slug": event_slug,
                    "end_date": end,
                    "minutes_left": mins,
                    "tokens": tokens,
                    "outcomes": outcomes,
                    "closed": bool(m.get("closed", False)),
                })
            self._contracts = out
        except Exception as e:
            print(f"[GAMMA] refresh error: {e}")
        return self._contracts

    def active_contracts(self) -> List[Dict]:
        return self._refresh_active()

    def book_yes(self, token_yes: str) -> Optional[float]:
        """Best ask YES — approssima prezzo Polymarket per l'outcome UP."""
        try:
            r = self.s.get(f"{self.clob}/midpoint",
                           params={"token_id": token_yes}, timeout=5)
            if not r.ok:
                return None
            mid = r.json().get("mid")
            return float(mid) if mid is not None else None
        except Exception:
            return None

    # ----- CLOB path: /markets/{condition_id} diretto, niente filtri ---------
    def _fetch_clob_market(self, condition_id: str) -> Optional[Dict]:
        """CLOB GET /markets/{condition_id} — recupera diretto per condition_id.

        Funziona per qualsiasi stato (live/closed/resolved): gamma non supporta
        filtro condition_ids, e i crypto 5/15min non sono raggiungibili via
        closed=true&active=false (non paginano). La CLOB ritorna sempre il
        mercato per cid, con `tokens` che hanno `outcome` (nome), `price`
        (0/1 post-resolution) e `winner` (bool). Piu robusto di gamma."""
        try:
            r = self.s.get(f"{self.clob}/markets/{condition_id}", timeout=15)
            if not r.ok:
                return None
            m = r.json()
            if not isinstance(m, dict) or not m:
                return None
            return m
        except Exception:
            return None

    def _fetch_market(self, condition_id: str,
                       market_id: Optional[int] = None) -> Optional[Dict]:
        """Recupera un singolo mercato per condition_id. Path primario: CLOB
        (direzionale, niente filtri gamma roti). Fallback gamma ?id=<int> se
        disponibile, poi scan closed (per legacy senza market_id)."""
        # 1) CLOB diretto (sempre, niente filtri)
        m = self._fetch_clob_market(condition_id)
        if m is not None:
            return m
        # 2) gamma ?id=<int> (se noto)
        if market_id is not None:
            try:
                r = self.s.get(f"{self.gamma}/markets",
                               params={"id": int(market_id)}, timeout=10)
                if r.ok:
                    arr = r.json()
                    if isinstance(arr, list) and arr:
                        return arr[0]
            except Exception:
                pass
        # 3) fallback: scan closed markets (gamma), match locale conditionId
        try:
            offset = 0
            limit = 200
            for _ in range(10):
                r = self.s.get(f"{self.gamma}/markets",
                               params={"closed": "true", "active": "false",
                                       "limit": limit, "offset": offset},
                               timeout=15)
                if not r.ok:
                    break
                arr = r.json()
                if not isinstance(arr, list) or not arr:
                    break
                for m in arr:
                    if (m.get("conditionId") or "") == condition_id:
                        return m
                if len(arr) < limit:
                    break
                offset += limit
        except Exception:
            pass
        return None

    def resolve_contract(self, condition_id: str,
                         market_id: Optional[int] = None) -> Optional[bool]:
        """Resolve mercato CLOB: True=UP_won, False=DOWN_won, None=non risolto.

        Path primario: CLOB /markets/{cid}. La CLOB ritorna `tokens` array dove
        ogni token ha `outcome` (nome: "Up"/"Down"), `price` (0.0 o 1.0 post-res),
        `winner` (bool). Derivazione diretta dal `winner` flag — piu robusto di
        parsare outcomePrices (CLOB non li espone). Fallback gamma se CLOB fail.
        """
        try:
            m = self._fetch_market(condition_id, market_id)
            if m is None:
                return None
            closed = bool(m.get("closed", False))
            tokens = m.get("tokens")
            # --- CLOB path: tokens con `winner` flag ---
            if isinstance(tokens, list) and tokens and isinstance(tokens[0], dict):
                if not closed:
                    return None  # ancora live
                up_won = None
                down_won = None
                any_winner = False
                for t in tokens:
                    if not isinstance(t, dict):
                        continue
                    name = (t.get("outcome") or "").lower()
                    won = bool(t.get("winner", False))
                    if won:
                        any_winner = True
                    if ("up" in name or "yes" in name) and won:
                        up_won = True
                    elif ("down" in name or "no" in name) and won:
                        down_won = True
                if not any_winner:
                    # ancora non finalizzato (nessun winner=True)
                    return None
                if up_won:
                    return True
                if down_won:
                    return False
                # winner flag su outcome non Up/Down (es. "Same") → non mappabile
                return None
            # --- gamma fallback: outcomes + outcomePrices ---
            outcomes = _parse_json_list(m.get("outcomes"))
            prices_raw = _parse_json_list(m.get("outcomePrices"))
            if len(outcomes) < 2 or len(prices_raw) < 2:
                if not closed:
                    return None
                return None
            try:
                prices = [float(p) if p is not None else 0.0 for p in prices_raw]
            except Exception:
                return None
            hi_idx = max(range(len(prices)), key=lambda i: prices[i])
            lo_idx = min(range(len(prices)), key=lambda i: prices[i])
            hi = prices[hi_idx]
            lo = prices[lo_idx]
            if not (hi >= 0.95 and lo <= 0.05):
                return None
            winner_name = (outcomes[hi_idx] or "").lower()
            if "up" in winner_name or "yes" in winner_name:
                return True
            if "down" in winner_name or "no" in winner_name:
                return False
            return None
        except Exception:
            return None


def _find_outcome_idx(outcomes, needles) -> Optional[int]:
    """Trova l'index di outcomes il cui nome (lowercased) contiene uno dei needles.
    None se niente matcha."""
    for i, name in enumerate(outcomes or []):
        n = (name or "").lower()
        for k in needles:
            if k in n:
                return i
    return None


def _parse_json_list(raw):
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            v = json.loads(raw)
            return v if isinstance(v, list) else []
        except Exception:
            return []
    return []


def _days_to_expiry(end_date_iso: str) -> Optional[float]:
    if not end_date_iso:
        return None
    try:
        s = end_date_iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (dt - now).total_seconds() / 86400.0
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Detector principale: calcwork edge + log signals + resolve virt
# ---------------------------------------------------------------------------
class LatencyArbDetector:
    """Valida edge su feed reali senza piazzare ordini. Stats in jsonl/json."""

    def __init__(self):
        self.binance = BinanceFeed()
        self.poly = PolymarketContractFeed()
        self.signals_file = Path(LATENCY_ARB["signals_file"])
        self.stats_file = Path(LATENCY_ARB["stats_file"])
        self.signals_file.parent.mkdir(parents=True, exist_ok=True)
        # pending: condition_id -> signal dict
        self.pending: Dict[str, Dict] = {}
        self._load_pending_from_log()
        self.stats = {"n_signals": 0, "n_resolved": 0, "n_win": 0,
                      "virtual_pnl": 0.0, "by_edge_bucket": {"win_10_20": [0, 0],
                                                              "win_20_plus": [0, 0],
                                                              "all": [0, 0]}}
        self._load_stats()

    def _load_pending_from_log(self):
        """Ripristina signal unresolved dal log se restart."""
        if not self.signals_file.exists():
            return
        try:
            with open(self.signals_file, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if rec.get("status") == "open":
                        self.pending[rec["condition_id"]] = rec
        except Exception:
            pass

    def _load_stats(self):
        if not self.stats_file.exists():
            return
        try:
            with open(self.stats_file, "r", encoding="utf-8") as f:
                self.stats.update(json.load(f))
        except Exception:
            pass

    def _save_stats(self):
        try:
            with open(self.stats_file, "w", encoding="utf-8") as f:
                json.dump(self.stats, f, indent=2)
        except Exception:
            pass

    def _append_signal(self, rec: Dict):
        try:
            with open(self.signals_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception as e:
            print(f"[LOG] append error: {e}")

    def _update_signal(self, rec: Dict):
        """Aggiorna un record (resolve). Append una nuova riga con status=resolved."""
        self._append_signal(rec)

    # ---------------- detector ---------------------------------------------
    def scan_cycle(self) -> int:
        """Un ciclo di detection. Ritorna numero di nuovi signal aperti questo ciclo."""
        contracts = self.poly.active_contracts()
        if not contracts:
            # anche senza contratti, prova a risolvere i pending
            self._resolve_pending()
            return 0
        prices = self.binance.last_prices()
        n_open = 0
        for c in contracts:
            cid = c["condition_id"]
            if cid in self.pending:
                continue  # già signal aperto per questo contract
            sym = self.binance.symbol_for_contract(c["title"])
            if not sym or sym not in prices:
                continue
            mom = self.binance.momentum(sym)
            if mom is None:
                continue
            delta_5m, p_binance = mom
            # Match outcomes per NOME invece di assumere outcomes[0]="Up".
            # Polymarket spesso ordina alfabeticamente → ["Down","Up"],
            # in quel caso tokens[0] e' il token DOWN e p_yes e' p(DOWN).
            up_idx = _find_outcome_idx(c.get("outcomes") or [], ("up", "yes"))
            down_idx = _find_outcome_idx(c.get("outcomes") or [], ("down", "no"))
            if up_idx is None or down_idx is None or up_idx == down_idx:
                # non riusciamo a label-izzare (es. "Same"): skip
                continue
            if len(c["tokens"]) <= max(up_idx, down_idx):
                continue
            token_up = c["tokens"][up_idx]
            # book_yes chiama /midpoint sul token_id: prezzo del token specifico
            p_up_market = self.poly.book_yes(token_up)
            if p_up_market is None or p_up_market <= 0 or p_up_market >= 1:
                continue
            # model: expected p(UP) da momentum Binance
            K = LATENCY_ARB["momentum_k"]
            expected_up = 0.5 + K * delta_5m
            expected_up = max(0.05, min(0.95, expected_up))
            # edge = expected p(UP) - prezzo mercato del token UP (p(UP)_market)
            edge = expected_up - p_up_market
            if abs(edge) < LATENCY_ARB["edge_threshold_pct"]:
                continue
            action = "LONG_YES" if edge > 0 else "LONG_NO"
            # virtual entry at ask (taker ottimistico) sul lato scelto
            # LONG_YES → compra token UP a p_up_market
            # LONG_NO  → compra token DOWN a (1 - p_up_market)
            entry_price = p_up_market if action == "LONG_YES" else (1.0 - p_up_market)
            rec = {
                "ts_open": datetime.now(timezone.utc).isoformat(),
                "status": "open",
                "condition_id": cid,
                # market_id (intero gamma) per resolve_contract robusto:
                # gamma NON filtra per condition_ids, ma ?id=<int> si.
                "market_id": c.get("market_id"),
                "title": c["title"],
                "end_date": c["end_date"],
                "minutes_left": round(c["minutes_left"], 2),
                "symbol": sym,
                "binance_price": round(p_binance, 2),
                "delta_5m_pct": round(delta_5m * 100, 3),
                "expected_p_up": round(expected_up, 4),
                "p_up_market": round(p_up_market, 4),
                # alias legacy p_yes = p_up_market (per compat report)
                "p_yes": round(p_up_market, 4),
                "up_idx": up_idx,
                "down_idx": down_idx,
                "outcomes": c.get("outcomes") or [],
                "edge": round(edge, 4),
                "action": action,
                "entry_price": round(entry_price, 4),
                "virtual_size_usdc": LATENCY_ARB["virtual_size_usdc"],
            }
            # cleanup di eventuali campi legacy (no-op su record freschi)
            rec.pop("payload_full_same", None)
            self.pending[cid] = rec
            self._append_signal(rec)
            n_open += 1
            print(f"[SIGNAL] {action} | {c['title'][:45]} | edge={edge:+.2f} | "
                  f"p_up={p_up_market:.3f} | Δ5m Binance={delta_5m*100:+.2f}% | "
                  f"{c['minutes_left']:.1f}min")
        # risolvi pending
        self._resolve_pending()
        return n_open

    def _resolve_pending(self):
        if not self.pending:
            return
        for cid, sig in list(self.pending.items()):
            days = _days_to_expiry(sig.get("end_date", ""))
            if days is None or days > 0:
                # ancora non scaduto
                continue
            # scaduto: prova a leggere il risultato (passa market_id se noto)
            result = self.poly.resolve_contract(cid, sig.get("market_id"))
            if result is None:
                # non ancora risolto/known; resta pending fino a fetch riuscita
                # limit retry: dopo ~5 min ancora non risolto → close come stale
                stale = (datetime.now(timezone.utc) -
                         datetime.fromisoformat(sig["ts_open"])).total_seconds()
                if stale > 600:
                    sig["status"] = "stale"
                    sig["ts_close"] = datetime.now(timezone.utc).isoformat()
                    sig["resolved"] = None
                    sig["virtual_pnl"] = 0.0
                    sig["win"] = None
                    self._update_signal(sig)
                    self.pending.pop(cid, None)
                continue
            # risultato: True=UP won, False=DOWN won
            up_won = bool(result)
            action = sig["action"]
            won = (action == "LONG_YES" and up_won) or (action == "LONG_NO" and not up_won)
            entry = sig["entry_price"]
            size = sig["virtual_size_usdc"]
            payout = (1.0 if won else 0.0) * (size / entry)  # virt shares
            cost = size
            pnl = round(payout - cost, 4)
            sig["status"] = "resolved"
            sig["ts_close"] = datetime.now(timezone.utc).isoformat()
            sig["resolved"] = up_won
            sig["win"] = won
            sig["virtual_pnl"] = pnl
            self._update_signal(sig)
            self.pending.pop(cid, None)
            # update stats
            self.stats["n_resolved"] += 1
            self.stats["virtual_pnl"] = round(self.stats["virtual_pnl"] + pnl, 4)
            if won:
                self.stats["n_win"] += 1
            abs_edge = abs(sig["edge"])
            if abs_edge < 0.20:
                bk = "win_10_20"
            else:
                bk = "win_20_plus"
            self.stats["by_edge_bucket"][bk][0] += (1 if won else 0)
            self.stats["by_edge_bucket"][bk][1] += 1
            self.stats["by_edge_bucket"]["all"][0] += (1 if won else 0)
            self.stats["by_edge_bucket"]["all"][1] += 1
            self._save_stats()
            wr = self.stats["n_win"] / max(1, self.stats["n_resolved"])
            print(f"[RESOLVE] {action} {'WIN ' if won else 'LOSS'} | edge={sig['edge']:+.2f} | "
                  f"pnl={pnl:+.4f} | tol WR={wr*100:.1f}% "
                  f"({self.stats['n_win']}/{self.stats['n_resolved']})")

    def heartbeat_save_stats(self):
        """Save stats.json anche senza nuovi RESOLVE — cosi pending/n_resolved e'
        visibile da cat senza RESOLVE. Chiamato dal loop ogni ~60s.
        Fix Bug #3: stats_file prima d'ora mai scritto fino al primo RESOLVE."""
        # include pending count e ts per audit
        self.stats["pending"] = len(self.pending)
        self.stats["ts_last_save"] = datetime.now(timezone.utc).isoformat()
        self._save_stats()

    def print_stats(self):
        n_res = self.stats["n_resolved"]
        wr = self.stats["n_win"] / n_res if n_res > 0 else 0.0
        pnl = self.stats["virtual_pnl"]
        pending = len(self.pending)
        print(f"\n[LATENCY-ARB STATS] resolved={n_res} | WR={wr*100:.1f}% | "
              f"P&L virt=${pnl:.3f} | pending={pending}")
        if n_res > 0:
            for bucket, (w, n) in self.stats["by_edge_bucket"].items():
                if n > 0:
                    bwr = w / n
                    print(f"  bucket {bucket}: {w}/{n} = {bwr*100:.1f}% WR")
        print()


def run_loop(detector: Optional[LatencyArbDetector] = None,
             duration_min: float = 0.0):
    """Loop principale. duration_min=0 → run forever."""
    det = detector or LatencyArbDetector()
    interval = LATENCY_ARB["poll_interval_sec"]
    start = time.time()
    stats_every = 60  # stampa stats ogni 60 cicli (~1min)
    save_every = 60   # persist stats.json ogni 60 cicli (anche se 0 resolved)
    cycle = 0
    print(f"[LATENCY-ARB] loop start | poll={interval}s | "
          f"edge_threshold={LATENCY_ARB['edge_threshold_pct']} | "
          f"K={LATENCY_ARB['momentum_k']} | "
          f"log={'forever' if duration_min <= 0 else f'{duration_min}min'}")
    try:
        while True:
            try:
                n = det.scan_cycle()
                cycle += 1
                if cycle % stats_every == 0:
                    det.print_stats()
                if cycle % save_every == 0:
                    det.heartbeat_save_stats()
                if duration_min > 0 and time.time() - start > duration_min * 60:
                    print("[LATENCY-ARB] duration reached, stop.")
                    det.print_stats()
                    break
            except Exception as e:
                print(f"[LATENCY-ARB] cycle error: {e}")
                import traceback; traceback.print_exc()
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[LATENCY-ARB] stop requested")
        det.print_stats()


def _cli_runner():
    """Entry point: python src/latency_arb.py — run isolation loop."""
    print("[LATENCY-ARB] Step 0 validator: NO orders, feed reali, P&L virtual.")
    run_loop()


if __name__ == "__main__":
    _cli_runner()