"""Criteri di promozione prospettica per COPY.

Il modulo non abilita denaro reale e non cambia automaticamente configurazione:
produce un verdetto riproducibile sul run paper indipendente.
"""
from collections import defaultdict
from datetime import datetime, timezone
import random
from typing import Dict, Iterable, List, Optional

from config import EXECUTION


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


def _event_cluster_bootstrap_lower_95(trades: List, iterations: int = 10000,
                                      seed: int = 42) -> float:
    """CI dell'EV/trade campionando eventi, non segnali correlati singoli."""
    if not trades:
        return float("-inf")
    clusters = defaultdict(list)
    for index, position in enumerate(trades):
        key = (
            getattr(position, "event_slug", "")
            or getattr(position, "condition_id", "")
            or getattr(position, "signal_id", "")
            or f"trade-{index}"
        )
        clusters[key].append(float(position.pnl))
    groups = list(clusters.values())
    rng = random.Random(seed)
    means = []
    for _ in range(iterations):
        sampled = [groups[rng.randrange(len(groups))] for _ in groups]
        count = sum(len(group) for group in sampled)
        means.append(sum(sum(group) for group in sampled) / count)
    means.sort()
    return means[max(0, int(iterations * 0.025) - 1)]


def evaluate_copy_run(closed_positions: Iterable, run_id: str,
                      intended_domains: Optional[List[str]] = None,
                      now: Optional[datetime] = None,
                      bootstrap_iterations: int = 10000,
                      max_drawdown_override: Optional[float] = None,
                      min_distinct_wallets: int = 5,
                      max_trade_share_per_wallet: float = 0.20) -> Dict:
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
    if max_drawdown_override is not None:
        max_dd = max(max_dd, max(0.0, float(max_drawdown_override)))

    positive_total = sum(pnl for pnl in pnls if pnl > 0)
    by_event = defaultdict(float)
    by_wallet = defaultdict(float)
    trades_by_wallet = defaultdict(int)
    by_domain = defaultdict(int)
    for p in trades:
        source_wallet = getattr(p, "source_wallet", "") or "unknown"
        trades_by_wallet[source_wallet] += 1
        if p.pnl > 0:
            by_event[getattr(p, "event_slug", "") or p.condition_id] += p.pnl
            by_wallet[source_wallet] += p.pnl
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
    minimum_domain_trades = int(
        EXECUTION.get("promotion_min_trades_per_domain", 30)
    )
    domain_ok = all(by_domain[d] >= minimum_domain_trades for d in domains)
    ci_lower = _event_cluster_bootstrap_lower_95(
        trades, bootstrap_iterations
    )
    max_wallet_trade_share = (
        max(trades_by_wallet.values(), default=0) / len(trades)
        if trades else 1.0
    )

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
        "distinct_source_wallets_at_least_5":
            len(trades_by_wallet) >= int(min_distinct_wallets),
        "wallet_trade_concentration_at_most_20pct":
            max_wallet_trade_share <= float(max_trade_share_per_wallet),
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
            "bootstrap_unit": "event_cluster",
            "max_drawdown": max_dd,
            "event_positive_pnl_concentration": event_concentration,
            "wallet_positive_pnl_concentration": wallet_concentration,
            "distinct_source_wallets": len(trades_by_wallet),
            "max_wallet_trade_share": max_wallet_trade_share,
            "trades_by_wallet": dict(trades_by_wallet),
            "trades_by_domain": dict(by_domain),
            "minimum_trades_per_domain": minimum_domain_trades,
        },
    }


def evaluate_shadow_run(closed_positions: Iterable, run_id: str,
                        intended_domains: Optional[List[str]] = None,
                        now: Optional[datetime] = None,
                        bootstrap_iterations: int = 10000,
                        max_drawdown_override: Optional[float] = None,
                        min_distinct_wallets: int = 5,
                        max_trade_share_per_wallet: float = 0.20) -> Dict:
    """Valuta lo shadow cohort; puo promuovere solo a un paper indipendente."""
    result = evaluate_copy_run(
        closed_positions,
        run_id,
        intended_domains=intended_domains,
        now=now,
        bootstrap_iterations=bootstrap_iterations,
        max_drawdown_override=max_drawdown_override,
        min_distinct_wallets=min_distinct_wallets,
        max_trade_share_per_wallet=max_trade_share_per_wallet,
    )
    passed = bool(result.pop("eligible_for_paper_promotion", False))
    domains_frozen = bool(intended_domains)
    result["checks"]["intended_domains_frozen"] = domains_frozen
    passed = passed and domains_frozen
    result["eligible_for_independent_paper"] = passed
    result["real_money_authorized"] = False
    result["validation_stage"] = "shadow"
    return result
