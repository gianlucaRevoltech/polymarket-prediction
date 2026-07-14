"""Diagnostica Step 0 latency arb (esegui sulla VPS).

Uso:
    cd /root/polymarket-prediction
    PYTHONPATH=src ./venv/bin/python scripts/diag_latency_arb.py

Risponde alla domanda: perche' pending=0 sempre?
    - CONTRACTS 0  -> problema discovery (pattern errato / niente crypto 5-15min)
    - CONTRACTS N>0 ma nessun signal -> problemi edge (momentum basso / threshold)
    - Binance prices None -> endpoint Binance bloccato
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

from latency_arb import PolymarketContractFeed, BinanceFeed, LATENCY_ARB

print("=== PATTERNS:", LATENCY_ARB["contract_patterns"])
print("=== WIN min/max min:", LATENCY_ARB["min_minutes_to_expiry"], "-",
      LATENCY_ARB["max_minutes_to_expiry"])
print("=== edge_threshold_pct:", LATENCY_ARB["edge_threshold_pct"])
print()

pf = PolymarketContractFeed()
cs = pf.active_contracts()
print("=== CONTRACTS FOUND:", len(cs))
for c in cs[:25]:
    print("  %.1fmin | %s | yes=%s..." % (c["minutes_left"],
                                           c["title"][:60],
                                           str(c["tokens"][0])[:12]))
print()

b = BinanceFeed()
print("=== BINANCE prices:", b.last_prices())
for sym in ["BTCUSDT", "ETHUSDT"]:
    mom = b.momentum(sym)
    if mom is None:
        print("  %s momentum: None (klines non disponibili)" % sym)
    else:
        d5, p = mom
        print("  %s momentum: delta5m=%+.3f%% price=%.2f" % (sym, d5 * 100, p))
        # mostra expected_p(UP) vs 0.5 per capire se genera edge
        K = LATENCY_ARB["momentum_k"]
        exp = 0.5 + K * d5
        exp = max(0.05, min(0.95, exp))
        print("    -> expected_p(UP)=%.3f, edge vs p_yes=0.5 = %+.3f (thres %.2f)" %
              (exp, exp - 0.5, LATENCY_ARB["edge_threshold_pct"]))
print()

print("=== DIAGNOSTICA EDGE per ogni contratto ===")
prices = b.last_prices()
for c in cs[:25]:
    sym = b.symbol_for_contract(c["title"])
    if not sym or sym not in prices:
        print("  %s: nessun symbol Binance match" % c["title"][:50])
        continue
    mom = b.momentum(sym)
    if mom is None:
        print("  %s: momentum None" % c["title"][:50])
        continue
    d5, pb = mom
    p_yes = pf.book_yes(c["tokens"][0])
    if p_yes is None:
        print("  %s: book_yes None (mid non disponibile)" % c["title"][:50])
        continue
    K = LATENCY_ARB["momentum_k"]
    exp = 0.5 + K * d5
    exp = max(0.05, min(0.95, exp))
    edge = exp - p_yes
    flag = "SIGNAL!!" if abs(edge) >= LATENCY_ARB["edge_threshold_pct"] else "skip"
    print("  %+.3f edge=%+.3f p_yes=%.3f | %s" %
          (d5 * 100, edge, p_yes, c["title"][:50]))
    print("      | %s" % flag)

# ---------------------------------------------------------------------------
# DIAGNOSTICA AVANZATA: capiamo perche' CONTRACTS=0
# ---------------------------------------------------------------------------
import requests as _rq
from latency_arb import _parse_json_list, _days_to_expiry

from config import POLYMARKET_API
gamma_url = POLYMARKET_API["gamma"]
s = pf.s
r = s.get(f"{gamma_url}/markets",
         params={"closed": "false", "active": "true",
                 "order": "volumeNum", "ascending": "false",
                 "limit": 500}, timeout=20)
print("=== GAMMA raw response ok:", r.ok, "status:", r.status_code, "len:", len(r.text))
all_m = r.json() if r.ok else []
print("=== GAMMA total markets:", len(all_m))

# (1) Quanti matchano il pattern (senza filtro scadenza)
match_pattern = 0
samples = []
for m in all_m:
    t = (m.get("question") or m.get("title") or "").lower()
    if any(p in t for p in LATENCY_ARB["contract_patterns"]):
        match_pattern += 1
        if len(samples) < 10:
            end = m.get("endDate", "")
            days = _days_to_expiry(end)
            mins = (days * 1440.0) if days is not None else None
            samples.append((t[:70], str(end), mins))
print("=== MATCH PATTERN (qualsiasi scadenza):", match_pattern)
for t, end, mins in samples:
    print("  [%s min] %s" % ("?" if mins is None else "%.1f" % mins, t))
print()

# (2) Titoli che parlano di bitcoin/btc/eth/ethereum (pattern vasto)
crypto_broad = 0
broad_samples = []
for m in all_m:
    t = (m.get("question") or m.get("title") or "").lower()
    if any(k in t for k in ["bitcoin", "btc", "ethereum", "eth"]):
        crypto_broad += 1
        if len(broad_samples) < 15:
            end = m.get("endDate", "")
            days = _days_to_expiry(end)
            mins = (days * 1440.0) if days is not None else None
            broad_samples.append((t[:70], str(end), mins))
print("=== CRYPTO BROAD (bitcoin/btc/eth/ethereum):", crypto_broad)
for t, end, mins in broad_samples:
    print("  [%s min] %s end=%s" % ("?" if mins is None else "%.1f" % mins, t, end[:19]))
print()

# (3) Qualsiasi mercato con scadenza < 15 min (indipendentemente dal pattern)
short_expiry = 0
short_samples = []
for m in all_m:
    end = m.get("endDate", "")
    days = _days_to_expiry(end)
    if days is None:
        continue
    mins = days * 1440.0
    if 0 <= mins <= 15:
        short_expiry += 1
        if len(short_samples) < 15:
            t = (m.get("question") or m.get("title") or "")[:70]
            short_samples.append((mins, t))
print("=== SHORT EXPIRY (<=15min, qualsiasi mercato):", short_expiry)
for mins, t in short_samples:
    print("  [%.1f min] %s" % (mins, t))
print()

# (4) Probe diretto: search by slug "bitcoin-up-or-down"
print("=== PROBE slug 'bitcoin-up-or-down' ===")
try:
    r2 = s.get(f"{gamma_url}/markets",
              params={"slug": "bitcoin-up-or-down"}, timeout=10)
    print("  status:", r2.status_code, "len:", len(r2.text))
    if r2.ok:
        print("  body[:,400]:", r2.text[:400])
except Exception as ex:
    print("  error:", ex)

print("\n=== FINE DIAGNOSTICA ===")