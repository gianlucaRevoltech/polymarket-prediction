"""CLI preflight: shared checks plus an isolated synthetic lifecycle."""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA, LOGS = ROOT / "data", ROOT / "logs"
sys.path.insert(0, str(ROOT / "src"))
from paper_readiness import build_readiness, pid_alive as _pid_alive
from runtime_contract import atomic_json, git_commit


def _git_commit():
    return git_commit(ROOT)


def _run_synthetic() -> dict:
    try:
        result = subprocess.run([sys.executable, str(ROOT / "tools" / "paper_lifecycle_smoke.py")],
                                cwd=ROOT, capture_output=True, text=True, timeout=30, check=False)
        for line in reversed(result.stdout.splitlines()):
            try:
                payload = json.loads(line)
                if isinstance(payload, dict):
                    payload["passed"] = bool(payload.get("passed")) and result.returncode == 0
                    return payload
            except ValueError:
                continue
        return {"passed": False, "error": result.stderr[-1000:] or "smoke senza risultato"}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"passed": False, "error": str(exc)}


def build_report(*, post_start=False, run_synthetic=True, synthetic=None, stopped=False):
    if run_synthetic:
        synthetic = _run_synthetic()
    return build_readiness(DATA, LOGS, ROOT, expected_mode="paper_validation" if post_start else "observe",
                           synthetic=synthetic, stopped=stopped,
                           process_check=_pid_alive, current_commit=_git_commit())


def wait_report(*, wait_seconds=0, post_start=False, synthetic=None,
                process_instance_id=None, stopped=False):
    synthetic = _run_synthetic() if synthetic is None else synthetic
    deadline = time.monotonic() + wait_seconds
    recoverable = {"feed_health", "feed_coverage", "healthy_cycles", "two_cycles",
                   "ledger_fresh", "heartbeat_fresh", "cycle_fresh", "process_identity",
                   "bot_process", "dashboard_process", "new_process"}
    if post_start:
        # Fresh run has no ledger until the first complete snapshot is saved.
        recoverable.update({"ledger_schema", "accounting", "runtime_schema",
                            "runtime_mode", "deployed_commit", "cohort_identity", "initial_paper_sample"})
    while True:
        report = build_report(post_start=post_start, run_synthetic=False,
                              synthetic=synthetic, stopped=stopped)
        if process_instance_id and report.get("process_instance_id") == process_instance_id:
            failure = dict(key="new_process", severity="red", passed=False,
                           message="startup non ha creato un nuovo processo")
            report["blockers"].append(failure)
            report["checks"].append(failure)
            report["ready"] = False
        if report["ready"] or time.monotonic() >= deadline:
            return report
        if any(c["key"] not in recoverable for c in report["blockers"]):
            return report
        print("[WAIT] " + "; ".join(c["message"] for c in report["blockers"]), flush=True)
        time.sleep(min(5, max(0, deadline - time.monotonic())))


def _atomic_write_report(report):
    atomic_json(DATA / "preflight_report.json", report)


def print_report(report):
    print("\n=== PREFLIGHT PAPER ===")
    for item in report["checks"]:
        symbol = "OK" if item["passed"] else "WARN" if item["severity"] == "yellow" else "BLOCK"
        print(f"[{symbol}] {item['key']}: {item['message']}")
    print("Risultato: " + ("READY" if report["ready"] else "BLOCKED"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--post-start", action="store_true")
    parser.add_argument("--skip-synthetic", action="store_true", help="reuse only a matching saved smoke")
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--wait", type=int, default=0, choices=range(0, 121), metavar="0..120")
    args = parser.parse_args()
    if args.skip_synthetic:
        from paper_accounting import read_json
        from run_manifest import load_run_manifest
        old = read_json(DATA / "preflight_report.json")
        synthetic = old.get("synthetic_lifecycle", {}) if old.get("checked_commit") == _git_commit() and old.get("run_id") == load_run_manifest(DATA).get("run_id") else {}
    else:
        synthetic = _run_synthetic()
    from run_manifest import load_run_manifest
    paper_mode = load_run_manifest(DATA).get("execution_mode") == "paper_validation"
    report = wait_report(wait_seconds=args.wait, post_start=args.post_start or paper_mode, synthetic=synthetic)
    if not args.no_write:
        _atomic_write_report(report)
    print_report(report)
    return 0 if report["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
