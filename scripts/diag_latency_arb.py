"""Diagnostica Step 0 latency arb v3 - discovery strategies.

Uso (sulla VPS):
    cd /root/polymarket-prediction
    PYTHONPATH=src ./venv/bin/python scripts/diag_latency_arb.py
"""
import sys
import os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

from config import POLYMARKET_API
from latency_arb import (PolymarketContractFeed, BinanceFeed, LATENCY_ARB,
                         _parse_json_list, _days_to_expiry)
import requests as _rq

GAMMA = POLYMARKET_API["gamma"]
CLOB = POLYMARKET_API["clob"]

s = _rq.Session()
s.headers.update({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"})


def fetch(params, label):
    try:
        r = s.get(f"{GAMMA}/markets", params=params, timeout=20)
        print(f"  [{label}] status={r.status_code} len={len(r.text)}")
        return r.json() if r.ok else []
    except Exception as e:
        print(f"  [{label}] error: {e}")
        return []


def classify(markets, label):
    crypto = 0
    short = 0
    short_samples = []
    for m in markets:
        t = (m.get("question") or m.get("title") or "").lower()
        end = m.get("endDate", "")
        days = _days_to_expiry(end)
        mins = (days * 1440.0) if days is not None else None
        if any(k in t for k in ["bitcoin", "btc", "ethereum", "eth up", " eth "]):
            crypto += 1
        if mins is not None and 0 <= mins <= 15:
            short += 1
            if len(short_samples) < 10:
                short_samples.append((mins, t[:70]))
    print(f"  -> total={len(markets)} crypto-like={crypto} short-expiry(<=15min)={short}")
    for mins, t in short_samples:
        print(f"     [{mins:.1f}min] {t}")


print("=== GAMMA base URL:", GAMMA)
print()

# Strategia A: limit esplicito alto (cap摸到底)
print("=== STRATEGIA A: limit=500 (default ordering volumeNum) ===")
mA = fetch({"closed": "false", "active": "true",
            "order": "volumeNum", "ascending": "false", "limit": 500}, "A")
classify(mA, "A")
print()

# Strategia B: paginazione con offset (vedi se ci sono oltre 100)
print("=== STRATEGIA B: offset=100 limit=500 ===")
mB = fetch({"closed": "false", "active": "true",
            "order": "volumeNum", "ascending": "false",
            "limit": 500, "offset": 100}, "B")
classify(mB, "B")
print()

# Strategia C: filtro end_date_max = now+30min (solo scadenza ravvicinata)
print("=== STRATEGIA C: end_date_max = now+30min ISO ===")
now = datetime.now(timezone.utc)
end_max = (now + timedelta(minutes=30)).isoformat()
end_min = now.isoformat()
print(f"  end_date_min={end_min}")
print(f"  end_date_max={end_max}")
mC = fetch({"closed": "false", "active": "true",
            "end_date_min": end_min, "end_date_max": end_max,
            "limit": 500}, "C")
classify(mC, "C")
print()

# Strategia D: tag_slug=Bitcoin
print("=== STRATEGIA D: tag_slug=Bitcoin ===")
mD = fetch({"closed": "false", "active": "true",
            "tag_slug": "Bitcoin", "limit": 500}, "D")
classify(mD, "D")
print()

# Strategia E: ordering per end_date ASC (prima quelli che scadono)
print("=== STRATEGIA E: order=end_date_min ascending ===")
mE = fetch({"closed": "false", "active": "true",
            "order": "end_date_min", "ascending": "true", "limit": 500}, "E")
classify(mE, "E")
# stampa primi 10 titoli con scadenza
samples_E = []
for m in mE[:30]:
    t = (m.get("question") or m.get("title") or "")[:65]
    end = m.get("endDate", "")
    days = _days_to_expiry(end)
    mins = (days * 1440.0) if days is not None else None
    samples_E.append((mins, t))
print("  primeros 10 per scadenza ASC:")
for mins, t in samples_E[:10]:
    print(f"     [{'?' if mins is None else '%.1f' % mins}min] {t}")
print()

# Strategia F: endpoint /events con tag bitcoin
print("=== STRATEGIA F: /events?tag_slug=Bitcoin ===")
try:
    rF = s.get(f"{GAMMA}/events",
              params={"closed": "false", "active": "true",
                      "tag_slug": "Bitcoin", "limit": 50}, timeout=20)
    print(f"  status={rF.status_code} len={len(rF.text)}")
    if rF.ok:
        evs = rF.json()
        print(f"  events={len(evs)}")
        for e in evs[:10]:
            print(f"     slug={e.get('slug','')[:50]} | {e.get('title','')[:50]}")
except Exception as e:
    print(f"  error: {e}")
print()

# Strategia G: search text "up or down"
print("=== STRATEGIA G: /markets?slug_contains=up-or-down ===")
mG = fetch({"closed": "false", "active": "true",
            "slug": "up-or-down", "limit": 500}, "G")
classify(mG, "G")
print()

# Strategia H: liquidity ordering (invece di volumeNum)
print("=== STRATEGIA H: order=liquidityNum descending ===")
mH = fetch({"closed": "false", "active": "true",
            "order": "liquidityNum", "ascending": "false", "limit": 500}, "H")
classify(mH, "H")
print()

print("=== FINE DIAGNOSTICA v3 ===")