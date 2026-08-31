"""Public, serial, bounded research reads. Cache lives only in audit output."""
from __future__ import annotations

import hashlib
import json
import time
from collections import deque
from pathlib import Path

import requests

from wallet_history import deduplicate, number, row_key

API = "https://data-api.polymarket.com"
CATEGORIES = ("OVERALL", "POLITICS", "SPORTS", "CRYPTO", "WEATHER", "ECONOMICS",
              "FINANCE", "CULTURE", "MENTIONS", "TECH", "ESPORTS")


class FeedError(ValueError):
    pass


class PublicResearchClient:
    def __init__(self, output, *, session=None, sleep=time.sleep, clock=time.monotonic,
                 max_requests=20000, offline=False):
        self.cache = Path(output) / "cache"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.session = session or requests.Session()
        self.sleep, self.clock = sleep, clock
        self.last_request = None
        self.requests = 0
        self.cache_hits = 0
        self.max_requests = max_requests
        self.offline = offline
        self.failures = []
        self.transport_block = None

    def get(self, endpoint, params):
        if endpoint not in {"/activity", "/closed-positions", "/positions", "/v1/leaderboard"}:
            raise FeedError("endpoint not public/read-only allowlist")
        key = hashlib.sha256(json.dumps([endpoint, params], sort_keys=True).encode()).hexdigest()
        path = self.cache / f"{key}.json"
        if path.exists():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(cached["rows"], list) or cached["params"] != params or cached["endpoint"] != endpoint:
                    raise ValueError("cache mismatch")
                self.cache_hits += 1
                return cached["rows"]
            except (ValueError, KeyError, TypeError):
                raise FeedError("invalid_cache")
        if self.offline:
            raise FeedError("cache_missing_offline")
        if self.transport_block:
            raise FeedError(self.transport_block)
        for attempt in range(3):
            if self.requests >= self.max_requests:
                raise FeedError("request_budget_exhausted")
            if self.last_request is not None:
                self.sleep(max(0, .5 - (self.clock() - self.last_request)))
            self.last_request = self.clock()
            self.requests += 1
            response = None
            try:
                response = self.session.get(API + endpoint, params=params, timeout=20)
                response.raise_for_status()
                rows = response.json()
                if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
                    raise FeedError("invalid_response")
                payload = {"endpoint": endpoint, "params": params, "fetched_at": time.time(), "rows": rows}
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(payload, allow_nan=False), encoding="utf-8")
                tmp.replace(path)
                return rows
            except requests.exceptions.SSLError as exc:
                # Certificate failures are not transient rate limits. Never
                # downgrade verification or repeat across hundreds of wallets.
                self.transport_block = "tls_verification_failed"
                self.failures.append(self.transport_block)
                raise FeedError(self.transport_block) from exc
            except (requests.RequestException, ValueError) as exc:
                code = getattr(response, "status_code", None)
                retryable = code is None or code in (408, 425, 429) or code >= 500
                if attempt == 2 or not retryable:
                    reason = f"{endpoint}:http_{code}:{type(exc).__name__}"
                    self.failures.append(reason)
                    raise FeedError(reason) from exc
                delay = 2 ** (attempt + 1)
                try:
                    delay = max(delay, number(response.headers.get("Retry-After", delay)))
                except (AttributeError, ValueError, TypeError):
                    pass
                # No busy retry, and no single blocking sleep over 60 seconds.
                if delay > 60:
                    raise FeedError("retry_after_exceeds_budget")
                self.sleep(delay)
        raise FeedError("unreachable")

    def activity(self, wallet, start, end, *, max_pages=600):
        """Split dense time windows before offset exceeds 5000; never truncate."""
        pages = 0

        def window(lo, hi):
            nonlocal pages
            rows, keys = [], set()
            for offset in range(0, 5001, 500):
                if pages >= max_pages:
                    raise FeedError("activity_page_budget_exhausted")
                page = self.get("/activity", {"user": wallet, "start": lo, "end": hi,
                                "sortBy": "TIMESTAMP", "sortDirection": "ASC",
                                "limit": 500, "offset": offset})
                pages += 1
                for row in page:
                    ts = number(row.get("timestamp"))
                    if not lo <= ts <= hi:
                        raise FeedError("activity_outside_window")
                incoming = {row_key(r) for r in page}
                if page and incoming <= keys:
                    raise FeedError("activity_repeated_page")
                keys |= incoming
                rows.extend(page)
                if len(page) < 500:
                    return deduplicate(rows)
            if lo == hi:
                raise FeedError("activity_single_second_overflow")
            middle = (lo + hi) // 2
            # Discard parent pages, reread disjoint children with their own offset budget.
            return window(lo, middle) + window(middle + 1, hi)

        try:
            rows = window(int(start), int(end))
            return {"rows": deduplicate(rows), "coverage": "complete", "errors": [], "pages": pages}
        except (FeedError, ValueError, TypeError) as exc:
            return {"rows": [], "coverage": "unknown", "errors": [str(exc)], "pages": pages}

    def positions(self, wallet, *, closed=False, start=None, max_pages=400):
        endpoint = "/closed-positions" if closed else "/positions"
        limit, offset_cap = (50, 100000) if closed else (500, 10000)
        rows, keys = [], set()
        try:
            for page_index in range(max_pages):
                offset = page_index * limit
                if offset > offset_cap:
                    raise FeedError("positions_offset_cap")
                params = {"user": wallet, "limit": limit, "offset": offset}
                if closed:
                    params.update(sortBy="TIMESTAMP", sortDirection="DESC")
                else:
                    params["sizeThreshold"] = 0
                page = self.get(endpoint, params)
                incoming = {json.dumps(r, sort_keys=True) for r in page}
                if page and incoming <= keys:
                    raise FeedError("positions_repeated_page")
                keys |= incoming
                stop = False
                for row in page:
                    if closed and start is not None and number(row.get("timestamp")) < start:
                        stop = True
                    else:
                        rows.append(row)
                if len(page) < limit or stop:
                    return {"rows": rows, "coverage": "complete", "errors": []}
            raise FeedError("positions_page_budget_exhausted")
        except (FeedError, ValueError, TypeError) as exc:
            return {"rows": rows, "coverage": "unknown", "errors": [str(exc)]}

    def discover(self):
        sources, errors = {}, []
        for category in CATEGORIES:
            for period in ("WEEK", "MONTH"):
                for ordering in ("PNL", "VOL"):
                    source = f"leaderboard:{category}:{period}:{ordering}"
                    try:
                        sources[source] = self.get("/v1/leaderboard", {
                            "category": category, "timePeriod": period,
                            "orderBy": ordering, "limit": 50, "offset": 0})
                    except FeedError as exc:
                        errors.append(f"{source}:{exc}")
        return sources, errors


