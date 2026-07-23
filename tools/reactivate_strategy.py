"""Riattivazione manuale di una strategia quarantinata nel run corrente."""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from config import BUDGET  # noqa: E402
from simulator import PaperTradingSimulator  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("strategy", help="es. copy")
    parser.add_argument(
        "--confirm", action="store_true",
        help="richiesto: conferma la riattivazione manuale",
    )
    args = parser.parse_args()
    if not args.confirm:
        parser.error("aggiungere --confirm per modificare safety_state.json")
    sim = PaperTradingSimulator(BUDGET["initial_capital"])
    sim.reactivate_strategy(args.strategy)
    print(f"[OK] {args.strategy} riattivata manualmente nel run {sim.run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
