"""Current-run, read-only paper audit; only its report output is written."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from paper_accounting import economic_report, journal_rows, read_json, number
from paper_readiness import build_readiness
from run_manifest import load_run_manifest
from runtime_contract import atomic_json
from time_utils import utc_now_iso


def report(data: Path, logs: Path, root: Path):
    manifest = load_run_manifest(data)
    if not manifest:
        raise ValueError("manifest assente/non valido")
    run_id = manifest["run_id"]
    rows, errors = journal_rows(data / "candidate_journal.jsonl", run_id)
    state = read_json(data / "portfolio_state.json")
    curve = []
    try:
        curve = json.loads((data / "equity_curve.json").read_text(encoding="utf-8"))
        if isinstance(curve, dict):
            curve = curve.get("points", [])
        if not isinstance(curve, list):
            curve = []
            raise ValueError("equity curve non-lista")
    except (OSError, ValueError):
        errors.append("curva equity assente/non valida")
    peak = float(state.get("initial_capital", 300))
    drawdown = float(state.get("max_drawdown", 0) or 0)
    for point in curve:
        if not isinstance(point, dict) or point.get("run_id", run_id) != run_id:
            continue
        try:
            value = number(point.get("equity"), "curve equity")
        except ValueError as exc:
            errors.append(str(exc))
            continue
        peak = max(peak, value)
        drawdown = max(drawdown, (peak - value) / peak if peak else 0)
    economic = economic_report(state, rows, manifest, max_drawdown=drawdown)
    economic["quality_errors"].extend(errors)
    if errors:
        economic.pop("promotion", None)
    return {"report_version": 1, "run_id": run_id, "generated_at": utc_now_iso(),
            "execution_mode": manifest["execution_mode"], "economic_status": economic,
            "readiness": build_readiness(data, logs, root),
            "feed_health": read_json(data / "runtime_status.json").get("feed_health", {}),
            "real_money_authorized": False}


def main():
    data = ROOT / "data"
    result = report(data, ROOT / "logs", ROOT)
    atomic_json(data / "paper_report.json", result)
    economic = result["economic_status"]
    print(f"Run: {result['run_id']} | {result['execution_mode']} | SOLO SIMULAZIONE")
    for key in ("cash", "equity", "realized_pnl", "unrealized_pnl", "net_pnl", "fees_usdc", "closed_trades", "close_reasons"):
        print(f"{key}: {economic.get(key)}")
    print("Contabilita:", "OK" if economic["reconciled"] else "ERRORE")
    print("Qualita:", economic["quality_errors"] + economic["fee_quality_errors"])
    print("JSON: data/paper_report.json | edge non dimostrato; nessun denaro reale")
    return 0 if economic["reconciled"] and not economic["quality_errors"] and not economic["fee_quality_errors"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
