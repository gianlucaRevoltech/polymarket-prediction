"""
Data Models per Polymarket Paper Trading Bot
"""
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from datetime import datetime
from enum import Enum


class TradeSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class Outcome(Enum):
    YES = "Yes"
    NO = "No"


@dataclass
class Wallet:
    """Wallet Polymarket con metriche base"""
    address: str
    name: str
    profit: float
    volume: float
    rank: int
    pseudonym: str = ""
    profile_image: str = ""
    
    # Calcolato dopo analisi
    roi: float = 0.0
    num_trades: int = 0
    win_rate: float = 0.0
    
    def __post_init__(self):
        if self.volume > 0:
            self.roi = (self.profit / self.volume) * 100


@dataclass
class Trade:
    """Singolo trade eseguito da un wallet"""
    condition_id: str
    market_title: str
    market_slug: str
    side: TradeSide
    outcome: Outcome
    size_usdc: float
    price: float
    timestamp: int
    tx_hash: str
    
    # Metadata
    event_slug: str = ""
    event_id: str = ""
    run_id: str = ""
    signal_id: str = ""
    icon: str = ""
    
    @property
    def timestamp_dt(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp)
    
    @property
    def cost(self) -> float:
        """Costo totale del trade"""
        return self.size_usdc


@dataclass
class Position:
    """Posizione aperta nel portfolio simulato"""
    position_id: str
    market_title: str
    market_slug: str
    condition_id: str
    outcome: str
    entry_price: float
    size_usdc: float
    shares: float
    entry_time: datetime
    source_wallet: str
    
    # Identificativo univoco del token (asset / ERC1155 token id Polymarket)
    asset: str = ""

    # Identità prospettica e correlazione. I default mantengono compatibilità con
    # ledger creati prima della Phase CK.
    run_id: str = ""
    signal_id: str = ""
    event_id: str = ""
    event_slug: str = ""
    event_title: str = ""
    
    # Categoria di mercato (sport/crypto/politics/weather/other) per fee e analisi
    category: str = ""

    # Strategia di origine (Phase M multi-strategy): copy | arb_binary | harvest | arb_cross
    strategy: str = "copy"
    # Per arbitraggio binario: id del "bundle" (es. condition_id) che aggancia le
    # due gambe YES+NO; nei trade normali e' vuoto.
    pair_id: str = ""

    # Stato
    current_price: float = 0.0
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    is_closed: bool = False
    # Motivo della chiusura: "exit" (wallet uscito) o "resolved" (mercato risolto)
    close_reason: str = ""
    
    @property
    def current_value(self) -> float:
        """Valore attuale della posizione"""
        return self.shares * self.current_price
    
    @property
    def pnl(self) -> float:
        """Profit/Loss non realizzato"""
        if self.is_closed and self.exit_price is not None:
            return (self.exit_price - self.entry_price) * self.shares
        return (self.current_price - self.entry_price) * self.shares
    
    @property
    def pnl_pct(self) -> float:
        """P&L percentuale"""
        if self.size_usdc == 0:
            return 0.0
        return (self.pnl / self.size_usdc) * 100
    
    def close(self, exit_price: float, exit_time: datetime):
        """Chiudi la posizione"""
        self.exit_price = exit_price
        self.exit_time = exit_time
        self.is_closed = True


@dataclass
class WalletAnalysis:
    """Analisi completa di un wallet"""
    wallet_address: str
    wallet_name: str
    
    # Metriche performance
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    
    # P&L
    total_profit: float
    total_volume: float
    roi: float
    
    # Risk metrics
    max_drawdown: float
    sharpe_ratio: float
    
    # Consistency
    profitable_days: int
    total_days: int
    consistency: float
    
    # Trading style
    avg_trade_size: float
    avg_holding_time_hours: float
    markets_traded: int
    
    # Qualification
    is_qualified: bool = False
    qualification_score: float = 0.0
    disqualified_reasons: List[str] = field(default_factory=list)
    
    def calculate_score(self) -> float:
        """Calcola score di qualità (0-100)"""
        score = 0.0
        
        # ROI (30 punti)
        if self.roi > 0.50:
            score += 30
        elif self.roi > 0.30:
            score += 20
        elif self.roi > 0.20:
            score += 10
        
        # Win rate (25 punti)
        score += self.win_rate * 25
        
        # Consistency (20 punti)
        score += self.consistency * 20
        
        # Sharpe ratio (15 punti)
        if self.sharpe_ratio > 2.0:
            score += 15
        elif self.sharpe_ratio > 1.5:
            score += 10
        elif self.sharpe_ratio > 1.0:
            score += 5
        
        # Trade volume (10 punti)
        if self.total_trades > 100:
            score += 10
        elif self.total_trades > 50:
            score += 7
        elif self.total_trades > 20:
            score += 4
        
        self.qualification_score = score
        return score


@dataclass
class Portfolio:
    """Portfolio simulato"""
    initial_capital: float
    cash: float
    positions: Dict[str, Position] = field(default_factory=dict)
    closed_positions: List[Position] = field(default_factory=list)
    trades: List[Trade] = field(default_factory=list)
    
    @property
    def total_value(self) -> float:
        """Valore totale del portfolio"""
        positions_value = sum(pos.current_value for pos in self.positions.values())
        return self.cash + positions_value
    
    @property
    def total_pnl(self) -> float:
        """P&L totale"""
        return self.total_value - self.initial_capital
    
    @property
    def total_pnl_pct(self) -> float:
        """P&L totale percentuale"""
        return (self.total_pnl / self.initial_capital) * 100
    
    @property
    def open_positions_count(self) -> int:
        return len(self.positions)
    
    def add_position(self, position: Position):
        """Aggiungi posizione aperta"""
        self.positions[position.position_id] = position
        self.cash -= position.size_usdc
    
    def close_position(self, position_id: str, exit_price: float, exit_time: datetime):
        """Chiudi posizione"""
        if position_id in self.positions:
            pos = self.positions[position_id]
            pos.close(exit_price, exit_time)
            self.cash += pos.current_value
            self.closed_positions.append(pos)
            del self.positions[position_id]
    
    def update_position_price(self, position_id: str, new_price: float):
        """Aggiorna prezzo corrente posizione"""
        if position_id in self.positions:
            self.positions[position_id].current_price = new_price
