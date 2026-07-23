"""
Categorizzazione mercati Polymarket e modello fee per categoria.

Usato da scanner (selezione specialisti per categoria), simulator (fee in
ingresso) e backtester (fee nel calcolo realistico).

CATEGORIE: sport, crypto, politics, weather, macro, geopolitics, other.

FEE: i mercati sport su Polymarket usano lo schema `sports_fees_v2`
(taker-only). Dal feeSchedule osservato su Gamma: {exponent:1, rate:0.03,
takerOnly:true, rebateRate:0.25}. La fee effettiva NON e' un 3% piatto: e'
proporzionale all'incertezza del prezzo (massima a 0.5, ~0 agli estremi).
Modelliamo quindi la fee come rate * min(p, 1-p), un'approssimazione realistica
e limitata. Le altre categorie non hanno trading fee sul CLOB (0%).
"""
from typing import List, Tuple, Union

SPORTS_FEE_RATE = 0.03  # da Gamma feeSchedule (sports_fees_v2, taker-only)

# Parole chiave per categoria (ordine = priorita di match dopo lo sport)
# Phase CJ: keyword ampliate — prima molte finivano in "other" per match mancato.
# ATTENZIONE: keyword corte senza spazi generano false positive ("eth" = Ethiopia).
# Teniamo solo token univoci o con delimitatori ($eth, eth/, eth-).
_KEYWORDS = {
    "macro": [
        "federal reserve", "fed ", "fed-", "interest rate", "interest rates",
        "rate cut", "rate hike", "basis points", " bps", "fomc", "central bank",
        "inflation", "cpi", "gdp", "unemployment", "nonfarm payroll",
        "treasury yield", "ecb", "bank of england", "monetary policy",
    ],
    "geopolitics": [
        "ceasefire", "war ", "invasion", "military strike", "airstrike",
        "missile", "troops", "peace deal", "hostage", "sanctions",
        "israel", "iran", "gaza", "ukraine", "russia", "nato",
        "taiwan", "china invade", "strait of hormuz",
    ],
    "crypto": [
        # Token names (univoci o con delimiter per evitare false positive)
        "bitcoin", "btc", "ethereum", "solana", "crypto", "dogecoin",
        "doge", "xrp", "bnb", "cardano", "binance", "stablecoin",
        "altcoin", "memecoin", "satoshi", "blockchain",
        "pepe", "shiba", "litecoin", "ltc", "tron", "avalanche",
        "avax", "polygon", "matic", "chainlink", "polkadot",
        "uniswap", "aptos", "coinbase", "microstrategy", "saylor",
        # Pattern con simbolo/ticker per ETH/SOL/LINK/DOT (corti, evitano false positive)
        "$eth", "$sol", "$link", "$dot", "$uni", "$near", "$apt", "$sui",
        "eth/", "sol/", "link/", "dot/", "uni/", "near/",
        "eth-", "sol-", "link-", "dot-", "uni-", "near-",
        # Concept keywords
        "halving", "etf", "spot bitcoin", "price target", "close above",
        "close below", "dip to", "reach $", "hit $", "mining", "hashrate",
        "deFi", "nft", "token", "coin", "airdrop", "staking", "liquidity pool",
    ],
    "politics": [
        "election", "president", "nominee", "democratic", "republican",
        "senate", "congress", "governor", "prime minister", "parliament",
        "vote", "poll ", "candidate", "primary", "referendum", "cabinet",
        "minister", "chancellor", "mayor", "impeach", "nomination",
        "trump", "biden", "harris", "desantis", "newsom", "whitmer",
        "gop", "inauguration", "caucus", "midterm",
        "legislature", "congressman", "senator",
    ],
    "weather": [
        "temperature", "weather", "rain", "hurricane", "snow", "climate",
        "degrees", "noaa", "celsius", "fahrenheit", "storm", "tornado",
        "heatwave", "wildfire", "heat", "cold", "freeze", "frost",
        "drought", "flood", "wind", "forecast", "°f", "°c",
        "high of", "low of", "precipitation", "humidity", "barometric",
        "snowfall", "rainfall", "wind speed", "mph", "heat index",
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

CATEGORIES = ["sport", "crypto", "politics", "weather", "macro", "geopolitics", "other"]


def categorize_market(question: str = "", event_ticker: str = "",
                      event_slug: str = "", fee_type: str = "",
                      tags: Union[List, str, None] = None) -> str:
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

    if isinstance(tags, list):
        tag_text = " ".join(
            str(t.get("slug") or t.get("label") or t.get("name") or "")
            if isinstance(t, dict) else str(t)
            for t in tags
        )
    else:
        tag_text = str(tags or "")
    metadata = f" {event_ticker} {event_slug} {tag_text} ".lower()

    # I tag/slug Gamma sono più affidabili del titolo: hanno priorità.
    for cat in ("macro", "geopolitics", "crypto", "politics", "weather", "sport"):
        if any(kw in metadata for kw in _KEYWORDS[cat]):
            return cat

    text = f" {question} {metadata} ".lower()

    for cat in ("macro", "geopolitics", "crypto", "politics", "weather"):
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
