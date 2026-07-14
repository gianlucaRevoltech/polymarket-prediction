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

print("\n=== FINE DIAGNOSTICA ===")