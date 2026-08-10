"""Registro prospettico cross-run dei wallet non promuovibili."""
import json
import os
import tempfile
from pathlib import Path
from typing import Dict, Set

from time_utils import utc_now_iso


REGISTRY_FILENAME = "wallet_validation_registry.json"


def registry_path(data_dir) -> Path:
    return Path(data_dir) / REGISTRY_FILENAME


def load_registry(data_dir) -> Dict:
    path = registry_path(data_dir)
    if not path.exists():
        return {"registry_version": 1, "wallets": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("registry root non object")
        wallets = data.get("wallets", {})
        if not isinstance(wallets, dict):
            raise ValueError("registry wallets non object")
        data["registry_version"] = int(data.get("registry_version", 1) or 1)
        data["wallets"] = wallets
        return data
    except (OSError, ValueError, json.JSONDecodeError):
        # Fail closed: un registro corrotto non deve essere sovrascritto in modo
        # silenzioso ne usato per inventare nuovi wallet qualificati.
        return {
            "registry_version": 1,
            "wallets": {},
            "load_error": "wallet_validation_registry_unreadable",
        }


def quarantined_wallets(data_dir) -> Set[str]:
    registry = load_registry(data_dir)
    if registry.get("load_error"):
        raise ValueError(registry["load_error"])
    return {
        str(address).lower()
        for address, record in registry.get("wallets", {}).items()
        if isinstance(record, dict) and record.get("status") == "quarantined"
    }


def quarantine_wallet(data_dir, address: str, *, run_id: str, reason: str,
                      loss_streak: int) -> bool:
    address = str(address or "").lower().strip()
    if not address:
        return False
    path = registry_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    registry = load_registry(data_dir)
    if registry.get("load_error"):
        return False
    wallets = registry.setdefault("wallets", {})
    current = wallets.get(address, {}) if isinstance(wallets.get(address), dict) else {}
    evidence = list(current.get("evidence", []))
    event = {
        "run_id": str(run_id),
        "reason": str(reason),
        "loss_streak": int(loss_streak),
        "recorded_at": utc_now_iso(),
    }
    if not any(
        row.get("run_id") == event["run_id"] and row.get("reason") == event["reason"]
        for row in evidence if isinstance(row, dict)
    ):
        evidence.append(event)
    wallets[address] = {
        "status": "quarantined",
        "reason": str(reason),
        "trigger_run_id": str(run_id),
        "loss_streak": int(loss_streak),
        "quarantined_at": current.get("quarantined_at") or event["recorded_at"],
        "evidence": evidence,
    }
    registry["updated_at"] = utc_now_iso()

    fd, temp_name = tempfile.mkstemp(suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(registry, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp_name, path)
    except Exception:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
        raise
    return True
