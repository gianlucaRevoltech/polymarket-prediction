"""
Diagnostica: scopre quale query parameter di gamma /markets
ritorna davvero il mercato per un condition_id dato.

USO (su VPS Linux):
    python3 tools/test_gamma_query.py
    python3 tools/test_gamma_query.py --cid 0x3098...
"""
import argparse
import requests

G = "https://gamma-api.polymarket.com"
H = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"}

DEFAULT_CID = "0x3098ae72bf6e3eeaa297b15f6c20dedf6ac5bd3e3d942c3158a60b446147460c"


def try_query(label, params):
    try:
        r = requests.get(f"{G}/markets", params=params, headers=H, timeout=15)
    except Exception as e:
        print(f"{label:40s} ERROR: {e}")
        return
    try:
        arr = r.json()
    except Exception:
        arr = None
    if isinstance(arr, list):
        n = len(arr)
        extra = ""
        if n == 1:
            m = arr[0]
            extra = (f" | q={m.get('question','')[:50]} "
                     f"closed={m.get('closed')} "
                     f"outcomes={m.get('outcomes')}")
        elif n > 1:
            extra = f" | first q={arr[0].get('question','')[:50]}"
    else:
        n = f"non-list: {str(arr)[:120]}"
        extra = ""
    print(f"{label:40s} HTTP={r.status_code} len={n}{extra}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cid", default=DEFAULT_CID,
                    help="condition_id da testare")
    args = ap.parse_args()
    cid = args.cid

    print(f"Testing condition_id = {cid}\n")

    tests = [
        ("condition_ids (plurale, corrente)", {"condition_ids": cid}),
        ("condition_id (singolare)",          {"condition_id": cid}),
        ("id",                                 {"id": cid}),
        ("slug",                                {"slug": cid}),
        ("conditionId (camelCase)",            {"conditionId": cid}),
    ]

    for label, params in tests:
        try_query(label, params)

    # test extra: senza filtro, primi 3 mercati closed (sanity check gamma)
    print("\nSanity check: /markets?closed=true&active=false&limit=3")
    try:
        r = requests.get(f"{G}/markets",
                         params={"closed": "true", "active": "false",
                                 "limit": 3},
                         headers=H, timeout=15)
        arr = r.json() if r.ok else None
        if isinstance(arr, list) and arr:
            for m in arr:
                print(f"  cid={m.get('conditionId','')[:20]}... "
                      f"q={m.get('question','')[:50]} "
                      f"closed={m.get('closed')}")
        else:
            print(f"  HTTP={r.status_code} body={str(arr)[:200]}")
    except Exception as e:
        print(f"  ERROR: {e}")


if __name__ == "__main__":
    main()
