"""
Sync delle posizioni reali dei wallet target via data-api Polymarket.

A differenza del feed `activity` (eventi singoli, in ritardo, con mapping YES/NO
fragile), qui leggiamo lo SNAPSHOT del portafoglio di ogni wallet:
  GET https://data-api.polymarket.com/positions?user=<addr>

L'endpoint restituisce, per ogni posizione aperta, prezzo corrente, PnL e stato
di risoluzione: questo ci permette di valorizzare, chiudere e realizzare le
posizioni simulate in modo fedele.
"""
import requests
import json as _json
from typing import Dict, List, Optional
from config import POLYMARKET_API, STRATEGY
from categories import categorize_market


class PolymarketPositionFetcher:
    """Recupera lo snapshot delle posizioni dei wallet e i prezzi correnti."""

    def __init__(self):
        self.data_api = POLYMARKET_API["data"]
        self.clob = POLYMARKET_API["clob"]
        self.gamma_api = POLYMARKET_API["gamma"]
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })

    def get_positions(self, wallet_address: str, limit: int = 200) -> List[Dict]:
        """
        Ritorna le posizioni aperte di un wallet, normalizzate.

        Returns:
            Lista di dict normalizzati (vedi `_normalize`).
        """
        try:
            url = f"{self.data_api}/positions"
            params = {"user": wallet_address, "limit": limit}
            response = self.session.get(url, params=params, timeout=15)
            response.raise_for_status()
            raw = response.json()
        except Exception as e:
            print(f"[SYNC] Errore positions {wallet_address[:10]}...: {e}")
            return []

        positions = []
        for p in raw:
            norm = self._normalize(p)
            # Scarta dust / posizioni vuote
            if norm["asset"] and norm["size"] > 0:
                positions.append(norm)
        return positions

    @staticmethod
    def _normalize(p: Dict) -> Dict:
        """Normalizza la posizione raw del data-api in un formato stabile."""
        size = float(p.get("size", 0.0) or 0.0)
        avg_price = float(p.get("avgPrice", 0.0) or 0.0)
        title = p.get("title", "Unknown Market")
        slug = p.get("slug", "")
        event_slug = p.get("eventSlug") or p.get("event_slug") or ""
        event_id = str(p.get("eventId") or p.get("event_id") or "")
        event_title = p.get("eventTitle") or p.get("event_title") or ""
        return {
            "asset": str(p.get("asset", "")),
            "condition_id": p.get("conditionId", ""),
            "title": title,
            "slug": slug,
            "event_id": event_id,
            "event_slug": event_slug,
            "event_title": event_title,
            "category": categorize_market(title, event_slug=event_slug or slug),
            "outcome": p.get("outcome", ""),
            "outcome_index": p.get("outcomeIndex", 0),
            "size": size,
            "avg_price": avg_price,
            "cur_price": float(p.get("curPrice", 0.0) or 0.0),
            "current_value": float(p.get("currentValue", 0.0) or 0.0),
            "cash_pnl": float(p.get("cashPnl", 0.0) or 0.0),
            "realized_pnl": float(p.get("realizedPnl", 0.0) or 0.0),
            "redeemable": bool(p.get("redeemable", False)),
            "source_trade_at": (
                p.get("timestamp") or p.get("lastUpdated")
                or p.get("updatedAt") or p.get("createdAt")
            ),
            "end_date": p.get("endDate", ""),
            # Phase D: data di scadenza ISO per filtro capital-lock
            "end_date_iso": p.get("endDate", ""),
            # Notional in USDC che il wallet ha investito nella posizione
            "notional_usdc": size * avg_price,
        }

    # ----------------------------------------------------------------
    # Phase D: filtro liquidita / scadenza
    # ----------------------------------------------------------------
    def get_book(self, token_id: str) -> Optional[Dict]:
        """
        Order book del token via CLOB. Returns best bid/ask con size, o None.
        Usato dal filtro liquidita in ingresso (Phase D).
        """
        try:
            url = f"{self.clob}/book"
            r = self.session.get(url, params={"token_id": token_id}, timeout=10)
            if not r.ok:
                return None
            data = r.json()
            # FIX: il CLOB /book di Polymarket restituisce bids in ASC (best = MAX
            # price) e asks in DESC (best = MIN price). Prendere bids[0]/asks[0]
            # era ERRATO (selezionava il peggior prezzo -> spread ~0.98 finto).
            bids = data.get("bids") or []
            asks = data.get("asks") or []
            best_bid = None; best_ask = None
            bid_size = 0.0; ask_size = 0.0
            for b in bids:
                pr = float(b["price"])
                if best_bid is None or pr > best_bid:
                    best_bid = pr; bid_size = float(b["size"])
            for a in asks:
                pr = float(a["price"])
                if best_ask is None or pr < best_ask:
                    best_ask = pr; ask_size = float(a["size"])
            bid_levels = sorted(
                [{"price": float(x["price"]), "size": float(x["size"])} for x in bids],
                key=lambda x: x["price"], reverse=True,
            )
            ask_levels = sorted(
                [{"price": float(x["price"]), "size": float(x["size"])} for x in asks],
                key=lambda x: x["price"],
            )
            return {
                "best_bid": best_bid,
                "best_ask": best_ask,
                "bid_size": bid_size,
                "ask_size": ask_size,
                "mid": ((best_bid + best_ask) / 2) if (best_bid is not None and best_ask is not None) else None,
                "spread": ((best_ask - best_bid) if (best_bid is not None and best_ask is not None) else None),
                "bid_levels": bid_levels,
                "ask_levels": ask_levels,
            }
        except Exception:
            return None

    @staticmethod
    def passes_liquidity(book: Optional[Dict], side_size_min: float,
                         max_spread_ticks: float = 3.0) -> bool:
        """Phase D: check liquidita su book (size + spread)."""
        if book is None:
            return False  # niente book = non entri su mercato illiquido/sconosciuto
        bs = book.get("bid_size", 0.0)
        asz = book.get("ask_size", 0.0)
        spread = book.get("spread")
        if max(bs, asz) < side_size_min:
            return False
        if spread is None or spread > (max_spread_ticks * 0.01):
            return False
        return True

    @staticmethod
    def days_to_expiry(end_date_iso: str) -> Optional[float]:
        """Phase D: giorni alla scadenza mercato; None se non parseable."""
        if not end_date_iso:
            return None
        try:
            from datetime import datetime, timezone
            # Gestisce Z e offset
            s = end_date_iso.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                from datetime import timezone as tz
                dt = dt.replace(tzinfo=tz.utc)
            now = datetime.now(timezone.utc)
            return (dt - now).total_seconds() / 86400.0
        except Exception:
            return None

    def get_price(self, token_id: str) -> Optional[float]:
        """
        Compatibilità legacy: ritorna il best bid eseguibile, mai il midpoint.
        Per nuovi call-site usare esplicitamente `get_executable_price`.
        """
        return self.get_executable_price(token_id, "SELL")

    def get_executable_price(self, token_id: str, side: str,
                             size_shares: float = 0.0) -> Optional[float]:
        """VWAP attraversabile ora; con size=0 ritorna il top of book."""
        book = self.get_book(token_id)
        if not book:
            return None
        key = "best_ask" if side.upper() == "BUY" else "best_bid"
        if size_shares and size_shares > 0:
            levels_key = "ask_levels" if side.upper() == "BUY" else "bid_levels"
            remaining = float(size_shares)
            notional = 0.0
            filled = 0.0
            for level in book.get(levels_key, []):
                take = min(remaining, float(level["size"]))
                notional += take * float(level["price"])
                filled += take
                remaining -= take
                if remaining <= 1e-9:
                    break
            if remaining > 1e-9 or filled <= 0:
                return None
            return notional / filled
        value = book.get(key)
        return float(value) if value is not None else None

    # ----------------------------------------------------------------
    # Phase M: scanning mercati attivi per strategie arb/harvest (gamma)
    # ----------------------------------------------------------------
    @staticmethod
    def _parse_json_list(raw):
        """gamma restituisce outcomes/clobTokenIds come stringa JSON."""
        if raw is None:
            return []
        if isinstance(raw, list):
            return raw
        if isinstance(raw, str):
            try:
                v = _json.loads(raw)
                return v if isinstance(v, list) else []
            except Exception:
                return []
        return []

    def get_market(self, condition_id: str) -> Optional[Dict]:
        """Mercato singolo via gamma (conditionId) con outcomes/tokens parsati."""
        try:
            url = f"{self.gamma_api}/markets"
            r = self.session.get(url, params={"condition_ids": condition_id},
                                 timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            if not r.ok:
                return None
            arr = r.json()
            if not arr:
                return None
            m = arr[0]
        except Exception:
            return None
        return self._normalize_market(m)

    @classmethod
    def _normalize_market(cls, m: Dict) -> Dict:
        outcomes = cls._parse_json_list(m.get("outcomes"))
        tokens = cls._parse_json_list(m.get("clobTokenIds"))
        events = m.get("events") or []
        event_slug = events[0].get("slug", "") if events else ""
        event_ticker = events[0].get("ticker", "") if events else ""
        event_id = str(events[0].get("id", "")) if events else ""
        event_title = events[0].get("title", "") if events else ""
        tags = (events[0].get("tags") or []) if events else []
        tags = tags or m.get("tags") or []
        title = m.get("question") or m.get("title", "")
        return {
            "condition_id": m.get("conditionId", ""),
            "question": title,
            "slug": m.get("slug", ""),
            "event_slug": event_slug,
            "event_id": event_id,
            "event_title": event_title,
            "event_ticker": event_ticker,
            "tags": tags,
            "outcomes": outcomes,        # ["Yes","No"]
            "tokens": tokens,             # [asset_yes, asset_no]
            "end_date": m.get("endDate", ""),
            "fee_type": m.get("feeType", ""),
            "volume": float(m.get("volumeNum", 0) or 0),
            "closed": bool(m.get("closed", False)),
            "category": categorize_market(question=title, event_ticker=event_ticker,
                                           event_slug=event_slug,
                                           fee_type=m.get("feeType", ""),
                                           tags=tags),
        }

    def get_active_markets(self, limit: int = 100, min_volume: float = 1000.0) -> List[Dict]:
        """
        Mercati attivi (non closed) ordinati per volume — candidati arb/harvest.
        """
        try:
            url = f"{self.gamma_api}/markets"
            params = {"closed": "false", "active": "true",
                      "order": "volumeNum", "ascending": "false",
                      "limit": limit}
            r = self.session.get(url, params=params, timeout=25,
                                 headers={"User-Agent": "Mozilla/5.0"})
            if not r.ok:
                return []
            out = []
            for m in r.json():
                mk = self._normalize_market(m)
                if not mk["condition_id"]:
                    continue
                if mk["volume"] < min_volume:
                    continue
                if len(mk["tokens"]) < 2 and len(mk["outcomes"]) < 2:
                    continue  # serve almeno una coppia YES/NO
                out.append(mk)
            return out
        except Exception as e:
            print(f"[SYNC] get_active_markets errore: {e}")
            return []

    def get_event_markets(self, event_slug: str) -> List[Dict]:
        """
        Tutti i sotto-mercati di un evento esaustivo (es. world-cup-winner x32).
        Usato da arb_cross (Phase P)."""
        try:
            url = f"{self.gamma_api}/events"
            r = self.session.get(url, params={"slug": event_slug, "limit": 5},
                                 timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            if not r.ok or not r.json():
                return []
            ev = r.json()[0]
            out = []
            for m in ev.get("markets", []):
                mk = self._normalize_market(m)
                if mk["condition_id"] and len(mk["tokens"]) >= 2:
                    out.append(mk)
            return out
        except Exception:
            return []

    def get_active_events(self, limit: int = 25) -> List[Dict]:
        """Eventi popolari per arb_cross (slug + n sotto-mercati)."""
        try:
            url = f"{self.gamma_api}/events"
            r = self.session.get(url, params={"closed": "false", "active": "true",
                                              "order": "volumeNum", "ascending": "false",
                                              "limit": limit}, timeout=25,
                                 headers={"User-Agent": "Mozilla/5.0"})
            if not r.ok:
                return []
            out = []
            for ev in r.json():
                markets = ev.get("markets", [])
                if len(markets) >= 3:
                    out.append({"slug": ev.get("slug", ""), "title": ev.get("title", ""),
                                "n_markets": len(markets),
                                "volume": float(ev.get("volumeNum", 0) or 0)})
            return out
        except Exception:
            return []

    def snapshot_wallets(self, wallet_addresses: List[str]) -> Dict[str, Dict]:
        """
        Aggrega le posizioni di piu wallet per asset.

        Returns:
            Dict asset -> {
                "info": <ultima posizione normalizzata per quell'asset>,
                "holders": set(wallet_address),
                "max_notional": float
            }
        """
        aggregate: Dict[str, Dict] = {}
        for addr in wallet_addresses:
            for pos in self.get_positions(addr):
                asset = pos["asset"]
                entry = aggregate.get(asset)
                if entry is None:
                    aggregate[asset] = {
                        "info": pos,
                        "holders": {addr},
                        "max_notional": pos["notional_usdc"],
                    }
                else:
                    entry["holders"].add(addr)
                    entry["max_notional"] = max(entry["max_notional"], pos["notional_usdc"])
                    # Tieni l'info con prezzo corrente piu aggiornato (sono uguali, ma
                    # preferiamo quella con redeemable=True se presente)
                    if pos["redeemable"]:
                        entry["info"] = pos
        return aggregate


if __name__ == "__main__":
    fetcher = PolymarketPositionFetcher()
    test = "0x664ce9fb97ae1bbd538d7381b2f4e92dab16f49c"
    for pos in fetcher.get_positions(test)[:5]:
        print(f"{pos['title'][:40]:40} | {pos['outcome']:6} | "
              f"cur={pos['cur_price']:.3f} | redeemable={pos['redeemable']}")
