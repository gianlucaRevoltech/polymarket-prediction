"""
Diagnostica: testa CLOB /markets/{condition_id} e gamma /markets?archived=true
per recuperare mercati scaduti che non si trovano con closed=true&active=false.

USO (su VPS Linux):
    python3 tools/test_clob_market.py
"""
import argparse
import json
import requests

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
H = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"}

DEFAULT_CID = "0x3098ae72bf6e3eeaa297b15f6c20dedf6ac5bd3e3d942c3158a60b446147460c"


def try_clob(cid):
    """CLOB GET /markets/{condition_id} — diretto, niente filtri."""
    try:
        r = requests.get(f"{CLOB}/markets/{cid}", headers=H, timeout=15)
        print(f"CLOB /markets/{{cid}}  HTTP={r.status_code}")
        if not r.ok:
            print(f"  body[:200]={r.text[:200]}")
            return None
        m = r.json()
        # dump campi chiave
        for k in ("condition_id", "question", "market_slug", "end_date_iso",
                  "closed", "accepting_orders", "game_starting_time",
                  "outcomes", "outcome_prices", "tokens", "rewards",
                  "uma_disputes", "resolvedBy"):
            v = m.get(k, "<absent>")
            print(f"  {k}: {json.dumps(v)[:200] if v != '<absent>' else v}")
        return m
    except Exception as e:
        print(f"CLOB ERROR: {e}")
        return None


def try_gamma_archived(cid):
    """gamma /markets?archived=true — forse i crypto 5/15min sono archived."""
    try:
        r = requests.get(f"{GAMMA}/markets",
                         params={"archived": "true", "active": "false",
                                 "limit": 500},
                         headers=H, timeout=15)
        arr = r.json() if r.ok else None
        n = len(arr) if isinstance(arr, list) else f"non-list {str(arr)[:120]}"
        print(f"gamma archived=true HTTP={r.status_code} len={n}")
        if isinstance(arr, list):
            found = None
            for m in arr:
                if (m.get("conditionId") or "") == cid:
                    found = m
                    break
            if found:
                print(f"  FOUND in archived! q={found.get('question','')[:50]} "
                      f"closed={found.get('closed')}")
                for k in ("outcomes", "outcomePrices", "closed", "id"):
                    print(f"  {k}: {json.dumps(found.get(k))[:200]}")
            else:
                # stampa un sample per capire che tipo di mercati sono
                if arr:
                    sample = arr[0]
                    print(f"  sample: q={sample.get('question','')[:50]} "
                          f"closed={sample.get('closed')} "
                          f"cid={sample.get('conditionId','')[:20]}...")
        return found if isinstance(arr, list) and found else None
    except Exception as e:
        print(f"gamma archived ERROR: {e}")
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cid", default=DEFAULT_CID)
    args = ap.parse_args()
    cid = args.cid
    print(f"=== Testing condition_id = {cid} ===\n")
    print("[1] CLOB API diretta:")
    m = try_clob(cid)
    print()
    print("[2] gamma archived=true (scan locale):")
    try_gamma_archived(cid)


if __name__ == "__main__":
    main()