"""
Step 0 v3 — Latency Arbitrage Detector (validazione PAPER su feed REALI)

Modulo standalone di validazione: NON piazza ordini, NON tocca il portfolio.

=== PERCHE' v3 (2026-07-22) ===
L'audit del run v2 (738 resolved, +$100 netto apparente) ha dimostrato che il
P&L era un ARTEFATTO DI MISURA, non edge:
1. `best_ask()` usava CLOB /price?side=buy che ritorna il best BID, non il
   best ask (docs ufficiali: "Returns the best bid price for BUY side").
   Compravamo simulando il prezzo del lato opposto del book: 1-2 cent meglio
   della realta' per leg. Prova empirica: ask_up+ask_down < 1 nel 97.8% dei
   record (somma di BID, non di ask — la somma di veri ask non puo' stare
   sotto 1 in modo persistente).
2. P&L concentrato nei longshot: top-5 trade = 62% del totale; P&L TRIMMED
   (senza top 5% win) = -$161. 44 win a entry<0.25 = +$284 (283% del totale).
3. WR reale < p_model in TUTTI i bucket reliability (selezione avversa da
   strike impreciso: per crypto 5/15min lo strike vero e' il prezzo CHAINLINK
   a inizio finestra — l'endpoint equity price-to-beat NON esiste per crypto;
   il fallback open Binance 1m ha basis di bps, fatale con |S/K|<0.01%).

Fix v3:
- entry ONESTA dal book reale: GET /book, best ask = min(asks), con check
  size sufficiente. NESSUN fallback midpoint (skip se book vuoto).
- log anche best bid (audit spread) nel record.
- min_entry_price 0.03 -> 0.15 (i longshot dominavano l'artefatto convesso)
- edge_threshold 0.10 -> 0.15 (bucket 15+ gli unici col profilo sano)
- skip segnali con |S/K - 1| < 0.03% (zona in cui l'errore di basis del
  fallback strike Binance ribalta il modello)

=== PERCHE' v2 (2026-07-20) ===
Il modello v1 (`expected_up = 0.5 + K*delta_5m`, K=2) era matematicamente
inerte: con delta 5min tipico ±0.2% lo shift era ±0.004 contro una soglia di
0.10. Il segnale scattava SOLO quando il prezzo Polymarket si allontanava da
0.50, e il bot comprava il lato opposto — cioe' scommetteva CONTRO il flusso
informato. Risultato weekend: 396 resolved, WR 34.6% ~ prezzo medio di
entrata, P&L virtuale -$17. Il mercato era prezzato bene; noi no.

=== MODELLO v2 ===
I contratti "BTC/ETH Up or Down Nmin" risolvono UP se prezzo_fine >=
prezzo_inizio finestra (strike), fonte Chainlink. La probabilita' vera di UP
dipende da DOVE sta il prezzo rispetto allo strike e da quanto tempo manca:

    p_up = Phi( ln(S_now / strike) / (sigma_sec * sqrt(tau_sec)) )

  - S_now: prezzo live Binance (proxy del feed Chainlink, basis ~bps)
  - strike: "price to beat" ufficiale Polymarket (endpoint equity API),
    fallback open del candle Binance 1m all'inizio finestra
  - sigma_sec: vol per-secondo stimata dai log-return 1m (lookback 30min)
  - tau_sec: secondi alla scadenza

Signal se |p_model - best_ask| > soglia, SOLO negli ultimi 1-3 minuti del
contratto (dove il modello strike+vol e' affidabile e il lag di Polymarket
vale di piu'). Entry simulata al BEST ASK (taker realistico) con taker fee
crypto ufficiale sottratta nel P&L netto:

    fee = shares * 0.07 * p * (1-p)     [docs.polymarket.com/trading/fees]

NESSUN ordine viene piazzato. NESSUN capitale toccato. Solo scienza.

Lancio (su VPS Linux, non da Windows locale — gamma geo-blockata):
    python src/latency_arb.py
oppure (background):
    nohup python -u src/latency_arb.py > logs/latency_arb.log 2>&1 &

Output:
    data/latency_arb_signals.jsonl  - 1 evento per signal aperto/risolto
    data/latency_arb_stats.json     - stats rollup (WR, P&L lordo/netto)
"""
import json
import math
import os
import re
import time
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
# Config Step 0 v2 (validazione) — tweakabili senza restart per tuning veloce
# ---------------------------------------------------------------------------
LATENCY_ARB = {
    "enabled": True,
    # loop cadenza 1s per VALIDATION. In real Step 1 diverrebbe 100-200ms WS.
    "poll_interval_sec": 1.0,
    # contratti Polymarket di interesse: pattern titolo/slug
    "contract_patterns": [
        "bitcoin up or down", "btc up or down",
        "ethereum up or down", "eth up or down",
        "bitcoin price up or down", "ethereum price up or down",
    ],
    # v2: finestra operativa RISTRETTA agli ultimi minuti del contratto.
    # Il modello strike+vol e' affidabile solo vicino a scadenza, ed e' li'
    # che il lag di Polymarket vale (l'esito e' quasi noto, il book no).
    "max_minutes_to_expiry": 3.0,
    "min_minutes_to_expiry": 0.5,   # no entry agli ultimi secondi
    # edge threshold per flag signal: p_model - best_ask (in prob points)
    # v3: 0.10 -> 0.15 (audit v2: bucket 10-15 EV/trade ~0, bucket 15+ sani)
    "edge_threshold_pct": 0.15,
    # vol: lookback in minuti per la stima sigma dai log-return 1m Binance
    "vol_lookback_min": 30,
    # floor sigma 1m (0.005% = mercato morto -> p_model estremo inaffidabile)
    "min_sigma_1m": 5e-5,
    # clamp del p_model (mai certezza assoluta: basis Binance/Chainlink ~bps)
    "p_model_clamp": (0.02, 0.98),
    # entry: max prezzo ask accettato (sopra non c'e' spazio di profitto)
    # v3: min 0.03 -> 0.15 (audit v2: tutto il P&L apparente era in 44 win
    # longshot entry<0.25 = payout convesso su fill irrealistici)
    "max_entry_price": 0.85,
    "min_entry_price": 0.15,
    # v3: size minima del best ask perche' il fill $1 virtuale sia credibile
    "min_ask_size_shares": 5.0,
    # v3: skip se |S/K - 1| sotto questa soglia (basis Binance/Chainlink ~bps
    # ribalta il modello quando lo spot e' attaccato allo strike)
    "min_dist_from_strike_pct": 0.03,
    # taker fee crypto ufficiale (docs.polymarket.com/trading/fees, lug 2026)
    # fee = shares * rate * p * (1-p)
    "taker_fee_rate": 0.07,
    # virtual sizing per calcolo P&L fittizio ($1 investito per signal)
    "virtual_size_usdc": 1.0,
    # log file paths
    "signals_file": "data/latency_arb_signals.jsonl",
    "stats_file": "data/latency_arb_stats.json",
    # binance endpoints (public, no key)
    "binance_base": "https://api.binance.com",
    "binance_symbols": ["BTCUSDT", "ETHUSDT"],
    # fetch klines Binance ogni N secondi (tickers ogni ciclo)
    "klines_every_sec": 5,
}


