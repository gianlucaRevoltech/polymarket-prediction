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


def p_side_of(rec) -> float:
    """Probabilita' modello che il LATO COMPRATO vinca (record v2)."""
    p_up = float(rec.get("p_model_up") or 0.5)
    return p_up if rec.get("action") == "LONG_YES" else 1.0 - p_up


def reliability_bucket(p: float) -> str:
    if p < 0.40:
        return "<0.40"
    if p < 0.50:
        return "0.40-0.50"
    if p < 0.60:
        return "0.50-0.60"
    if p < 0.70:
        return "0.60-0.70"
    if p < 0.80:
        return "0.70-0.80"
    if p < 0.90:
        return "0.80-0.90"
    return "0.90+"


RELIABILITY_ORDER = ["<0.40", "0.40-0.50", "0.50-0.60", "0.60-0.70",
                     "0.70-0.80", "0.80-0.90", "0.90+"]


def analyze_v2_calibration(recs):
    """Sezione calibrazione per record v2 (p_model_up/z_score/strike_source).

    Test diretto della sovraconfidenza: per ogni bucket di p_model del lato
    comprato, confronta p_model medio col WR realizzato. Modello calibrato =>
    le due colonne coincidono (entro rumore binomiale)."""
    v2 = [r for r in recs if r.get("model_version") == 2
          and r.get("p_model_up") is not None]
    if not v2:
        return
    print("\n" + "=" * 62)
    print("CALIBRAZIONE MODELLO v2")
    print("=" * 62)

    # --- reliability table + Brier -------------------------------------
    rel = defaultdict(lambda: {"n": 0, "win": 0, "p_sum": 0.0,
                               "pnl": 0.0, "fee": 0.0})
    brier_sum = 0.0
    for r in v2:
        p = p_side_of(r)
        won = 1.0 if r["win"] else 0.0
        brier_sum += (p - won) ** 2
        b = rel[reliability_bucket(p)]
        b["n"] += 1
        b["win"] += 1 if r["win"] else 0
        b["p_sum"] += p
        b["pnl"] += float(r.get("virtual_pnl") or 0.0)
        b["fee"] += float(r.get("fee_virtual") or 0.0)
    n2 = len(v2)
    brier = brier_sum / n2
    print(f"\nv2 resolved: {n2} | Brier score: {brier:.4f} "
          f"(0.25 = coin flip senza skill; piu' basso = meglio)")
    print("\n=== Reliability table (p_model lato comprato vs WR reale) ===")
    print(f"  {'bucket':10s} {'n':>5s} {'p_model med':>11s} {'WR reale':>9s} "
          f"{'gap':>7s} {'P&L netto':>9s}")
    for k in RELIABILITY_ORDER:
        b = rel.get(k)
        if not b or b["n"] == 0:
            continue
        p_avg = b["p_sum"] / b["n"]
        wr_real = b["win"] / b["n"]
        gap = wr_real - p_avg
        net = b["pnl"] - b["fee"]
        print(f"  {k:10s} {b['n']:5d} {p_avg*100:10.1f}% {wr_real*100:8.1f}% "
              f"{gap*100:+6.1f}pt {net:+9.2f}")
    print("  (gap negativo sistematico nei bucket alti = sovraconfidenza "
          "=> sigma da alzare)")

    # --- split per strike_source ----------------------------------------
    by_src = defaultdict(bucket_stats)
    for r in v2:
        src = (r.get("strike_source") or "?").split(":")[0]
        add(by_src[src], r)
    print_table("v2: split per FONTE STRIKE", by_src)

    # --- split per |z_score| ---------------------------------------------
    by_z = defaultdict(bucket_stats)
    for r in v2:
        z = abs(float(r.get("z_score") or 0.0))
        zk = ("0-0.5" if z < 0.5 else "0.5-1" if z < 1.0
              else "1-2" if z < 2.0 else "2+")
        add(by_z[zk], r)
    print_table("v2: split per |z_score|", by_z, ["0-0.5", "0.5-1", "1-2", "2+"])

    # --- split per distanza dallo strike ---------------------------------
    by_dist = defaultdict(bucket_stats)
    for r in v2:
        d = abs(float(r.get("dist_from_strike_pct") or 0.0))
        dk = ("<0.03%" if d < 0.03 else "0.03-0.10%" if d < 0.10
              else "0.10-0.30%" if d < 0.30 else "0.30%+")
        add(by_dist[dk], r)
    print_table("v2: split per |S/K - 1| (distanza dallo strike)", by_dist,
                ["<0.03%", "0.03-0.10%", "0.10-0.30%", "0.30%+"])


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

    analyze_v2_calibration(recs)


if __name__ == "__main__":
    main()
