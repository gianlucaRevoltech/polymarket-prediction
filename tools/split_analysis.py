#!/usr/bin/env python3
"""Join [SIGNAL] -> [RESOLVE] per estimare WR per asset (BTC vs ETH)
e combinazioni asset x direzione.

Strategia: per ogni RESOLVE troviamo la SIGNAL precedente ancora unmatched
con stessa (action, edge). Greedy backward match.
"""
import re
import sys
from collections import defaultdict

LOG = "logs_weekend/latency_arb.log"

sig_pat = re.compile(r"\[SIGNAL\] (LONG_YES|LONG_NO) \| (.+?) \| edge=([\+\-][\d\.]+)")
res_pat = re.compile(r"\[RESOLVE\] (LONG_YES|LONG_NO) (WIN|LOSS)\s+\| edge=([\+\-][\d\.]+) \| pnl=([\+\-][\d\.]+)")

events = []  # lista di dict in ordine temporale

with open(LOG, "r", encoding="utf-8", errors="replace") as f:
    for line in f:
        m = sig_pat.search(line)
        if m:
            action, title, edge = m.groups()
            edge = float(edge)
            asset = "BTC" if "Bitcoin" in title else ("ETH" if "Ethereum" in title else "OTHER")
            events.append({"type": "S", "action": action, "asset": asset, "edge": round(edge, 2), "edge_raw": edge})
            continue
        m = res_pat.search(line)
        if m:
            action, outcome, edge, pnl = m.groups()
            edge = float(edge)
            events.append({"type": "R", "action": action, "edge": round(edge, 2), "edge_raw": edge, "win": outcome == "WIN"})

# Greedy backward match: per ogni RESOLVE dalla prima all'ultima, prendi la SIGNAL più recente non matchata con stesso (action, edge)
available = []  # stack di SIGNAL unmatched, in ordine di apparizione (più recenti in fondo)
stats = defaultdict(lambda: {"n": 0, "win": 0})

total_matched = 0
total_unmatched = 0

for ev in events:
    if ev["type"] == "S":
        available.append(ev)
    else:  # RESOLVE
        # cerca più recente disponibile con stesso (action, edge)
        idx = None
        for i in range(len(available) - 1, -1, -1):
            if available[i]["action"] == ev["action"] and available[i]["edge"] == ev["edge"]:
                idx = i
                break
        if idx is None:
            # fallback match solo per action (ignore edge se collide)
            for i in range(len(available) - 1, -1, -1):
                if available[i]["action"] == ev["action"]:
                    idx = i
                    break
        if idx is None:
            total_unmatched += 1
            continue
        sig = available.pop(idx)
        asset = sig["asset"]
        key = (asset, ev["action"])
        stats[key]["n"] += 1
        if ev["win"]:
            stats[key]["win"] += 1
        total_matched += 1

# ricalcola aggregate
agg_asset = defaultdict(lambda: {"n": 0, "win": 0})
agg_action = defaultdict(lambda: {"n": 0, "win": 0})
for key, v in stats.items():
    asset, action = key
    agg_asset[asset]["n"] += v["n"]
    agg_asset[asset]["win"] += v["win"]
    agg_action[action]["n"] += v["n"]
    agg_action[action]["win"] += v["win"]

print(f"=== MATCH STATS ===")
print(f"matched: {total_matched}  unmatched: {total_unmatched}")
print()
print("=== Split per ASSET (BTC vs ETH) ===")
for a in ["BTC", "ETH", "OTHER"]:
    v = agg_asset.get(a, {"n": 0, "win": 0})
    wr = 100.0 * v["win"] / v["n"] if v["n"] else 0
    print(f"  {a:6s}: n={v['n']:4d}  win={v['win']:3d}  WR={wr:5.1f}%")
print()
print("=== Split per DIREZIONE (LONG_YES vs LONG_NO) ===")
for a in ["LONG_YES", "LONG_NO"]:
    v = agg_action.get(a, {"n": 0, "win": 0})
    wr = 100.0 * v["win"] / v["n"] if v["n"] else 0
    print(f"  {a:9s}: n={v['n']:4d}  win={v['win']:3d}  WR={wr:5.1f}%")
print()
print("=== Matrice ASSET x DIREZIONE ===")
for a in ["BTC", "ETH"]:
    for d in ["LONG_YES", "LONG_NO"]:
        v = stats.get((a, d), {"n": 0, "win": 0})
        wr = 100.0 * v["win"] / v["n"] if v["n"] else 0
        print(f"  {a:5s} {d:9s}: n={v['n']:4d}  win={v['win']:3d}  WR={wr:5.1f}%")