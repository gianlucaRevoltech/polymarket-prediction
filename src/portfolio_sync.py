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
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from config import POLYMARKET_API, STRATEGY, TRACKING
from categories import categorize_market, normalize_fee_schedule
from time_utils import utc_iso, utc_now_iso


@dataclass
class PositionsFetchResult:
    """Esito non ambiguo di uno snapshot `/positions` per un wallet."""

    wallet: str
    ok: bool
    positions: List[Dict] = field(default_factory=list)
    error: str = ""
    transient: bool = False


@dataclass
class WalletSnapshotResult:
    """Snapshot aggregato con copertura esplicita dei wallet richiesti."""

    aggregate: Dict[str, Dict] = field(default_factory=dict)
    successful_wallets: Set[str] = field(default_factory=set)
    failed_wallets: Dict[str, str] = field(default_factory=dict)


@dataclass
class RecentBuyResult:
    """Esito lookup BUY: `ok`, `not_found` oppure `error`."""

    status: str
    trade: Optional[Dict] = None
    error: str = ""


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
        self._data_last_request_monotonic = 0.0
        self._data_blocked_until_monotonic = 0.0
        self._data_consecutive_transient_errors = 0
        self._feed_status_codes: Counter = Counter()
        self._feed_health = {
            "requests": 0,
            "successful_requests": 0,
            "transient_errors": 0,
            "rate_limit_errors": 0,
            "network_errors": 0,
            "snapshot_cycles": 0,
            "partial_snapshot_cycles": 0,
            "fully_failed_snapshot_cycles": 0,
            "wallet_reads_ok": 0,
            "wallet_reads_failed": 0,
            "last_success_at": None,
            "last_error_at": None,
            "last_error": "",
            "backoff_seconds": 0.0,
            "last_snapshot_at": None,
            "last_snapshot_status": "unknown",
            "last_snapshot_wallets_ok": [],
            "last_snapshot_wallets_failed": [],
            "consecutive_complete_snapshots": 0,
            "consecutive_incomplete_snapshots": 0,
            "consecutive_failed_snapshots": 0,
        }

    def _wait_for_data_api_slot(self) -> None:
        """Pacing unico per il Data API, incluso il cooldown dopo errori."""
        now = time.monotonic()
        min_interval = max(
            0.0, float(TRACKING.get("data_api_min_interval_sec", 0.10))
        )
        ready_at = max(
            self._data_blocked_until_monotonic,
            self._data_last_request_monotonic + min_interval,
        )
        if ready_at > now:
            time.sleep(ready_at - now)

    def _record_data_api_failure(self, error: str, status_code=None,
                                 transient: bool = False) -> None:
        self._feed_health["last_error_at"] = utc_now_iso()
        self._feed_health["last_error"] = str(error)
        if status_code is not None:
            self._feed_status_codes[str(status_code)] += 1
        if transient:
            self._feed_health["transient_errors"] += 1
            self._data_consecutive_transient_errors += 1
            if status_code == 429:
                self._feed_health["rate_limit_errors"] += 1
            base = max(
                0.0, float(TRACKING.get("data_api_backoff_base_sec", 1.0))
            )
            ceiling = max(
                base, float(TRACKING.get("data_api_backoff_max_sec", 30.0))
            )
            delay = min(
                ceiling,
                base * (2 ** min(self._data_consecutive_transient_errors - 1, 5)),
            )
            self._data_blocked_until_monotonic = max(
                self._data_blocked_until_monotonic, time.monotonic() + delay
            )
            self._feed_health["backoff_seconds"] = delay

    def _data_get(self, path: str, *, params: Dict, timeout):
        """GET Data API con pacing e backoff fail-closed tra le richieste."""
        self._wait_for_data_api_slot()
        self._feed_health["requests"] += 1
        response = None
        try:
            response = self.session.get(
                f"{self.data_api}/{path.lstrip('/')}",
                params=params,
                timeout=timeout,
            )
            self._data_last_request_monotonic = time.monotonic()
            if not response.ok:
                status_code = getattr(response, "status_code", None)
                transient = (
                    status_code in {408, 425, 429}
                    or (isinstance(status_code, int) and status_code >= 500)
                )
                self._record_data_api_failure(
                    f"HTTP {status_code}", status_code, transient
                )
            else:
                self._feed_health["successful_requests"] += 1
                self._feed_health["last_success_at"] = utc_now_iso()
                self._feed_health["backoff_seconds"] = 0.0
                self._data_consecutive_transient_errors = 0
                self._data_blocked_until_monotonic = 0.0
            return response
        except Exception as exc:
            self._data_last_request_monotonic = time.monotonic()
            transient = isinstance(
                exc, (requests.Timeout, requests.ConnectionError)
            )
            if transient:
                self._feed_health["network_errors"] += 1
            self._record_data_api_failure(str(exc), None, transient)
            raise

    def get_feed_health(self) -> Dict:
        """Snapshot serializzabile della salute feed del processo corrente."""
        health = dict(self._feed_health)
        health["status_codes"] = dict(self._feed_status_codes)
        health["consecutive_transient_errors"] = (
            self._data_consecutive_transient_errors
        )
        health["backoff_remaining_seconds"] = max(
            0.0, self._data_blocked_until_monotonic - time.monotonic()
        )
        return health

    def get_positions_result(self, wallet_address: str,
                             limit: int = 500) -> PositionsFetchResult:
        """
        Ritorna uno snapshot strutturato. Una lista vuota con `ok=True` prova
        che il wallet non ha posizioni; `ok=False` non è mai una vendita.
        """
        page_size = min(max(int(limit), 1), 500)
        offset = 0
        raw = []
        while True:
            response = None
            try:
                params = {
                    "user": wallet_address,
                    "limit": page_size,
                    "offset": offset,
                    "sortBy": "TOKENS",
                    "sortDirection": "DESC",
                }
                response = self._data_get(
                    "positions", params=params, timeout=(5, 10)
                )
                response.raise_for_status()
                page = response.json()
            except Exception as exc:
                status_code = getattr(response, "status_code", None)
                transient = (
                    isinstance(exc, (requests.Timeout, requests.ConnectionError))
                    or status_code in {408, 425, 429}
                    or (isinstance(status_code, int) and status_code >= 500)
                )
                print(f"[SYNC] Errore positions {wallet_address[:10]}...: {exc}")
                return PositionsFetchResult(
                    wallet=wallet_address,
                    ok=False,
                    error=str(exc),
                    transient=transient,
                )

            if not isinstance(page, list):
                error = f"payload inatteso: {type(page).__name__}"
                print(f"[SYNC] Errore positions {wallet_address[:10]}...: {error}")
                return PositionsFetchResult(
                    wallet=wallet_address, ok=False, error=error
                )
            raw.extend(page)
            if len(page) < page_size:
                break
            if offset >= 10000:
                error = "snapshot incompleto: superato offset massimo 10000"
                print(f"[SYNC] Errore positions {wallet_address[:10]}...: {error}")
                return PositionsFetchResult(
                    wallet=wallet_address, ok=False, error=error
                )
            offset += page_size

        positions = []
        try:
            for p in raw:
                norm = self._normalize(p)
                # Scarta dust / posizioni vuote
                if norm["asset"] and norm["size"] > 0:
                    positions.append(norm)
        except Exception as exc:
            error = f"payload positions non valido: {exc}"
            print(f"[SYNC] Errore positions {wallet_address[:10]}...: {error}")
            return PositionsFetchResult(
                wallet=wallet_address, ok=False, error=error
            )
        return PositionsFetchResult(
            wallet=wallet_address, ok=True, positions=positions
        )

    def get_positions(self, wallet_address: str, limit: int = 500) -> List[Dict]:
        """Wrapper legacy: i nuovi call-site devono usare l'esito strutturato."""
        return self.get_positions_result(wallet_address, limit).positions

    def get_recent_buy_result(self, wallet_address: str, asset: str,
                              limit: int = 500) -> RecentBuyResult:
        """Trova il BUY sorgente senza confondere assenza valida ed errore API."""
        if not wallet_address or not asset:
            return RecentBuyResult(status="not_found")
        try:
            params = {
                "user": wallet_address,
                "type": "TRADE",
                "side": "BUY",
                "limit": min(max(int(limit), 1), 500),
                "offset": 0,
                "sortBy": "TIMESTAMP",
                "sortDirection": "DESC",
            }
            response = self._data_get(
                "activity", params=params, timeout=15
            )
            response.raise_for_status()
            matches = [
                row for row in response.json()
                if str(row.get("asset", "")) == str(asset)
                and str(row.get("side", "")).upper() == "BUY"
                and str(row.get("type", "TRADE")).upper() == "TRADE"
            ]
            if not matches:
                return RecentBuyResult(status="not_found")
            row = max(matches, key=lambda item: float(item.get("timestamp", 0) or 0))
            return RecentBuyResult(status="ok", trade={
                "transaction_hash": row.get("transactionHash", ""),
                "source_trade_at": utc_iso(row.get("timestamp")),
                "source_trade_price": float(row.get("price", 0) or 0),
                # `size` e' numero di shares; solo `usdcSize` e' confrontabile
                # con la size paper in dollari. Se manca, il gate fallisce chiuso.
                "source_trade_size": float(row.get("usdcSize", 0) or 0),
            })
        except Exception as exc:
            print(f"[SYNC] Errore activity BUY {wallet_address[:10]}...: {exc}")
            return RecentBuyResult(status="error", error=str(exc))

    def get_recent_buy(self, wallet_address: str, asset: str,
                       limit: int = 500) -> Optional[Dict]:
        """Wrapper legacy che ritorna il trade solo quando il lookup è riuscito."""
        result = self.get_recent_buy_result(wallet_address, asset, limit)
        return result.trade if result.status == "ok" else None

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
    @staticmethod
    def _normalize_book(data: Dict) -> Dict:
        """Normalizza un book CLOB singolo o proveniente da ``POST /books``."""
        bids = data.get("bids") or []
        asks = data.get("asks") or []
        bid_levels = sorted(
            [
                {"price": float(level["price"]), "size": float(level["size"])}
                for level in bids
                if float(level.get("size", 0) or 0) > 0
            ],
            key=lambda level: level["price"], reverse=True,
        )
        ask_levels = sorted(
            [
                {"price": float(level["price"]), "size": float(level["size"])}
                for level in asks
                if float(level.get("size", 0) or 0) > 0
            ],
            key=lambda level: level["price"],
        )
        best_bid = bid_levels[0]["price"] if bid_levels else None
        best_ask = ask_levels[0]["price"] if ask_levels else None
        return {
            "observed_at": utc_now_iso(),
            "best_bid": best_bid,
            "best_ask": best_ask,
            "bid_size": bid_levels[0]["size"] if bid_levels else 0.0,
            "ask_size": ask_levels[0]["size"] if ask_levels else 0.0,
            "mid": (
                (best_bid + best_ask) / 2
                if best_bid is not None and best_ask is not None else None
            ),
            "spread": (
                best_ask - best_bid
                if best_bid is not None and best_ask is not None else None
            ),
            "bid_levels": bid_levels,
            "ask_levels": ask_levels,
        }

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
            return self._normalize_book(r.json())
        except Exception:
            return None

    def get_books(self, token_ids: List[str]) -> Dict[str, Dict]:
        """Book CLOB batch, indicizzati per asset id; fallimento = mapping vuoto."""
        unique = list(dict.fromkeys(str(token) for token in token_ids if token))
        if not unique:
            return {}
        result: Dict[str, Dict] = {}
        try:
            for start in range(0, len(unique), 500):
                batch = unique[start:start + 500]
                response = self.session.post(
                    f"{self.clob}/books",
                    json=[{"token_id": token} for token in batch],
                    timeout=15,
                )
                if not response.ok:
                    return {}
                payload = response.json()
                if not isinstance(payload, list):
                    return {}
                for raw in payload:
                    asset = str(raw.get("asset_id") or raw.get("assetId") or "")
                    if asset:
                        result[asset] = self._normalize_book(raw)
        except Exception:
            return {}
        return result

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
        outcome_prices = cls._parse_json_list(m.get("outcomePrices"))
        events = m.get("events") or []
        event_slug = events[0].get("slug", "") if events else ""
        event_ticker = events[0].get("ticker", "") if events else ""
        event_id = str(events[0].get("id", "")) if events else ""
        event_title = events[0].get("title", "") if events else ""
        tags = (events[0].get("tags") or []) if events else []
        tags = tags or m.get("tags") or []
        title = m.get("question") or m.get("title", "")
        raw_fees_enabled = m.get("feesEnabled", m.get("fees_enabled"))
        if isinstance(raw_fees_enabled, str):
            fees_enabled = raw_fees_enabled.strip().lower() in {
                "1", "true", "yes", "on"
            }
        elif raw_fees_enabled is None:
            fees_enabled = None
        else:
            fees_enabled = bool(raw_fees_enabled)
        fee_schedule = normalize_fee_schedule(
            m.get("feeSchedule", m.get("fee_schedule"))
        )
        if fees_enabled is None and fee_schedule is not None:
            fees_enabled = float(fee_schedule.get("rate", 0.0)) > 0
        fee_metadata_known = (
            fees_enabled is False
            or (fees_enabled is True and fee_schedule is not None)
        )
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
            "outcome_prices": outcome_prices,
            "end_date": m.get("endDate", ""),
            "fee_type": m.get("feeType", ""),
            "fees_enabled": fees_enabled,
            "fee_schedule": fee_schedule,
            "fee_metadata_known": fee_metadata_known,
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

    def snapshot_wallets_with_status(
        self, wallet_addresses: List[str]
    ) -> WalletSnapshotResult:
        """
        Aggrega le posizioni e conserva l'esito di ogni wallet. Gli errori non
        vengono trasformati in snapshot vuoti.
        """
        self._feed_health["snapshot_cycles"] += 1
        aggregate: Dict[str, Dict] = {}
        successful_wallets: Set[str] = set()
        failed_wallets: Dict[str, str] = {}
        consecutive_transient_failures = 0
        max_transient_failures = 3
        for index, addr in enumerate(wallet_addresses):
            result = self.get_positions_result(addr)
            if not result.ok:
                failed_wallets[addr] = result.error
                consecutive_transient_failures = (
                    consecutive_transient_failures + 1
                    if result.transient else 0
                )
                if consecutive_transient_failures >= max_transient_failures:
                    reason = (
                        "snapshot saltato dopo 3 errori transitori consecutivi"
                    )
                    for remaining in wallet_addresses[index + 1:]:
                        failed_wallets[remaining] = reason
                    print(
                        f"[SYNC] Circuit breaker feed: {reason}; "
                        f"{len(wallet_addresses) - index - 1} wallet rinviati"
                    )
                    break
                continue
            consecutive_transient_failures = 0
            successful_wallets.add(addr)
            for pos in result.positions:
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
        self._feed_health["wallet_reads_ok"] += len(successful_wallets)
        self._feed_health["wallet_reads_failed"] += len(failed_wallets)
        if failed_wallets:
            self._feed_health["partial_snapshot_cycles"] += 1
        if wallet_addresses and not successful_wallets:
            self._feed_health["fully_failed_snapshot_cycles"] += 1
        complete = bool(wallet_addresses) and not failed_wallets
        failed = bool(wallet_addresses) and not successful_wallets
        self._feed_health.update({
            "last_snapshot_at": utc_now_iso(),
            "last_snapshot_status": "complete" if complete else ("failed" if failed else "partial"),
            "last_snapshot_wallets_ok": sorted(successful_wallets),
            "last_snapshot_wallets_failed": sorted(failed_wallets),
            "consecutive_complete_snapshots": self._feed_health["consecutive_complete_snapshots"] + 1 if complete else 0,
            "consecutive_incomplete_snapshots": self._feed_health["consecutive_incomplete_snapshots"] + 1 if not complete else 0,
            "consecutive_failed_snapshots": self._feed_health["consecutive_failed_snapshots"] + 1 if failed else 0,
        })
        return WalletSnapshotResult(
            aggregate=aggregate,
            successful_wallets=successful_wallets,
            failed_wallets=failed_wallets,
        )

    def snapshot_wallets(self, wallet_addresses: List[str]) -> Dict[str, Dict]:
        """Wrapper legacy: ritorna solo l'aggregato degli snapshot riusciti."""
        return self.snapshot_wallets_with_status(wallet_addresses).aggregate


if __name__ == "__main__":
    fetcher = PolymarketPositionFetcher()
    test = "0x664ce9fb97ae1bbd538d7381b2f4e92dab16f49c"
    for pos in fetcher.get_positions(test)[:5]:
        print(f"{pos['title'][:40]:40} | {pos['outcome']:6} | "
              f"cur={pos['cur_price']:.3f} | redeemable={pos['redeemable']}")
