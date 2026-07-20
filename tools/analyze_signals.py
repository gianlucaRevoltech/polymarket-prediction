#!/usr/bin/env python3
"""Analisi offline di data/latency_arb_signals.jsonl (record status=resolved).

Scopo (Fase A del piano "Fix modello latency arb"):
  - quantificare il comportamento del modello vecchio (0.5 + K*delta_5m)
  - confermare/smentire l'ipotesi "il bot compra il lato che il flusso
    informato sta vendendo" (WR ~ entry price => mercato ben prezzato)
  - stimare l'EV della strategia INVERSA (comprare il favorito) al lordo
    e al netto della taker fee crypto (rate 0.07, fee = shares*rate*p*(1-p))

USO:
    python tools/analyze_signals.py [path/al/signals.jsonl]
    # default: prova logs_weekend/latency_arb_signals.jsonl poi data/...
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

CRYPTO_FEE_RATE = 0.07  # docs.polymarket.com/trading/fees (lug 2026)


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


def taker_fee_per_dollar(entry: float) -> float:
    """Fee taker per $1 di capitale investito a prezzo `entry`.

    shares = 1/entry; fee = shares * rate * p * (1-p) = rate * (1-entry).
    """
    if entry <= 0 or entry >= 1:
        return 0.0
    return CRYPTO_FEE_RATE * (1.0 - entry)


def wr(stats):
    return 100.0 * stats["win"] / stats["n"] if stats["n"] else 0.0


def bucket_stats():
    return {"n": 0, "win": 0, "pnl": 0.0, "entry_sum": 0.0,
            "pnl_inv": 0.0, "fee": 0.0, "fee_inv": 0.0}


def add(stats, rec):
    entry = float(rec.get("entry_price") or 0.0)
    won = bool(rec["win"])
    pnl = float(rec.get("virtual_pnl") or 0.0)
    stats["n"] += 1
    stats["win"] += 1 if won else 0
    stats["pnl"] += pnl
    stats["entry_sum"] += entry
    stats["fee"] += taker_fee_per_dollar(entry)
    # Strategia INVERSA: compra il lato opposto a prezzo ~(1-entry).
    # Vince quando il segnale originale perde. P&L su $1:
    #   win_inv:  1/(1-entry) - 1 ; loss_inv: -1
    inv_entry = 1.0 - entry
    if 0.0 < inv_entry < 1.0:
        if not won:
            stats["pnl_inv"] += (1.0 / inv_entry) - 1.0
        else:
            stats["pnl_inv"] += -1.0
        stats["fee_inv"] += taker_fee_per_dollar(inv_entry)


def minutes_bucket(m):
    if m is None:
        return "?"
    if m < 2:
        return "0-2min"
    if m < 5:
        return "2-5min"
    if m < 10:
        return "5-10min"
    return "10-15min"


def print_table(title, groups, order=None):
    print(f"\n=== {title} ===")
    keys = order or sorted(groups.keys())
    hdr = (f"  {'gruppo':14s} {'n':>5s} {'WR%':>6s} {'entry med':>9s} "
           f"{'P&L':>8s} {'P&L netto':>9s} {'P&L inv':>8s} {'inv netto':>9s}")
    print(hdr)
    for k in keys:
        s = groups.get(k)
        if not s or s["n"] == 0:
            continue
        avg_entry = s["entry_sum"] / s["n"]
        net = s["pnl"] - s["fee"]
        net_inv = s["pnl_inv"] - s["fee_inv"]
        print(f"  {str(k):14s} {s['n']:5d} {wr(s):6.1f} {avg_entry:9.3f} "
              f"{s['pnl']:+8.2f} {net:+9.2f} {s['pnl_inv']:+8.2f} {net_inv:+9.2f}")


def main():
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    else:
        candidates = [Path("logs_weekend/latency_arb_signals.jsonl"),
                      Path("data/latency_arb_signals.jsonl")]
        path = next((p for p in candidates if p.exists()), None)
        if path is None:
            print("Nessun signals.jsonl trovato. Uso: "
                  "python tools/analyze_signals.py <path>")
            sys.exit(1)
    print(f"File: {path}")
    recs = load_resolved(path)
    if not recs:
        print("Nessun record resolved nel file.")
        sys.exit(0)

    total = bucket_stats()
    by_action = defaultdict(bucket_stats)
    by_asset = defaultdict(bucket_stats)
    by_minutes = defaultdict(bucket_stats)
    by_edge = defaultdict(bucket_stats)
    by_coherence = defaultdict(bucket_stats)

    for rec in recs:
        add(total, rec)
        action = rec.get("action", "?")
        add(by_action[action], rec)
        title = (rec.get("title") or "").lower()
        sym = rec.get("symbol") or ""
        asset = ("BTC" if ("bitcoin" in title or sym == "BTCUSDT")
                 else "ETH" if ("ethereum" in title or sym == "ETHUSDT")
                 else "OTHER")
        add(by_asset[asset], rec)
        add(by_minutes[minutes_bucket(rec.get("minutes_left"))], rec)
        edge = abs(float(rec.get("edge") or 0.0))
        ek = "10-15" if edge < 0.15 else "15-20" if edge < 0.20 else "20+"
        add(by_edge[ek], rec)
        # coerenza: il segnale era allineato al momentum Binance?
        delta = rec.get("delta_5m_pct")
        if delta is not None:
            aligned = ((action == "LONG_YES" and float(delta) > 0) or
                       (action == "LONG_NO" and float(delta) < 0))
            add(by_coherence["allineato" if aligned else "contrario"], rec)

    n = total["n"]
    avg_entry = total["entry_sum"] / n
    print(f"\nresolved: {n} | WR {wr(total):.1f}% | entry medio {avg_entry:.3f}")
    print(f"P&L lordo strategia originale: ${total['pnl']:+.2f} "
          f"(netto fee taker: ${total['pnl'] - total['fee']:+.2f})")
    print(f"P&L lordo strategia INVERSA:   ${total['pnl_inv']:+.2f} "
          f"(netto fee taker: ${total['pnl_inv'] - total['fee_inv']:+.2f})")
    print(f"\nInterpretazione rapida:")
    print(f"  - WR ~ entry medio ({wr(total):.1f}% vs {avg_entry*100:.1f}%) "
          f"=> il mercato era prezzato bene, il modello non aggiunge edge")
    print(f"  - Se 'P&L inv netto' > 0 => il favorito aveva ancora spazio "
          f"(latency edge residuo); se <= 0 => mercato efficiente anche li'")

    print_table("Split per DIREZIONE", by_action, ["LONG_YES", "LONG_NO"])
    print_table("Split per ASSET", by_asset, ["BTC", "ETH", "OTHER"])
    print_table("Split per MINUTI a scadenza", by_minutes,
                ["0-2min", "2-5min", "5-10min", "10-15min", "?"])
    print_table("Split per |edge|", by_edge, ["10-15", "15-20", "20+"])
    print_table("Coerenza col momentum Binance", by_coherence,
                ["allineato", "contrario"])


if __name__ == "__main__":
    main()
