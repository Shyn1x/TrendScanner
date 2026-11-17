"""Research-only shared-position arbiter for independent LONG strategies.

No exchange calls, orders, database access or production integration.
One net symbol position, one owner. Daily RSI2 gets priority over speculative
4H companion, but existing positions ALWAYS keep their owner/exit plan.
Cross-account caps are applied to estimated fee-inclusive stop risk.
"""
from dataclasses import dataclass
from typing import Iterable

MAX_OPEN_SYMBOLS = 3
MAX_RISK_PER_TRADE_PCT = 0.01
MAX_AGGREGATE_STOP_RISK_PCT = 0.03
MAX_GROSS_NOTIONAL_EQUITY = 1.5
PRIORITY = {"RSI2_1D": 0, "COMPANION_4H": 1}

@dataclass(frozen=True)
class Position:
    symbol: str
    owner: str
    risk_usdt: float
    notional_usdt: float

@dataclass(frozen=True)
class Proposal:
    symbol: str
    owner: str
    side: str
    risk_usdt: float
    notional_usdt: float
    score: float = 0.0

def select_entries(positions: Iterable[Position], proposals: Iterable[Proposal],
                   equity_usdt: float):
    """Return (accepted proposals, rejected (proposal, reason)) deterministically.

    Conservative: held symbols cannot be re-entered or reversed by any strategy.
    Proposals on a symbol from both strategies: RSI2 gets preference if not held.
    Limits assume STOP risk includes entry+exit fees and known expected slippage.
    """
    if not equity_usdt > 0:
        raise ValueError("EQUITY_MUST_BE_POSITIVE")
    held = tuple(positions)
    if len({p.symbol for p in held}) != len(held):
        raise ValueError("DUPLICATE_OPEN_SYMBOL")
    if any(p.owner not in PRIORITY or p.risk_usdt < 0 or p.notional_usdt < 0 for p in held):
        raise ValueError("INVALID_OPEN_POSITION")
    symbols={p.symbol for p in held}
    risk=sum(p.risk_usdt for p in held)
    gross=sum(p.notional_usdt for p in held)
    accepted=[]
    rejected=[]
    seen=set()
    ordered=sorted(proposals,key=lambda p:(PRIORITY.get(p.owner,999),p.score,p.symbol))
    for p in ordered:
        if p.owner not in PRIORITY or p.side != "LONG":
            rejected.append((p,"UNSUPPORTED_STRATEGY_OR_SIDE"));continue
        if p.symbol in symbols:
            rejected.append((p,"SYMBOL_ALREADY_OWNED"));continue
        if p.symbol in seen:
            rejected.append((p,"DUPLICATE_SIGNAL"));continue
        seen.add(p.symbol)
        if not(0<p.risk_usdt<=equity_usdt*MAX_RISK_PER_TRADE_PCT) or p.notional_usdt<=0:
            rejected.append((p,"INVALID_SIZE_OR_RISK"));continue
        if len(symbols)>=MAX_OPEN_SYMBOLS:
            rejected.append((p,"MAX_SYMBOL_SLOTS"));continue
        if risk+p.risk_usdt>equity_usdt*MAX_AGGREGATE_STOP_RISK_PCT+1e-9:
            rejected.append((p,"AGGREGATE_STOP_RISK"));continue
        if gross+p.notional_usdt>equity_usdt*MAX_GROSS_NOTIONAL_EQUITY+1e-9:
            rejected.append((p,"MAX_NOTIONAL"));continue
        accepted.append(p)
        symbols.add(p.symbol)
        risk+=p.risk_usdt
        gross+=p.notional_usdt
    return accepted,rejected
