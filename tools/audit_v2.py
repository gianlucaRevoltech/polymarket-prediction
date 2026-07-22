#!/usr/bin/env python3
"""Audit profondo latency-arb v2: concentrazione P&L, entry buckets, bootstrap CI,
timeline, fill realism.

USO:
    python tools/audit_v2.py [path/al/signals.jsonl]
"""
import json
import random
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path


def load_resolved(path: Path):
    out = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("status") == "resolved" and rec.get("win") is not None:
                out.append(rec)
    return out


def net_pnl(r):
    if r.get("virtual_pnl_net") is not None:
        return float(r["virtual_pnl_net"])
    pnl = float(r.get("virtual_pnl") or 0.0)
    fee = float(r.get("fee_virtual") or 0.0)
    return pnl - fee


def entry_bucket(e):
    if e < 0.10:
        return "0-0.10"
    if e < 0.25:
        return "0.10-0.25"
    if e < 0.50:
        return "0.25-0.50"
    if e < 0.75:
        return "0.50-0.75"
    return "0.75+"


ENTRY_ORDER = ["0-0.10", "0.10-0.25", "0.25-0.50", "0.50-0.75", "0.75+"]


def bootstrap_ci(values, n_boot=10000, seed=42):
    rng = random.Random(seed)
    n = len(values)
    if n == 0:
        return 0.0, 0.0, 0.0
    means = []
    for _ in range(n_boot):
        s = sum(values[rng.randrange(n)] for _ in range(n)) / n
        means.append(s)
    means.sort()
    lo = means[int(0.025 * n_boot)]
    hi = means[int(0.975 * n_boot) - 1]
    return sum(values) / n, lo, hi


