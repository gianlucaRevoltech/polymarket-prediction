"""Archiviazione e reset esplicito, cross-platform, dello stato paper."""
import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LOGS = ROOT / "logs"

LEDGER_FILES = (
    "portfolio_state.json",
    "portfolio_state.json.bak",
    "trades_log.json",
    "equity_curve.json",
    "peak_equity.json",
    "recent_opens.json",
    "daily_halt.json",
    "safety_state.json",
    "candidate_journal.jsonl",
    "monitored_wallets.json",
)

CLEAR_FILES = LEDGER_FILES + (
    "price_history.json",
    "whale_wallets.json",
    "latency_arb_signals.jsonl",
    "latency_arb_stats.json",
)


def _safe_run_id(raw: str) -> str:
    cleaned = "".join(
        ch for ch in raw if ch.isalnum() or ch in "._-"
    ).strip("._-")
    return cleaned or f"legacy-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}"


def current_run_id() -> str:
    state = DATA / "portfolio_state.json"
    if state.exists():
        try:
            value = json.loads(state.read_text(encoding="utf-8")).get("run_id")
            if value:
                return _safe_run_id(str(value))
        except Exception:
            pass
    return _safe_run_id("")


def archive() -> Path:
    run_id = current_run_id()
    target = DATA / "runs" / run_id
    if target.exists():
        target = DATA / "runs" / (
            f"{run_id}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}"
        )
    target.mkdir(parents=True, exist_ok=False)
    for name in LEDGER_FILES:
        source = DATA / name
        if source.is_file():
            shutil.copy2(source, target / name)
    shutil.copy2(ROOT / "src" / "config.py", target / "config.py")
    marker = {
        "run_id": run_id,
        "archived_at": datetime.now(timezone.utc).isoformat(),
    }
    (target / "archive_manifest.json").write_text(
        json.dumps(marker, indent=2), encoding="utf-8"
    )
    print(f"[ARCHIVE] Run preservato in {target}")
    return target


def clear(force: bool) -> None:
    if not force:
        raise SystemExit("clear richiede --force")
    for name in CLEAR_FILES:
        path = DATA / name
        if path.is_file():
            path.unlink()
    alerts = LOGS / "alerts.log"
    if alerts.is_file():
        alerts.unlink()
    print("[RESET] Ledger corrente azzerato; archivi e scan_results preservati.")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("archive")
    clear_parser = sub.add_parser("clear")
    clear_parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.command == "archive":
        archive()
    else:
        clear(args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
