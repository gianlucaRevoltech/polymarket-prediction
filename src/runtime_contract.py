"""Process identity, live feed coverage and activation barrier."""
import os
import subprocess
from pathlib import Path

from paper_accounting import read_json, number
from time_utils import age_seconds, utc_now_iso

RUNTIME_VERSION = 2


def git_commit(root: Path) -> str:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                                capture_output=True, text=True, timeout=3, check=False)
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def atomic_json(path: Path, value: dict) -> None:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def feed_readiness(feed: dict) -> tuple[str, str]:
    required = ("last_snapshot_at", "last_snapshot_status", "last_snapshot_wallets_ok",
                "last_snapshot_wallets_failed", "consecutive_complete_snapshots",
                "consecutive_incomplete_snapshots", "consecutive_failed_snapshots",
                "backoff_remaining_seconds", "consecutive_transient_errors")
    if not isinstance(feed, dict) or any(feed.get(key) is None for key in required):
        return "unknown", "copertura feed assente/incompatibile"
    try:
        for key in ("consecutive_complete_snapshots", "consecutive_incomplete_snapshots",
                    "consecutive_failed_snapshots", "backoff_remaining_seconds", "consecutive_transient_errors"):
            if number(feed[key], key) < 0:
                return "unknown", "contatore feed negativo"
        if int(feed["consecutive_failed_snapshots"]) >= 2 or int(feed["consecutive_incomplete_snapshots"]) >= 3:
            return "outage", "outage feed consecutivo persistente"
        age = age_seconds(feed["last_snapshot_at"])
        if age is None or age > 60:
            return "stale", "ultimo snapshot feed oltre 60 secondi"
        if (feed["last_snapshot_status"] != "complete" or not feed["last_snapshot_wallets_ok"]
                or feed["last_snapshot_wallets_failed"] or int(feed["consecutive_complete_snapshots"]) < 2
                or float(feed["backoff_remaining_seconds"]) > 0
                or int(feed["consecutive_transient_errors"]) >= 3):
            return "recovering", "recupero feed: richiesti due snapshot completi consecutivi senza backoff"
        return "healthy", "due snapshot completi consecutivi; feed recuperato"
    except (ValueError, TypeError):
        return "unknown", "telemetria feed non valida"


def activation(data: Path, run_id: str) -> dict:
    value = read_json(data / "paper_activation.json")
    if value.get("run_id") != run_id:
        return {"run_id": run_id, "status": "pending", "reason": "attivazione paper non verificata"}
    return value


def record_deployment(data: Path, identity: dict) -> None:
    """Append-only provenance; the immutable origin manifest is never rewritten."""
    import json
    data.mkdir(parents=True, exist_ok=True)
    with (data / "deployment_history.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(identity, recorded_at=utc_now_iso())) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
