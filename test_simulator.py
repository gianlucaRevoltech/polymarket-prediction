from simulator import PaperTradingSimulator

sim = PaperTradingSimulator()
print(f"Budget iniziale: ${sim.portfolio.initial_capital:.2f}")
print(f"Cash disponibile: ${sim.portfolio.cash:.2f}")
print(f"Posizioni aperte: {len(sim.portfolio.positions)}")
print(f"Trade chiusi: {len(sim.portfolio.closed_positions)}")

if len(sim.portfolio.positions) > 0:
    print("\nDettagli posizioni:")
    for pid, pos in sim.portfolio.positions.items():
        print(f"  - {pos.market_title[:50]}")
        print(f"    Esito: {pos.outcome} @ ${pos.entry_price:.3f}")
        print(f"    Size: ${pos.size_usdc:.2f}")

print("\n✅ Stato caricato correttamente!")
