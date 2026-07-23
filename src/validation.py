"""Criteri di promozione prospettica per COPY.

Il modulo non abilita denaro reale e non cambia automaticamente configurazione:
produce un verdetto riproducibile sul run paper indipendente.
"""
from collections import defaultdict
from datetime import datetime, timezone
import random
from typing import Dict, Iterable, List, Optional


def _bootstrap_lower_95(values: List[float], iterations: int = 10000,
                        seed: int = 42) -> float:
    if not values:
        return float("-inf")
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(iterations):
        means.append(sum(values[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    return means[max(0, int(iterations * 0.025) - 1)]


def evaluate_copy_run(closed_positions: Iterable, run_id: str,
                      intended_domains: Optional[List[str]] = None,
                      now: Optional[datetime] = None,
                      bootstrap_iterations: int = 10000) -> Dict:
    """Valuta esclusivamente trade COPY chiusi e appartenenti al run indicato."""
    trades = [
        p for p in closed_positions
        if (getattr(p, "strategy", "copy") or "copy") == "copy"
        and getattr(p, "run_id", "") == run_id
        and getattr(p, "is_closed", True)
    ]
    trades.sort(key=lambda p: getattr(p, "exit_time", None) or datetime.min)
    pnls = [float(p.pnl) for p in trades]
    events = {
        getattr(p, "event_slug", "") or getattr(p, "condition_id", "")
        for p in trades
    }
    events.discard("")

    now = now or datetime.now()
    first = min((p.entry_time for p in trades if getattr(p, "entry_time", None)),
                default=now)
    elapsed_days = max(0.0, (now - first).total_seconds() / 86400.0)

    equity = 300.0
    peak = equity
    max_dd = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)

    positive_total = sum(pnl for pnl in pnls if pnl > 0)
    by_event = defaultdict(float)
    by_wallet = defaultdict(float)
    by_domain = defaultdict(int)
    for p in trades:
        if p.pnl > 0:
            by_event[getattr(p, "event_slug", "") or p.condition_id] += p.pnl
            by_wallet[getattr(p, "source_wallet", "") or "unknown"] += p.pnl
        by_domain[getattr(p, "category", "") or "other"] += 1

    event_concentration = (
        max(by_event.values(), default=0.0) / positive_total
        if positive_total > 0 else 1.0
    )
    wallet_concentration = (
        max(by_wallet.values(), default=0.0) / positive_total
        if positive_total > 0 else 1.0
    )
    domains = intended_domains or []
    domain_ok = all(by_domain[d] >= 30 for d in domains)
    ci_lower = _bootstrap_lower_95(pnls, bootstrap_iterations)

    checks = {
        "closed_trades_at_least_100": len(trades) >= 100,
        "distinct_events_at_least_30": len(events) >= 30,
        "elapsed_days_at_least_14": elapsed_days >= 14,
        "net_pnl_positive": sum(pnls) > 0,
        "bootstrap_ci95_lower_ev_positive": ci_lower > 0,
        "max_drawdown_at_most_3pct": max_dd <= 0.03,
        "event_positive_pnl_concentration_at_most_20pct":
            event_concentration <= 0.20,
        "wallet_positive_pnl_concentration_at_most_20pct":
            wallet_concentration <= 0.20,
        "intended_domains_at_least_30_trades": domain_ok,
    }
    return {
        "run_id": run_id,
        "eligible_for_paper_promotion": all(checks.values()),
        "real_money_authorized": False,
        "checks": checks,
        "metrics": {
            "closed_trades": len(trades),
            "distinct_events": len(events),
            "elapsed_days": elapsed_days,
            "net_pnl": sum(pnls),
            "ev_per_trade": (sum(pnls) / len(pnls)) if pnls else 0.0,
            "bootstrap_ci95_lower_ev": ci_lower,
            "max_drawdown": max_dd,
            "event_positive_pnl_concentration": event_concentration,
            "wallet_positive_pnl_concentration": wallet_concentration,
            "trades_by_domain": dict(by_domain),
        },
    }