def round_robin_candidates(current, sources, cap=300):
    """Current cohort never consumes the alternative-discovery budget."""
    out, seen = [], set()
    for row in current:
        addr = str(row.get("address", "")).lower()
        if addr and addr not in seen:
            out.append(dict(row, address=addr, name=row.get("name") or addr,
                            current=True, discovery_sources=["current_manifest"]))
            seen.add(addr)
    pools = [(name, deque(rows)) for name, rows in sorted(sources.items())]
    added = 0
    while pools and added < cap:
        next_pools = []
        for name, pool in pools:
            while pool:
                row = pool.popleft()
                addr = str(row.get("address") or row.get("proxyWallet") or "").lower()
                if addr in seen:
                    for item in out:
                        if item["address"] == addr and name not in item["discovery_sources"]:
                            item["discovery_sources"].append(name)
                    continue
                if len(addr) != 42 or not addr.startswith("0x"):
                    continue
                try:
                    int(addr[2:], 16)
                except ValueError:
                    continue
                out.append(dict(row, address=addr, name=row.get("name") or row.get("userName") or addr,
                                current=False, discovery_sources=[name]))
                seen.add(addr)
                added += 1
                break
            if pool:
                next_pools.append((name, pool))
            if added >= cap:
                break
        pools = next_pools
    return out
