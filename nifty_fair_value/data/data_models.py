from dataclasses import dataclass
from typing import List, Optional

@dataclass
class OptionData:
    strike: float
    call_ltp: float
    put_ltp: float
    call_oi: int
    put_oi: int
    call_iv: Optional[float] = 0.0
    put_iv: Optional[float] = 0.0

@dataclass
class MarketData:
    spot: float
    futures: float
    futures_vwap: float
    futures_oi: int
    options: List[OptionData]
    atm_strike: float
    atm_call_ltp: float
    atm_put_ltp: float
    atm_ce_iv: float
    atm_pe_iv: float
    total_ce_oi: int
    total_pe_oi: int
    expiry: str
    futures_expiry: str
    session: str = "Market Hours"
    futures_oi_chg_pct: float = 0.0
    bid_price: float = 0.0
    offer_price: float = 0.0
    bid_quantity: int = 0
    offer_quantity: int = 0
