"""Pure, conservative wallet history accounting. Never estimates copy returns.

Amounts are public activity cashflows; their fee coverage is not guaranteed.
Official closed-position totals corroborate, rather than replace, cost basis.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone

from categories import categorize_market

METHOD = "wallet_history_v2"
EPS = 1e-6
INCENTIVES = {"REWARD", "MAKER_REBATE", "TAKER_REBATE", "YIELD", "REFERRAL_REWARD"}
NON_DIRECTIONAL = {"DEPOSIT", "WITHDRAWAL"}


def number(value):
    if isinstance(value, bool):
        raise ValueError("boolean amount")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("nonfinite amount")
    return result


def row_key(row):
    # One transaction may contain many assets/actions/fills. Hash alone is NOT a key.
    fields = ("transactionHash", "logIndex", "asset", "conditionId", "type",
              "side", "size", "usdcSize", "timestamp", "outcomeIndex")
    payload = {k: row.get(k) for k in fields}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def deduplicate(rows):
    seen, result = set(), []
    for row in rows:
        key = row_key(row)
        if key not in seen:
            result.append(row)
            seen.add(key)
    return result


def reconstruct(activity, posmap=None, official_closed=None, *, reconcile=False):
    """Return cycles and asset aggregates; bad basis is excluded, not fabricated.

    A finite window has no assumed opening inventory. Sales outside its known
    basis contaminate the asset. Complete official totals/current inventory are
    mandatory for research qualification, not for the legacy diagnostic adapter.
    """
    posmap = posmap or {}
    states, errors, cycles, entries = {}, [], [], []
    incentive_total = 0.0
    rows = []
    for raw in activity:
        try:
            if not isinstance(raw, dict):
                raise ValueError("row non object")
            row = dict(raw)
            row["timestamp"] = number(row.get("timestamp"))
            if row["timestamp"] < 0:
                raise ValueError("negative timestamp")
            rows.append(row)
        except (ValueError, TypeError):
            errors.append("invalid_activity_row")
    rows = sorted(deduplicate(rows), key=lambda r: r["timestamp"])

    def state(asset, row):
        if asset not in states:
            states[asset] = {
                "asset": asset, "title": row.get("title", ""),
                "outcome": row.get("outcome", ""),
                "condition_id": row.get("conditionId", ""),
                "event_slug": row.get("eventSlug", ""),
                "category": categorize_market(question=row.get("title", ""),
                                               event_slug=row.get("eventSlug", "")),
                "shares": 0.0, "cost": 0.0, "bought": 0.0, "shares_bought": 0.0,
                "realized_pnl": 0.0, "first_buy_ts": None, "cycle_buy_ts": None,
                "cycle_bought": 0.0, "cycle_pnl": 0.0, "quality_errors": [],
                "verified": False,
            }
        return states[asset]

    def bad(s, reason):
        s["quality_errors"].append(reason)
        errors.append(f"{reason}:{s['asset']}")

    def close(s, timestamp, kind):
        cycles.append({k: s[k] for k in ("asset", "title", "outcome", "condition_id",
                                       "event_slug", "category")} | {
            "bought": s["cycle_bought"], "realized_pnl": s["cycle_pnl"],
            "opened_at": s["cycle_buy_ts"], "closed_at": timestamp, "close_type": kind,
        })
        s.update(shares=0.0, cost=0.0, cycle_bought=0.0, cycle_pnl=0.0, cycle_buy_ts=None)

    for row in rows:
        kind, asset = row.get("type"), str(row.get("asset") or "")
        if kind in NON_DIRECTIONAL:
            continue
        if kind in INCENTIVES:
            try:
                incentive_total += number(row.get("usdcSize"))
            except (ValueError, TypeError):
                errors.append("invalid_incentive")
            continue
        if kind not in {"TRADE", "REDEEM"}:
            errors.append(f"unsupported_operation:{kind}:{asset}")
            if asset:
                bad(state(asset, row), "unsupported_operation")
            continue
        if not asset:
            errors.append(f"missing_asset:{kind}")
            continue
        s = state(asset, row)
        if s["condition_id"] != row.get("conditionId", ""):
            bad(s, "condition_conflict")
        try:
            size, cash = number(row.get("size")), number(row.get("usdcSize"))
            if size <= 0 or cash < 0:
                raise ValueError("invalid size or cash")
        except (ValueError, TypeError):
            bad(s, "invalid_amount")
            continue
        side = row.get("side")
        if kind == "TRADE" and side == "BUY":
            flat = s["shares"] <= EPS
            if flat:
                s["cycle_buy_ts"] = row["timestamp"]
            entries.append({"asset": asset, "timestamp": row["timestamp"],
                            "notional": cash, "flat_to_buy": flat,
                            "category": s["category"]})
            s["shares"] += size
            s["cost"] += cash
            s["bought"] += cash
            s["cycle_bought"] += cash
            s["shares_bought"] += size
            if s["first_buy_ts"] is None:
                s["first_buy_ts"] = row["timestamp"]
        elif kind == "REDEEM" or (kind == "TRADE" and side == "SELL"):
            if size > s["shares"] + EPS:
                bad(s, "incomplete_cost_basis")
            matched = min(size, s["shares"])
            if matched <= EPS:
                continue
            cost = s["cost"] * matched / s["shares"]
            # Cap BOTH proceeds and inventory to known shares.
            proceeds = cash * matched / size
            pnl = proceeds - cost
            s["realized_pnl"] += pnl
            s["cycle_pnl"] += pnl
            s["shares"] = max(0.0, s["shares"] - matched)
            s["cost"] = max(0.0, s["cost"] - cost)
            if s["shares"] <= EPS:
                close(s, row["timestamp"], kind.lower())
        else:
            bad(s, "unknown_trade_side")

    official = {}
    conflicting_assets = set()
    for row in official_closed or []:
        asset = str(row.get("asset") or "")
        if not asset:
            errors.append("official_missing_asset")
        elif asset in official and official[asset] != row:
            errors.append(f"official_conflict:{asset}")
            conflicting_assets.add(asset)
        else:
            official[asset] = row

    for asset, s in states.items():
        if asset in conflicting_assets:
            bad(s, "official_conflict")
        info = posmap.get(asset)
        # redeemable + exact payout is official settlement evidence, unlike a
        # near-0/1 quote. Unknown settlement time cannot enter period statistics.
        if s["shares"] > EPS and info:
            try:
                cur = number(info.get("curPrice", info.get("cur_price")))
                if info.get("redeemable") is True and cur in (0.0, 1.0):
                    pnl = s["shares"] * cur - s["cost"]
                    s["realized_pnl"] += pnl
                    s["cycle_pnl"] += pnl
                    close(s, None, "verified_settlement_undated")
            except (TypeError, ValueError):
                bad(s, "invalid_current_mark")
        s["roi"] = s["realized_pnl"] / s["bought"] if s["bought"] else 0.0
        s["entry_price"] = s["bought"] / s["shares_bought"] if s["shares_bought"] else 0.0
        if reconcile:
            ref = official.get(asset) or info
            if not ref:
                bad(s, "official_position_missing")
                continue
            try:
                bought = number(ref.get("totalBought"))  # shares, not USDC
                pnl = number(ref.get("realizedPnl"))
                if not math.isclose(bought, s["shares_bought"], rel_tol=1e-5, abs_tol=1e-4):
                    bad(s, "official_basis_mismatch")
                if not math.isclose(pnl, s["realized_pnl"], rel_tol=1e-5, abs_tol=0.01):
                    bad(s, "official_pnl_mismatch")
                if info:
                    # Redeemable snapshot still holds settled tokens; otherwise
                    # even an unexpected nonzero inventory after SELL is an error.
                    settled = any(c["asset"] == asset and c["close_type"] == "verified_settlement_undated" for c in cycles)
                    if not settled and not math.isclose(number(info.get("size")), s["shares"], rel_tol=1e-5, abs_tol=1e-4):
                        bad(s, "official_inventory_mismatch")
                elif asset in official and s["shares"] > EPS:
                    bad(s, "official_closed_but_inventory_open")
            except (ValueError, TypeError):
                bad(s, "official_invalid_amount")
            s["verified"] = not s["quality_errors"]

    if reconcile:
        for asset in set(official) - set(states):
            errors.append(f"official_without_activity_basis:{asset}")
        for asset, ref in posmap.items():
            if asset not in states:
                try:
                    if number(ref.get("size")) > EPS:
                        errors.append(f"open_position_without_activity_basis:{asset}")
                except (ValueError, TypeError):
                    errors.append(f"official_invalid_amount:{asset}")
    valid_cycles = [c for c in cycles if not states[c["asset"]]["quality_errors"]
                    and (not reconcile or states[c["asset"]]["verified"])]
    closed = {a: s for a, s in states.items() if s["shares"] <= EPS
              and s["bought"] > 0 and not s["quality_errors"]}
    return {"method": METHOD, "states": states, "closed": closed, "cycles": valid_cycles,
            "entries": entries, "activity": rows, "incentives_usdc": incentive_total,
            "quality_errors": sorted(set(errors)), "fee_coverage": "unknown_public_cashflows",
            "copy_net_pnl": None}


def wilson(wins, n):
    if not n:
        return None
    z, p = 1.959963984540054, wins / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return [max(0, center - half), min(1, center + half)]


def pnl_metrics(cycles):
    values = [c["realized_pnl"] for c in cycles]
    wins, losses = [p for p in values if p > 0], [p for p in values if p < 0]
    grouped, contributions = defaultdict(float), defaultdict(float)
    for c in cycles:
        grouped[c.get("event_slug") or "unknown"] += c["realized_pnl"]
        contributions[c.get("event_slug") or "unknown"] += max(0, c["realized_pnl"])
    positives = {k: p for k, p in contributions.items() if p > 0}
    gross_win, gross_loss = sum(wins), -sum(losses)
    pf = gross_win / gross_loss if gross_loss else None
    return {"closed_positions": len(cycles), "wins": len(wins), "losses": len(losses),
            "breakeven": len(values) - len(wins) - len(losses),
            "win_rate": len(wins) / len(cycles) if cycles else None,
            "win_rate_ci95": wilson(len(wins), len(cycles)),
            "realized_pnl": sum(values), "average_win": gross_win / len(wins) if wins else None,
            "average_loss": -gross_loss / len(losses) if losses else None,
            "profit_factor": pf, "profit_factor_unbounded": bool(wins and not losses),
            "distinct_events": len(set(grouped) - {"unknown"}),
            "pnl_by_event": dict(grouped),
            "max_positive_event_share": max(positives.values()) / sum(positives.values()) if positives else None}


def window_metrics(result, start, end):
    cycles = [c for c in result["cycles"] if c["closed_at"] is not None
              and start <= c["closed_at"] <= end]
    rows = [r for r in result["activity"] if start <= r["timestamp"] <= end]
    buys = [e for e in result["entries"] if start <= e["timestamp"] <= end]
    qualifying = [e for e in buys if e["notional"] >= 5]
    verified = [e for e in buys if result["states"][e["asset"]]["verified"]]
    dates = lambda es: sorted({datetime.fromtimestamp(e["timestamp"], timezone.utc).date().isoformat() for e in es})
    metrics = pnl_metrics(cycles)
    metrics.update({"start": start, "end": end,
                    "transactions": sum(r.get("type") == "TRADE" for r in rows),
                    "buy_assets": len({e["asset"] for e in buys}),
                    "buy_assets_ge_5": len({e["asset"] for e in qualifying}),
                    "buy_days_ge_5": len(dates(qualifying)), "active_buy_days": len(dates(buys)),
                    "last_buy_at": max((e["timestamp"] for e in buys), default=None),
                    "verified_new_entries": sum(e["flat_to_buy"] for e in verified),
                    "verified_increments": sum(not e["flat_to_buy"] for e in verified),
                    "unverified_buys": len(buys) - len(verified),
                    "by_domain": {d: pnl_metrics([c for c in cycles if c["category"] == d])
                                  for d in sorted({c["category"] for c in cycles})}})
    return metrics


def exclusion_reasons(profile, as_of):
    reasons = []
    if profile.get("quarantined"):
        reasons.append("quarantined")
    if profile.get("quality_errors") or profile.get("coverage") != "complete":
        reasons.append("data_quality_unknown")
    w7, w30, w90 = (profile["windows"][str(d)] for d in (7, 30, 90))
    if (w90["closed_positions"] or 0) < 50:
        reasons.append("fewer_than_50_closures")
    if (w90["distinct_events"] or 0) < 20:
        reasons.append("fewer_than_20_events")
    if w30["realized_pnl"] is None or w90["realized_pnl"] is None or w30["realized_pnl"] <= 0 or w90["realized_pnl"] <= 0:
        reasons.append("nonpositive_30_or_90_day_pnl")
    if (w7["buy_assets_ge_5"] or 0) < 10 or (w7["buy_days_ge_5"] or 0) < 3:
        reasons.append("insufficient_recent_activity")
    if w7["last_buy_at"] is None or as_of - w7["last_buy_at"] > 48 * 3600:
        reasons.append("last_buy_older_than_48h")
    return reasons


def shortlist(profiles):
    qualified = [p for p in profiles if not p["exclusion_reasons"]]
    def rank(p):
        month = p["windows"]["30"]
        pf = math.inf if month["profit_factor_unbounded"] else (month["profit_factor"] or 0)
        return (-pf, -p["windows"]["7"]["buy_assets_ge_5"],
                -p["windows"]["90"]["closed_positions"], p["address"])
    return sorted(qualified, key=rank)[:20]