def _norm_cdf(x: float) -> float:
    """CDF normale standard via erf (niente scipy)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def taker_fee_usdc(size_usdc: float, entry_price: float,
                   rate: float = LATENCY_ARB["taker_fee_rate"]) -> float:
    """Taker fee ufficiale: fee = shares * rate * p * (1-p).

    Con shares = size/entry si semplifica: fee = size * rate * (1-entry)."""
    if entry_price <= 0.0 or entry_price >= 1.0:
        return 0.0
    return size_usdc * rate * (1.0 - entry_price)


# ---------------------------------------------------------------------------
# Binance feed: prezzo live + volatilita' + open storico via REST
# ---------------------------------------------------------------------------
class BinanceFeed:
    """REST polling Binance public market data. No API key required."""

    def __init__(self, base: str = LATENCY_ARB["binance_base"]):
        self.base = base
        self.symbols = list(LATENCY_ARB["binance_symbols"])
        self.s = requests.Session()
        self.s.verify = _SSL_VERIFY
        self.s.headers.update({"User-Agent": "latency-arb-validator/0.2"})
        # sym -> (delta_5m_pct, sigma_1m, last_price)
        self._klines_stats: Dict[str, Tuple[float, float, float]] = {}
        self._last_klines_ts: float = 0.0
        # cache open-at-minute per strike fallback: (sym, minute_ts) -> open
        self._open_cache: Dict[Tuple[str, int], float] = {}

    def last_prices(self) -> Dict[str, float]:
        """Prezzi istantanei (una sola chiamata ticker/price per tutti)."""
        try:
            r = self.s.get(f"{self.base}/api/v3/ticker/price", timeout=3)
            if not r.ok:
                return {}
            data = r.json()
            return {d["symbol"]: float(d["price"]) for d in data
                    if d["symbol"] in self.symbols}
        except Exception as e:
            print(f"[BINANCE] ticker error: {e}")
            return {}

    def _refresh_klines(self):
        """Aggiorna delta 5min + sigma 1m per symbol (lookback vol_lookback_min)."""
        now_ts = time.time()
        if now_ts - self._last_klines_ts < LATENCY_ARB["klines_every_sec"]:
            return
        self._last_klines_ts = now_ts
        lookback = max(6, int(LATENCY_ARB["vol_lookback_min"]) + 1)
        for sym in self.symbols:
            try:
                r = self.s.get(f"{self.base}/api/v3/klines",
                               params={"symbol": sym, "interval": "1m",
                                       "limit": lookback},
                               timeout=4)
                if not r.ok:
                    continue
                rows = r.json()
                if len(rows) < 6:
                    continue
                closes = [float(row[4]) for row in rows]
                # delta 5min: close corrente vs open di 5 candle fa
                price_5m_ago = float(rows[-6][1])
                price_now = closes[-1]
                delta_5m = ((price_now - price_5m_ago) / price_5m_ago
                            if price_5m_ago > 0 else 0.0)
                # sigma 1m: std dei log-return 1m sul lookback
                rets = []
                for i in range(1, len(closes)):
                    if closes[i - 1] > 0 and closes[i] > 0:
                        rets.append(math.log(closes[i] / closes[i - 1]))
                if len(rets) >= 5:
                    mean = sum(rets) / len(rets)
                    var = sum((x - mean) ** 2 for x in rets) / max(1, len(rets) - 1)
                    sigma_1m = math.sqrt(var)
                else:
                    sigma_1m = 0.0
                self._klines_stats[sym] = (delta_5m, sigma_1m, price_now)
            except Exception:
                continue

    def stats(self, symbol: str) -> Optional[Tuple[float, float, float]]:
        """Return (delta_5m_pct, sigma_1m, last_price_kline) o None."""
        self._refresh_klines()
        return self._klines_stats.get(symbol)

    def open_at_minute(self, symbol: str, ts_utc: datetime) -> Optional[float]:
        """Open del candle 1m che contiene ts_utc — proxy dello strike quando
        il price-to-beat ufficiale non e' disponibile. Cached per (sym, min)."""
        minute_ms = int(ts_utc.timestamp() // 60) * 60 * 1000
        key = (symbol, minute_ms)
        if key in self._open_cache:
            return self._open_cache[key]
        try:
            r = self.s.get(f"{self.base}/api/v3/klines",
                           params={"symbol": symbol, "interval": "1m",
                                   "startTime": minute_ms, "limit": 1},
                           timeout=4)
            if not r.ok:
                return None
            rows = r.json()
            if not rows:
                return None
            open_price = float(rows[0][1])
            self._open_cache[key] = open_price
            # cache bounded (contratti 5/15min: bastano poche entry)
            if len(self._open_cache) > 200:
                self._open_cache.pop(next(iter(self._open_cache)))
            return open_price
        except Exception:
            return None

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

_WINDOW_MIN_RE = re.compile(r"(\d+)\s*-?\s*m(?:in)?\b")


def _window_minutes_from(*texts) -> Optional[int]:
    """Estrae la durata finestra (5/15/60 min) da slug/titolo tipo
    'btc-updown-5m-...' o 'Bitcoin Up or Down 15min'."""
    for t in texts:
        if not t:
            continue
        m = _WINDOW_MIN_RE.search(str(t).lower())
        if m:
            try:
                v = int(m.group(1))
                if 1 <= v <= 120:
                    return v
            except Exception:
                pass
    return None


class PolymarketContractFeed:
    """Recupera contratti crypto up/down attivi via gamma API."""

    def __init__(self):
        self.gamma = POLYMARKET_API["gamma"]
        self.clob = POLYMARKET_API["clob"]
        self.equity_api = "https://polymarket.com/api/equity"
        self.s = requests.Session()
        self.s.verify = _SSL_VERIFY
        # User-Agent completa mitigando filtri geo/anti-bot di Cloudflare
        self.s.headers.update({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
                                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                                          "Chrome/120.0.0.0 Safari/537.36"})
        self._contracts: List[Dict] = []
        self._fetch_ts: float = 0.0
        # strike cache: condition_id -> (strike, source)
        self._strike_cache: Dict[str, Tuple[float, str]] = {}

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

    # ----- prezzi book CLOB --------------------------------------------------
    def midpoint(self, token_id: str) -> Optional[float]:
        """Midpoint del book per il token."""
        try:
            r = self.s.get(f"{self.clob}/midpoint",
                           params={"token_id": token_id}, timeout=5)
            if not r.ok:
                return None
            mid = r.json().get("mid")
            return float(mid) if mid is not None else None
        except Exception:
            return None

    # alias legacy (debug_resolver/diag importano book_yes)
    def book_yes(self, token_yes: str) -> Optional[float]:
        return self.midpoint(token_yes)

    def best_ask(self, token_id: str) -> Optional[Tuple[float, float, Optional[float]]]:
        """v3: (best_ask, ask_size, best_bid) dal book REALE via CLOB /book.

        BUG v2: /price?side=buy ritorna il best BID (docs: "Returns the best
        bid price for BUY side"), non il prezzo a cui un taker compra. Tutto
        il P&L v2 era simulato comprando al bid (1-2 cent meglio del reale).
        Qui leggiamo il book intero e prendiamo il min degli ask (robusto a
        qualsiasi ordinamento). NESSUN fallback midpoint: se il book manca,
        il segnale si salta — un fill non quotato non e' un fill.
        """
        try:
            r = self.s.get(f"{self.clob}/book",
                           params={"token_id": token_id}, timeout=5)
            if not r.ok:
                return None
            b = r.json()
            asks = b.get("asks") or []
            bids = b.get("bids") or []
            best = None
            for lvl in asks:
                try:
                    p = float(lvl.get("price"))
                    sz = float(lvl.get("size"))
                except Exception:
                    continue
                if 0.0 < p < 1.0 and sz > 0 and (best is None or p < best[0]):
                    best = (p, sz)
            if best is None:
                return None
            best_bid = None
            for lvl in bids:
                try:
                    p = float(lvl.get("price"))
                except Exception:
                    continue
                if 0.0 < p < 1.0 and (best_bid is None or p > best_bid):
                    best_bid = p
            return (best[0], best[1], best_bid)
        except Exception:
            return None

    # ----- strike ("price to beat") ------------------------------------------
    def price_to_beat(self, contract: Dict) -> Optional[Tuple[float, str]]:
        """Strike ufficiale del contratto via equity API Polymarket.

        GET https://polymarket.com/api/equity/price-to-beat/{slug}
        Prova slug del market, poi slug dell'evento. Cache per condition_id.
        Return (strike, source) o None."""
        cid = contract.get("condition_id", "")
        if cid in self._strike_cache:
            return self._strike_cache[cid]
        for slug in (contract.get("slug"), contract.get("event_slug")):
            if not slug:
                continue
            try:
                r = self.s.get(f"{self.equity_api}/price-to-beat/{slug}",
                               timeout=5)
                if not r.ok:
                    continue
                data = r.json()
                strike = _extract_price_value(data)
                if strike is not None and strike > 0:
                    entry = (strike, f"equity_api:{slug}")
                    self._strike_cache[cid] = entry
                    return entry
            except Exception:
                continue
        return None

    def cache_strike(self, condition_id: str, strike: float, source: str):
        self._strike_cache[condition_id] = (strike, source)
        if len(self._strike_cache) > 500:
            self._strike_cache.pop(next(iter(self._strike_cache)))

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
        (direzionale, niente filtri gamma rotti). Fallback gamma ?id=<int> se
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


def _extract_price_value(data) -> Optional[float]:
    """Estrae un valore prezzo da risposte API dal formato non garantito:
    numero secco, {"price": x}, {"priceToBeat": x}, {"data": {...}}, ecc."""
    if data is None:
        return None
    if isinstance(data, (int, float)):
        return float(data)
    if isinstance(data, str):
        try:
            return float(data)
        except Exception:
            return None
    if isinstance(data, dict):
        for k in ("priceToBeat", "price_to_beat", "price", "value",
                  "openPrice", "open_price", "strike"):
            if k in data:
                return _extract_price_value(data[k])
        if "data" in data:
            return _extract_price_value(data["data"])
    return None


def _find_outcome_idx(outcomes, needles) -> Optional[int]:
    """Trova l'index di outcomes il cui nome (lowercased) contiene uno dei
    needles. None se niente matcha."""
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


def _parse_iso(dt_iso: str) -> Optional[datetime]:
    try:
        s = dt_iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Detector principale: modello strike+vol, log signals, resolve virt
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
        self.stats = {"model_version": 3,
                      "n_signals": 0, "n_resolved": 0, "n_win": 0,
                      "virtual_pnl": 0.0,       # lordo
                      "virtual_pnl_net": 0.0,   # netto taker fee
                      "fees_paid": 0.0,
                      "by_edge_bucket": {"win_10_20": [0, 0],
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

    # ---------------- strike -------------------------------------------------
    def _get_strike(self, c: Dict, sym: str) -> Optional[Tuple[float, str]]:
        """Strike del contratto: 1) equity API ufficiale, 2) fallback open del
        candle Binance 1m all'inizio finestra (richiede durata nota)."""
        got = self.poly.price_to_beat(c)
        if got is not None:
            return got
        # fallback: window_start = end_date - durata finestra
        window_min = _window_minutes_from(c.get("slug"), c.get("event_slug"),
                                          c.get("title"))
        if window_min is None:
            return None
        end_dt = _parse_iso(c.get("end_date", ""))
        if end_dt is None:
            return None
        start_dt = end_dt - timedelta(minutes=window_min)
        # se la finestra non e' ancora iniziata (improbabile: siamo <3min da
        # scadenza) o start nel futuro, skip
        if start_dt > datetime.now(timezone.utc):
            return None
        open_price = self.binance.open_at_minute(sym, start_dt)
        if open_price is None or open_price <= 0:
            return None
        entry = (open_price, f"binance_open:{window_min}m")
        self.poly.cache_strike(c.get("condition_id", ""), *entry)
        return entry

    # ---------------- detector ----------------------------------------------
    def scan_cycle(self) -> int:
        """Un ciclo di detection. Ritorna numero di nuovi signal aperti."""
        contracts = self.poly.active_contracts()
        if not contracts:
            self._resolve_pending()
            return 0
        # prezzi spot freschi, una sola chiamata per ciclo (ticker, non kline)
        prices = self.binance.last_prices()
        n_open = 0
        for c in contracts:
            cid = c["condition_id"]
            if cid in self.pending:
                continue  # gia' signal aperto per questo contract
            sym = self.binance.symbol_for_contract(c["title"])
            if not sym:
                continue
            kstats = self.binance.stats(sym)
            if kstats is None:
                continue
            delta_5m, sigma_1m, _ = kstats
            if sigma_1m < LATENCY_ARB["min_sigma_1m"]:
                continue  # vol morta: p_model inaffidabile
            s_now = prices.get(sym)
            if s_now is None or s_now <= 0:
                continue
            # strike ufficiale (o fallback open Binance a inizio finestra)
            strike_got = self._get_strike(c, sym)
            if strike_got is None:
                continue
            strike, strike_source = strike_got
            # tempo residuo in secondi
            days = _days_to_expiry(c.get("end_date", ""))
            if days is None or days <= 0:
                continue
            tau_sec = days * 86400.0
            if tau_sec < LATENCY_ARB["min_minutes_to_expiry"] * 60.0:
                continue
            # === MODELLO v2: p_up = Phi( ln(S/strike) / (sigma*sqrt(tau)) )
            sigma_sec = sigma_1m / math.sqrt(60.0)
            denom = sigma_sec * math.sqrt(tau_sec)
            if denom <= 0:
                continue
            dist_pct = (s_now / strike - 1.0) * 100.0
            # v3: zona morta attorno allo strike — la basis Binance/Chainlink
            # (bps) domina il segno del modello quando |S/K| e' minuscola
            if abs(dist_pct) < LATENCY_ARB["min_dist_from_strike_pct"]:
                continue
            z = math.log(s_now / strike) / denom
            p_model = _norm_cdf(z)
            lo, hi = LATENCY_ARB["p_model_clamp"]
            p_model = max(lo, min(hi, p_model))
            # outcomes per NOME (Polymarket ordina spesso alfabetico:
            # ["Down","Up"] -> tokens[0] e' DOWN)
            up_idx = _find_outcome_idx(c.get("outcomes") or [], ("up", "yes"))
            down_idx = _find_outcome_idx(c.get("outcomes") or [], ("down", "no"))
            if up_idx is None or down_idx is None or up_idx == down_idx:
                continue
            if len(c["tokens"]) <= max(up_idx, down_idx):
                continue
            token_up = c["tokens"][up_idx]
            token_down = c["tokens"][down_idx]
            # v3: entry ONESTA = best ask dal book reale (+ size + bid audit)
            book_up = self.poly.best_ask(token_up)
            book_down = self.poly.best_ask(token_down)
            min_sz = LATENCY_ARB["min_ask_size_shares"]
            ask_up = book_up[0] if (book_up and book_up[1] >= min_sz) else None
            ask_down = book_down[0] if (book_down and book_down[1] >= min_sz) else None
            # edge per lato = p_vittoria_modello - prezzo che pagheremmo
            edge_up = (p_model - ask_up) if ask_up else None
            edge_down = ((1.0 - p_model) - ask_down) if ask_down else None
            action = None
            entry_price = None
            edge = None
            thr = LATENCY_ARB["edge_threshold_pct"]
            # scegli il lato con edge maggiore sopra soglia
            if edge_up is not None and edge_up >= thr and \
                    (edge_down is None or edge_up >= edge_down):
                action, entry_price, edge = "LONG_YES", ask_up, edge_up
            elif edge_down is not None and edge_down >= thr:
                action, entry_price, edge = "LONG_NO", ask_down, edge_down
            if action is None:
                continue
            if not (LATENCY_ARB["min_entry_price"] <= entry_price
                    <= LATENCY_ARB["max_entry_price"]):
                continue
            size = LATENCY_ARB["virtual_size_usdc"]
            fee = taker_fee_usdc(size, entry_price)
            rec = {
                "ts_open": datetime.now(timezone.utc).isoformat(),
                "status": "open",
                "model_version": 3,
                "condition_id": cid,
                "market_id": c.get("market_id"),
                "title": c["title"],
                "slug": c.get("slug", ""),
                "end_date": c["end_date"],
                "minutes_left": round(days * 1440.0, 2),
                "symbol": sym,
                "binance_price": round(s_now, 2),
                "strike": round(strike, 2),
                "strike_source": strike_source,
                "dist_from_strike_pct": round((s_now / strike - 1.0) * 100, 4),
                "sigma_1m": round(sigma_1m, 6),
                "tau_sec": round(tau_sec, 1),
                "z_score": round(z, 3),
                "delta_5m_pct": round(delta_5m * 100, 3),
                "p_model_up": round(p_model, 4),
                "ask_up": round(ask_up, 4) if ask_up else None,
                "ask_down": round(ask_down, 4) if ask_down else None,
                # v3: audit book (bid + size del lato comprato)
                "bid_up": round(book_up[2], 4) if (book_up and book_up[2]) else None,
                "bid_down": round(book_down[2], 4) if (book_down and book_down[2]) else None,
                "ask_size_up": round(book_up[1], 2) if book_up else None,
                "ask_size_down": round(book_down[1], 2) if book_down else None,
                "up_idx": up_idx,
                "down_idx": down_idx,
                "outcomes": c.get("outcomes") or [],
                "edge": round(edge, 4),
                "action": action,
                "entry_price": round(entry_price, 4),
                "virtual_size_usdc": size,
                "fee_virtual": round(fee, 5),
            }
            self.pending[cid] = rec
            self._append_signal(rec)
            self.stats["n_signals"] = self.stats.get("n_signals", 0) + 1
            n_open += 1
            print(f"[SIGNAL] {action} | {c['title'][:45]} | edge={edge:+.2f} | "
                  f"p_model={p_model:.3f} | entry={entry_price:.3f} | "
                  f"S/K={rec['dist_from_strike_pct']:+.3f}% | "
                  f"tau={tau_sec:.0f}s | strike_src={strike_source.split(':')[0]}")
        # risolvi pending
        self._resolve_pending()
        return n_open

    def _resolve_pending(self):
        if not self.pending:
            return
        for cid, sig in list(self.pending.items()):
            days = _days_to_expiry(sig.get("end_date", ""))
            if days is None or days > 0:
                continue  # ancora non scaduto
            # scaduto: prova a leggere il risultato (passa market_id se noto)
            result = self.poly.resolve_contract(cid, sig.get("market_id"))
            if result is None:
                # non ancora risolto; dopo ~10 min ancora nulla → stale
                stale = (datetime.now(timezone.utc) -
                         datetime.fromisoformat(sig["ts_open"])).total_seconds()
                if stale > 600:
                    sig["status"] = "stale"
                    sig["ts_close"] = datetime.now(timezone.utc).isoformat()
                    sig["resolved"] = None
                    sig["virtual_pnl"] = 0.0
                    sig["virtual_pnl_net"] = 0.0
                    sig["win"] = None
                    self._update_signal(sig)
                    self.pending.pop(cid, None)
                continue
            # risultato: True=UP won, False=DOWN won
            up_won = bool(result)
            action = sig["action"]
            won = (action == "LONG_YES" and up_won) or \
                  (action == "LONG_NO" and not up_won)
            entry = sig["entry_price"]
            size = sig["virtual_size_usdc"]
            fee = float(sig.get("fee_virtual") or taker_fee_usdc(size, entry))
            payout = (1.0 if won else 0.0) * (size / entry)  # virt shares
            pnl = round(payout - size, 4)          # lordo
            pnl_net = round(pnl - fee, 4)          # netto taker fee
            sig["status"] = "resolved"
            sig["ts_close"] = datetime.now(timezone.utc).isoformat()
            sig["resolved"] = up_won
            sig["win"] = won
            sig["virtual_pnl"] = pnl
            sig["virtual_pnl_net"] = pnl_net
            self._update_signal(sig)
            self.pending.pop(cid, None)
            # update stats
            self.stats["n_resolved"] += 1
            self.stats["virtual_pnl"] = round(
                self.stats["virtual_pnl"] + pnl, 4)
            self.stats["virtual_pnl_net"] = round(
                self.stats.get("virtual_pnl_net", 0.0) + pnl_net, 4)
            self.stats["fees_paid"] = round(
                self.stats.get("fees_paid", 0.0) + fee, 4)
            if won:
                self.stats["n_win"] += 1
            abs_edge = abs(sig["edge"])
            bk = "win_10_20" if abs_edge < 0.20 else "win_20_plus"
            self.stats["by_edge_bucket"][bk][0] += (1 if won else 0)
            self.stats["by_edge_bucket"][bk][1] += 1
            self.stats["by_edge_bucket"]["all"][0] += (1 if won else 0)
            self.stats["by_edge_bucket"]["all"][1] += 1
            self._save_stats()
            wr = self.stats["n_win"] / max(1, self.stats["n_resolved"])
            print(f"[RESOLVE] {action} {'WIN ' if won else 'LOSS'} | "
                  f"edge={sig['edge']:+.2f} | pnl={pnl:+.4f} "
                  f"(net={pnl_net:+.4f}) | WR={wr*100:.1f}% "
                  f"({self.stats['n_win']}/{self.stats['n_resolved']})")

    def heartbeat_save_stats(self):
        """Save stats.json anche senza nuovi RESOLVE — cosi pending/n_resolved
        e' visibile da cat senza RESOLVE. Chiamato dal loop ogni ~60s."""
        self.stats["pending"] = len(self.pending)
        self.stats["ts_last_save"] = datetime.now(timezone.utc).isoformat()
        self._save_stats()

    def print_stats(self):
        n_res = self.stats["n_resolved"]
        wr = self.stats["n_win"] / n_res if n_res > 0 else 0.0
        pnl = self.stats["virtual_pnl"]
        pnl_net = self.stats.get("virtual_pnl_net", 0.0)
        pending = len(self.pending)
        print(f"\n[LATENCY-ARB STATS] v3 | resolved={n_res} | WR={wr*100:.1f}% | "
              f"P&L virt=${pnl:.3f} (net=${pnl_net:.3f}) | pending={pending}")
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
    print(f"[LATENCY-ARB] v3 loop start | poll={interval}s | "
          f"edge_threshold={LATENCY_ARB['edge_threshold_pct']} | "
          f"window={LATENCY_ARB['min_minutes_to_expiry']}-"
          f"{LATENCY_ARB['max_minutes_to_expiry']}min | "
          f"entry_band={LATENCY_ARB['min_entry_price']}-{LATENCY_ARB['max_entry_price']} | "
          f"fee_rate={LATENCY_ARB['taker_fee_rate']} | "
          f"log={'forever' if duration_min <= 0 else f'{duration_min}min'}")
    try:
        while True:
            try:
                det.scan_cycle()
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
    print("[LATENCY-ARB] Step 0 v3 validator: NO orders, feed reali, "
          "modello strike+vol, entry da book reale (fix bid-as-ask v2).")
    run_loop()


if __name__ == "__main__":
    _cli_runner()
