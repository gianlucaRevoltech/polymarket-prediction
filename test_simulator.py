"""Smoke manuale del ledger; privo di side effect durante unittest discovery."""
from simulator import PaperTradingSimulator


def main() -> int:
    sim = PaperTradingSimulator()
    print(f"Budget iniziale: ${sim.portfolio.initial_capital:.2f}")
    print(f"Cash disponibile: ${sim.portfolio.cash:.2f}")
    print(f"Posizioni aperte: {len(sim.portfolio.positions)}")
    print(f"Trade chiusi: {len(sim.portfolio.closed_positions)}")
    print("Stato caricato correttamente.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
