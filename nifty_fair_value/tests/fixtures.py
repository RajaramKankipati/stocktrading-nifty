"""
Shared test fixtures: lightweight data classes that mirror the real market data
objects without requiring Groww API connectivity.
"""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class OptionData:
    strike: float
    call_ltp: float = 100.0
    put_ltp: float = 100.0
    call_iv: float = 15.0
    put_iv: float = 15.0
    call_oi: int = 100_000
    put_oi: int = 100_000


@dataclass
class MarketData:
    spot: float = 24_500.0
    futures: float = 24_528.0
    futures_vwap: float = 0.0
    futures_expiry: str = "2026-04-29"
    futures_oi_chg_pct: float = 0.5
    bid_price: float = 24_527.0
    offer_price: float = 24_529.0
    bid_quantity: int = 200
    offer_quantity: int = 150
    options: List[OptionData] = field(default_factory=list)
    total_ce_oi: int = 1_000_000
    total_pe_oi: int = 1_100_000


def make_chain(
    atm: float = 24_500,
    n_strikes: int = 7,
    step: int = 50,
    call_ltp: float = 120.0,
    put_ltp: float = 110.0,
    call_iv: float = 15.0,
    put_iv: float = 15.0,
    call_oi: int = 200_000,
    put_oi: int = 180_000,
) -> List[OptionData]:
    """Returns a synthetic option chain centred on `atm` with uniform spacing."""
    half = n_strikes // 2
    opts = []
    for i in range(-half, half + 1):
        strike = atm + i * step
        # ATM has highest OI; decay away from ATM
        oi_factor = max(0.1, 1.0 - abs(i) * 0.15)
        opts.append(OptionData(
            strike=strike,
            call_ltp=max(0.5, call_ltp - i * 10),
            put_ltp=max(0.5, put_ltp + i * 10),
            call_iv=call_iv,
            put_iv=put_iv,
            call_oi=int(call_oi * oi_factor),
            put_oi=int(put_oi * oi_factor),
        ))
    return opts


def make_conf(
    direction: str = "LONG",
    score: int = 3,
    data_reliable: bool = True,
    conflict: bool = False,
    conflict_sources: Optional[List[str]] = None,
) -> dict:
    """Returns a minimal ls_confidence dict for use in options_strategy tests."""
    return {
        "direction"       : direction,
        "score"           : score,
        "max_score"       : 5,
        "level"           : "HIGH" if score >= 4 else "MEDIUM" if score >= 2 else "LOW",
        "data_reliable"   : data_reliable,
        "conflict"        : conflict,
        "conflict_sources": conflict_sources or [],
        "checks"          : {},
    }