def main():
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    else:
        candidates = [
            Path("logs_monday/latency_arb_signals.jsonl"),
            Path("data/latency_arb_signals.jsonl"),
        ]
        path = next((p for p in candidates if p.exists()), None)
        if path is None:
            print("Nessun signals.jsonl trovato.")
            sys.exit(1)

    recs = load_resolved(path)
    print(f"File: {path}")
    print(f"resolved: {len(recs)}")
    if not recs:
        return

    nets = [net_pnl(r) for r in recs]
    total_net = sum(nets)
    print(f"P&L netto totale: ${total_net:+.2f}  |  EV/trade: ${total_net/len(recs):+.4f}")

    # --- 1. Concentrazione -------------------------------------------------
    print("\n=== CONCENTRAZIONE P&L ===")
    wins = sorted(
        [(net_pnl(r), r) for r in recs if r["win"]],
        key=lambda x: -x[0],
    )
    losses = sorted(
        [(net_pnl(r), r) for r in recs if not r["win"]],
        key=lambda x: x[0],
    )
    top10 = wins[:10]
    top10_sum = sum(p for p, _ in top10)
    print(f"Top-10 win P&L netto: ${top10_sum:+.2f}  "
          f"({100*top10_sum/total_net:.1f}% del totale netto)" if total_net else "")
    for i, (p, r) in enumerate(top10, 1):
        print(f"  #{i:2d} ${p:+7.2f}  entry={float(r['entry_price']):.3f}  "
              f"edge={float(r['edge']):+.2f}  "
              f"{r.get('symbol','?'):8s}  {r.get('action','?')}  "
              f"ask_u={r.get('ask_up')} ask_d={r.get('ask_down')}")

    # trimmed: drop top 5% wins by pnl
    n_drop = max(1, int(0.05 * len(wins)))
    drop_ids = {id(r) for _, r in wins[:n_drop]}
    trimmed = [net_pnl(r) for r in recs if id(r) not in drop_ids]
    trimmed_sum = sum(trimmed)
    print(f"\nTrimmed (senza top {n_drop} win = top 5% delle win): "
          f"n={len(trimmed)}  P&L netto=${trimmed_sum:+.2f}  "
          f"EV/trade=${trimmed_sum/len(trimmed):+.4f}")
    # also without top 1% and top 10%
    for pct in (0.01, 0.10):
        nd = max(1, int(pct * len(wins)))
        dropped = {id(r) for _, r in wins[:nd]}
        t = [net_pnl(r) for r in recs if id(r) not in dropped]
        print(f"  trimmed top {int(pct*100)}% win (drop {nd}): "
              f"P&L=${sum(t):+.2f}  EV=${sum(t)/len(t):+.4f}")

    # --- 2. Entry-price buckets --------------------------------------------
    print("\n=== SPLIT PER ENTRY PRICE (metrica corretta con payout convessi) ===")
    by_e = defaultdict(lambda: {"n": 0, "win": 0, "net": 0.0, "entry_sum": 0.0})
    for r in recs:
        e = float(r["entry_price"])
        b = by_e[entry_bucket(e)]
        b["n"] += 1
        b["win"] += 1 if r["win"] else 0
        b["net"] += net_pnl(r)
        b["entry_sum"] += e
    print(f"  {'bucket':12s} {'n':>5s} {'WR%':>6s} {'entry med':>9s} "
          f"{'P&L netto':>10s} {'EV/trade':>9s}")
    for k in ENTRY_ORDER:
        b = by_e.get(k)
        if not b or b["n"] == 0:
            continue
        wr = 100.0 * b["win"] / b["n"]
        avg_e = b["entry_sum"] / b["n"]
        ev = b["net"] / b["n"]
        print(f"  {k:12s} {b['n']:5d} {wr:6.1f} {avg_e:9.3f} "
              f"{b['net']:+10.2f} {ev:+9.4f}")

    # --- 3. Bootstrap CI ---------------------------------------------------
    print("\n=== BOOTSTRAP CI 95% su EV netto/trade (10k resample) ===")
    mean, lo, hi = bootstrap_ci(nets, n_boot=10000)
    print(f"  EV/trade = ${mean:+.4f}  CI95% = [${lo:+.4f}, ${hi:+.4f}]")
    print(f"  CI include zero? {'SI -> NON significativo' if lo <= 0 <= hi else 'NO -> significativo'}")
    # bootstrap trimmed
    mean_t, lo_t, hi_t = bootstrap_ci(trimmed, n_boot=10000)
    print(f"  Trimmed EV = ${mean_t:+.4f}  CI95% = [${lo_t:+.4f}, ${hi_t:+.4f}]")
    print(f"  Trimmed CI include zero? "
          f"{'SI -> NON significativo' if lo_t <= 0 <= hi_t else 'NO -> significativo'}")

    # bootstrap solo entry >= 0.25 (no longshot)
    no_ls = [net_pnl(r) for r in recs if float(r["entry_price"]) >= 0.25]
    if no_ls:
        m, l, h = bootstrap_ci(no_ls, n_boot=10000)
        print(f"  Solo entry>=0.25 (n={len(no_ls)}): EV=${m:+.4f}  "
              f"CI95%=[${l:+.4f}, ${h:+.4f}]  "
              f"{'include 0' if l <= 0 <= h else 'NO zero'}")

    # bootstrap solo |edge| >= 0.15
    hi_edge = [net_pnl(r) for r in recs if abs(float(r.get("edge") or 0)) >= 0.15]
    if hi_edge:
        m, l, h = bootstrap_ci(hi_edge, n_boot=10000)
        print(f"  Solo |edge|>=0.15 (n={len(hi_edge)}): EV=${m:+.4f}  "
              f"CI95%=[${l:+.4f}, ${h:+.4f}]  "
              f"{'include 0' if l <= 0 <= h else 'NO zero'}")

    # --- 4. Timeline -------------------------------------------------------
    print("\n=== TIMELINE P&L CUMULATO (per ora UTC) ===")
    by_hour = defaultdict(lambda: {"n": 0, "net": 0.0, "win": 0})
    ordered = sorted(recs, key=lambda r: r.get("ts_close") or r.get("ts_open") or "")
    cum = 0.0
    milestones = []
    for r in ordered:
        ts = r.get("ts_close") or r.get("ts_open") or ""
        hour = ts[:13]  # YYYY-MM-DDTHH
        p = net_pnl(r)
        cum += p
        by_hour[hour]["n"] += 1
        by_hour[hour]["net"] += p
        by_hour[hour]["win"] += 1 if r["win"] else 0
        milestones.append((ts, cum, p, r))

    # print hourly
    print(f"  {'ora UTC':16s} {'n':>4s} {'WR%':>6s} {'P&L ora':>9s} {'cumul':>9s}")
    running = 0.0
    for hour in sorted(by_hour.keys()):
        b = by_hour[hour]
        running += b["net"]
        wr = 100.0 * b["win"] / b["n"] if b["n"] else 0
        print(f"  {hour:16s} {b['n']:4d} {wr:6.1f} {b['net']:+9.2f} {running:+9.2f}")

    # detect big jumps: single trade > $3 or hour jump > $15
    print("\n  Gradini grandi (singolo trade |pnl|>$3):")
    big = [(ts, p, r) for ts, cum, p, r in milestones if abs(p) > 3.0]
    for ts, p, r in big[:20]:
        print(f"    {ts[:19]}  ${p:+7.2f}  entry={float(r['entry_price']):.3f}  "
              f"{r.get('symbol')} {r.get('action')} win={r['win']}")
    if not big:
        print("    nessuno")

    # --- 5. Fill realism ---------------------------------------------------
    print("\n=== FILL REALISM (spread implicito ask_up+ask_down-1) ===")
    spreads = []
    ghost = 0
    neg_spread = 0
    wide = 0
    missing_ask = 0
    for r in recs:
        au = r.get("ask_up")
        ad = r.get("ask_down")
        if au is None or ad is None:
            missing_ask += 1
            continue
        s = float(au) + float(ad) - 1.0
        spreads.append(s)
        if s < -0.01:
            neg_spread += 1
        if s > 0.10:
            wide += 1
        # ghost: entry very low AND opposite ask near 1 (thin book)
        e = float(r["entry_price"])
        if e < 0.15 and (float(au) > 0.90 or float(ad) > 0.90):
            ghost += 1

    if spreads:
        spreads.sort()
        n = len(spreads)
        print(f"  n con ask: {n}  missing: {missing_ask}")
        print(f"  spread mediano: {spreads[n//2]:+.4f}  "
              f"p10={spreads[n//10]:+.4f}  p90={spreads[int(0.9*n)]:+.4f}")
        print(f"  spread negativo (<-1c): {neg_spread}  "
              f"spread largo (>10c): {wide}")
        print(f"  ghost-book proxy (entry<0.15 & opposite ask>0.90): {ghost}")

    # P&L of ghost vs clean
    ghost_pnl = 0.0
    ghost_n = 0
    clean_pnl = 0.0
    clean_n = 0
    for r in recs:
        au = r.get("ask_up")
        ad = r.get("ask_down")
        e = float(r["entry_price"])
        is_ghost = (au is not None and ad is not None and e < 0.15
                    and (float(au) > 0.90 or float(ad) > 0.90))
        if is_ghost:
            ghost_pnl += net_pnl(r)
            ghost_n += 1
        else:
            clean_pnl += net_pnl(r)
            clean_n += 1
    print(f"  ghost trades: n={ghost_n} P&L=${ghost_pnl:+.2f}  "
          f"EV=${ghost_pnl/ghost_n:+.4f}" if ghost_n else "  ghost trades: 0")
    print(f"  clean trades: n={clean_n} P&L=${clean_pnl:+.2f}  "
          f"EV=${clean_pnl/clean_n:+.4f}" if clean_n else "")

    # low-entry winners specifically
    print("\n  Win a entry<0.15 (i longshot che gonfiano):")
    low_wins = [(net_pnl(r), r) for r in recs
                if r["win"] and float(r["entry_price"]) < 0.15]
    low_wins.sort(key=lambda x: -x[0])
    low_sum = sum(p for p, _ in low_wins)
    print(f"  n={len(low_wins)}  P&L netto=${low_sum:+.2f}")
    for p, r in low_wins[:15]:
        au, ad = r.get("ask_up"), r.get("ask_down")
        spr = (float(au) + float(ad) - 1.0) if au is not None and ad is not None else None
        print(f"    ${p:+7.2f} entry={float(r['entry_price']):.3f} "
              f"spread={spr:+.3f}" if spr is not None else
              f"    ${p:+7.2f} entry={float(r['entry_price']):.3f} spread=?")

    # --- Verdict helper ----------------------------------------------------
    print("\n=== VERDETTO RAPIDO ===")
    print(f"  total_net={total_net:+.2f}  trimmed_5%={trimmed_sum:+.2f}")
    print(f"  top10_share={100*top10_sum/total_net:.1f}%" if total_net else "")
    print(f"  bootstrap_full CI95=[{lo:+.4f},{hi:+.4f}]  "
          f"trimmed CI95=[{lo_t:+.4f},{hi_t:+.4f}]")
    if trimmed_sum <= 0 or lo_t <= 0:
        print("  => EDGE ILLUSORIO / NON ROBUSTO: trimmed~0 o CI include 0")
    elif top10_sum / total_net > 0.5 if total_net else False:
        print("  => EDGE CONCENTRATO in outlier: ripulire entry band / soglia")
    else:
        print("  => EDGE CREDIBILE ma da ripulire (sigma, soglia, entry band, strike)")


if __name__ == "__main__":
    main()
