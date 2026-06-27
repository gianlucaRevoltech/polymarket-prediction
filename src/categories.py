"""
Categorizzazione mercati Polymarket e modello fee per categoria.

Usato da scanner (selezione specialisti per categoria), simulator (fee in
ingresso) e backtester (fee nel calcolo realistico).

CATEGORIE: sport, crypto, politics, weather, other.

FEE: i mercati sport su Polymarket usano lo schema `sports_fees_v2`
(taker-only). Dal feeSchedule osservato su Gamma: {exponent:1, rate:0.03,
takerOnly:true, rebateRate:0.25}. La fee effettiva NON e' un 3% piatto: e'
proporzionale all'incertezza del prezzo (massima a 0.5, ~0 agli estremi).
Modelliamo quindi la fee come rate * min(p, 1-p), un'approssimazione realistica
e limitata. Le altre categorie non hanno trading fee sul CLOB (0%).
"""
from typing import List, Tuple

SPORTS_FEE_RATE = 0.03  # da Gamma feeSchedule (sports_fees_v2, taker-only)

# Parole chiave per categoria (ordine = priorita di match dopo lo sport)
_KEYWORDS = {
    "crypto": [
        "bitcoin", "btc", "ethereum", " eth ", "solana", " sol ", "crypto",
        "dogecoin", " xrp", " bnb", "cardano", "binance", "stablecoin",
        "altcoin", "memecoin", "satoshi", "blockchain",
    ],
    "politics": [
        "election", "president", "nominee", "democratic", "republican",
        "senate", "congress", "governor", "prime minister", "parliament",
        "vote", "poll ", "candidate", "primary", "referendum", "cabinet",
        "minister", "chancellor", "mayor", "impeach", "nomination",
    ],
    "weather": [
        "temperature", "weather", "rain", "hurricane", "snow", "climate",
        "degrees", "noaa", "celsius", "fahrenheit", "storm", "tornado",
        "heatwave", "wildfire",
    ],
    "sport": [
        "world cup", "fifa", " nba", " nfl", " nhl", "soccer", "football",
        "premier league", "la liga", "serie a", "bundesliga", "champions league",
        "goalscorer", "halftime", "vs.", " vs ", "win on 2026", "win on 2025",
        "both teams to score", "spread:", "o/u", "over/under", "match",
        "tournament", "playoff", "super bowl", "tennis", "ufc", "boxing",
        "cricket", "olympic", "grand prix", "formula 1", " f1 ",
        "wimbledon", "ballon d'or", "golden ball", "masters tournament",
        "drivers' championship", "constructors' championship", "top goalscorer",
        "nba finals", "exact score", "reach the 2026 fifa", "world cup",
    ],
}

CATEGORIES = ["sport", "crypto", "politics", "weather", "other"]


def categorize_market(question: str = "", event_ticker: str = "",
                      event_slug: str = "", fee_type: str = "") -> str:
    """
    Determina la categoria di un mercato da testo e metadati.

    Args:
        question: testo della domanda / titolo
        event_ticker: ticker dell'evento (gamma events[].ticker)
        event_slug: slug dell'evento
        fee_type: feeType del mercato (gamma); "sports_fees_v2" => sport
    """
    if fee_type and "sport" in fee_type.lower():
        return "sport"

    text = f" {question} {event_ticker} {event_slug} ".lower()

    # Crypto / politics / weather hanno keyword piu specifiche: controllale prima
    for cat in ("crypto", "politics", "weather"):
        if any(kw in text for kw in _KEYWORDS[cat]):
            return cat

    if any(kw in text for kw in _KEYWORDS["sport"]):
        return "sport"

    return "other"


def taker_fee_fraction(category: str, price: float) -> float:
    """
    Fee taker come frazione del valore scambiato, dipendente da categoria e prezzo.

    Returns:
        frazione (es. 0.012 = 1.2%) da applicare al notional in ingresso.
    """
    if category == "sport":
        p = max(0.0, min(1.0, price))
        return SPORTS_FEE_RATE * min(p, 1.0 - p)
    return 0.0
