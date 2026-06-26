"""
Sync delle posizioni reali dei wallet target via data-api Polymarket.

A differenza del feed `activity` (eventi singoli, in ritardo, con mapping YES/NO
fragile), qui leggiamo lo SNAPSHOT del portafoglio di ogni wallet:
  GET https://data-api.polymarket.com/positions?user=<addr>

L'endpoint restituisce, per ogni posizione aperta, prezzo corrente, PnL e stato
di risoluzione: questo ci permette di valorizzare, chiudere e realizzare le
posizioni simulate in modo fedele.
"""
import requests
from typing import Dict, List, Optional
from config import POLYMARKET_API


class PolymarketPositionFetcher:
    """Recupera lo snapshot delle posizioni dei wallet e i prezzi correnti."""

    def __init__(self):
        self.data_api = POLYMARKET_API["data"]
        self.clob = POLYMARKET_API["clob"]
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })

    def get_positions(self, wallet_address: str, limit: int = 200) -> List[Dict]:
        """
        Ritorna le posizioni aperte di un wallet, normalizzate.

        Returns:
            Lista di dict normalizzati (vedi `_normalize`).
        """
        try:
            url = f"{self.data_api}/positions"
            params = {"user": wallet_address, "limit": limit}
            response = self.session.get(url, params=params, timeout=15)
            response.raise_for_status()
            raw = response.json()
        except Exception as e:
            print(f"[SYNC] Errore positions {wallet_address[:10]}...: {e}")
            return []

        positions = []
        for p in raw:
            norm = self._normalize(p)
            # Scarta dust / posizioni vuote
            if norm["asset"] and norm["size"] > 0:
                positions.append(norm)
        return positions

    @staticmethod
    def _normalize(p: Dict) -> Dict:
        """Normalizza la posizione raw del data-api in un formato stabile."""
        size = float(p.get("size", 0.0) or 0.0)
        avg_price = float(p.get("avgPrice", 0.0) or 0.0)
        return {
            "asset": str(p.get("asset", "")),
            "condition_id": p.get("conditionId", ""),
            "title": p.get("title", "Unknown Market"),
            "slug": p.get("slug", ""),
            "outcome": p.get("outcome", ""),
            "outcome_index": p.get("outcomeIndex", 0),
            "size": size,
            "avg_price": avg_price,
            "cur_price": float(p.get("curPrice", 0.0) or 0.0),
            "current_value": float(p.get("currentValue", 0.0) or 0.0),
            "cash_pnl": float(p.get("cashPnl", 0.0) or 0.0),
            "realized_pnl": float(p.get("realizedPnl", 0.0) or 0.0),
            "redeemable": bool(p.get("redeemable", False)),
            "end_date": p.get("endDate", ""),
            # Notional in USDC che il wallet ha investito nella posizione
            "notional_usdc": size * avg_price,
        }

    def get_price(self, token_id: str) -> Optional[float]:
        """
        Prezzo corrente di un token via CLOB midpoint.
        Usato come fallback per valorizzare posizioni che nessun wallet
        monitorato detiene piu (es. quando il wallet sorgente e' uscito).

        Returns:
            float in [0,1] oppure None se non disponibile (es. mercato risolto
            senza orderbook).
        """
        try:
            url = f"{self.clob}/midpoint"
            response = self.session.get(url, params={"token_id": token_id}, timeout=10)
            if response.ok:
                data = response.json()
                mid = data.get("mid")
                if mid is not None:
                    return float(mid)
        except Exception:
            pass
        return None

    def snapshot_wallets(self, wallet_addresses: List[str]) -> Dict[str, Dict]:
        """
        Aggrega le posizioni di piu wallet per asset.

        Returns:
            Dict asset -> {
                "info": <ultima posizione normalizzata per quell'asset>,
                "holders": set(wallet_address),
                "max_notional": float
            }
        """
        aggregate: Dict[str, Dict] = {}
        for addr in wallet_addresses:
            for pos in self.get_positions(addr):
                asset = pos["asset"]
                entry = aggregate.get(asset)
                if entry is None:
                    aggregate[asset] = {
                        "info": pos,
                        "holders": {addr},
                        "max_notional": pos["notional_usdc"],
                    }
                else:
                    entry["holders"].add(addr)
                    entry["max_notional"] = max(entry["max_notional"], pos["notional_usdc"])
                    # Tieni l'info con prezzo corrente piu aggiornato (sono uguali, ma
                    # preferiamo quella con redeemable=True se presente)
                    if pos["redeemable"]:
                        entry["info"] = pos
        return aggregate


if __name__ == "__main__":
    fetcher = PolymarketPositionFetcher()
    test = "0x664ce9fb97ae1bbd538d7381b2f4e92dab16f49c"
    for pos in fetcher.get_positions(test)[:5]:
        print(f"{pos['title'][:40]:40} | {pos['outcome']:6} | "
              f"cur={pos['cur_price']:.3f} | redeemable={pos['redeemable']}")
