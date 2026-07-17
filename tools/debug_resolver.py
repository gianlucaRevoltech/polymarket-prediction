"""
Debug — conferma campi gamma per contratti crypto up/down RISOLTI/SCADUTI.

USO (su VPS Linux, NON da Windows — gamma geo-blockata):
    python tools/debug_resolver.py
    python tools/debug_resolver.py --max 10 --jsonl data/latency_arb_signals.jsonl

Dumpa il JSON completo dei primi N condition_id scaduti (presi dal signals.jsonl
o query diretta gamma) e stampa la derivazione che il nuovo resolver fa:
  - closed, acceptingOrders, closedTime
  - outcomes (parsed), outcomePrices (parsed), outcomeMetas, umaResolutionStatus
  - index max → winner name → bool UP_won

Utile per validare il fix di resolve_contract() prima di fare deploy blindato.
"""
import argparse
import json
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

# import dal src/ (runnato da root del repo)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from latency_arb import _parse_json_list, _days_to_expiry, PolymarketContractFeed  # noqa


def _pick_keyword(path):
    return ""


def _show_market(feed: PolymarketContractFeed, cid: str, idx: int,
                market_id=None):
    print(f"\n=== [{idx}] condition_id={cid} market_id={market_id} ===")
    # usa il nuovo metodo robusto (gamma NON supporta condition_ids filter)
    m = feed._fetch_market(cid, market_id)
    if m is None:
        print("  NO market trovato — cid non in gamma closed markets?")
        return
    # campi chiave da capire
    interesting = [
        "conditionId", "question", "slug", "closed", "acceptingOrders",
        "closedTime", "endDate", "resolvedBy", "umaResolutionStatus",
        "outcome", "outcomes", "outcomePrices", "outcomeMetas",
        "clobTokenIds", "bestBid", "bestAsk", "lastTradePrice",
        "volumeNum", "active", "archived",
    ]
    print("  --- raw fields ---")
    for k in interesting:
        if k in m:
            print(f"  {k}: {json.dumps(m[k])[:200]}")
    # stampa anche ogni altro campo che contiene 'price' o 'resolve' o 'win'
    print("  --- extra fields w/ price|resolve|win|outcome|uma ---")
    seen = set(interesting)
    for k, v in m.items():
        if k in seen:
            continue
        kl = k.lower()
        if any(t in kl for t in ("price", "resolve", "win", "outcome", "uma")):
            print(f"  {k}: {json.dumps(v)[:200]}")
    # prova la derivazione del nuovo resolver
    outcomes = _parse_json_list(m.get("outcomes"))
    prices_raw = _parse_json_list(m.get("outcomePrices"))
    print("  --- resolver derivation ---")
    print(f"  outcomes(parsed) = {outcomes}")
    print(f"  outcomePrices(parsed) = {prices_raw}")
    if len(outcomes) >= 2 and len(prices_raw) >= 2:
        try:
            prices = [float(p) if p is not None else 0.0 for p in prices_raw]
            hi_idx = max(range(len(prices)), key=lambda i: prices[i])
            lo_idx = min(range(len(prices)), key=lambda i: prices[i])
            print(f"  prices(float)={prices} hi_idx={hi_idx} lo_idx={lo_idx} "
                  f"hi={prices[hi_idx]:.4f} lo={prices[lo_idx]:.4f}")
            if prices[hi_idx] >= 0.95 and prices[lo_idx] <= 0.05:
                winner = (outcomes[hi_idx] or "").lower()
                up_like = "up" in winner or "yes" in winner
                down_like = "down" in winner or "no" in winner
                print(f"  -> RESOLVED winner={winner!r} UP_won={up_like} "
                      f"DOWN_won={down_like}")
                if up_like:
                    print(f"  RESULT: resolve_contract() → True (UP won)")
                elif down_like:
                    print(f"  RESULT: resolve_contract() → False (DOWN won)")
                else:
                    print(f"  RESULT: resolve_contract() → None (outcomes non "
                          f"Up/Down — non etichettabile)")
            else:
                print(f"  -> NOT YET RESOLVED (prices non 1/0) — still live/post-expiry")
        except Exception as e:
            print(f"  prices parse error: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=5,
                    help="max condition_id da mostrare (default 5)")
    ap.add_argument("--jsonl", default="data/latency_arb_signals.jsonl",
                    help="file signals da cui pescare condition_id scaduti")
    args = ap.parse_args()

    feed = PolymarketContractFeed()

    # raccogli (condition_id, market_id) da signals.jsonl o fallback gamma
    seen_cids = []
    seen_set = set()
    market_ids = {}   # cid -> market_id (se presente nel log)
    p = Path(args.jsonl)
    if not p.exists():
        print(f"!! {p} non esistente — provo fetch diretta gamma ( ultimi "
              f"mercati crypto up/down scaduti).")
        # refresh_active filtra solo attivi → qui serve closed. Fallback:
        # query markets con closed=true filter per pattern crypto up/down
        # + raccogli market_id (intero) per risoluzione robusta
        try:
            now = datetime.now(timezone.utc)
            r = feed.s.get(f"{feed.gamma}/markets",
                           params={"closed": "true", "active": "false",
                                   "end_date_max": now.isoformat(),
                                   "limit": 200}, timeout=15)
            if r.ok:
                for m in r.json():
                    t = (m.get("question") or "").lower()
                    if any(pat in t for pat in (
                        "bitcoin up or down", "btc up or down",
                        "ethereum up or down", "eth up or down")):
                        cid = m.get("conditionId", "")
                        if cid and cid not in seen_set:
                            seen_set.add(cid)
                            seen_cids.append(cid)
                            mid = m.get("id")
                            if mid is not None:
                                market_ids[cid] = mid
        except Exception as e:
            print(f"fallback fetch failed: {e}")
    else:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                cid = rec.get("condition_id") or rec.get("conditionId")
                end = rec.get("end_date")
                if not cid or not end:
                    continue
                # solo scaduti (end_date <= now)
                if _days_to_expiry(end) is None:
                    continue
                if _days_to_expiry(end) > 0:
                    continue
                if cid not in seen_set:
                    seen_set.add(cid)
                    seen_cids.append(cid)
                # raccogli market_id se presente (anche da record resolved/stale)
                mid = rec.get("market_id")
                if mid is not None:
                    market_ids[cid] = mid

    if not seen_cids:
        print("Nessun condition_id scaduto trovato.")
        return

    print(f"Trovati {len(seen_cids)} condition_id scaduti unici.")
    for i, cid in enumerate(seen_cids[:args.max]):
        _show_market(feed, cid, i + 1, market_ids.get(cid))


if __name__ == "__main__":
    main()