"""Fee-aware edge and sizing math for binary event contracts.

Contracts pay $1.00 if the event happens, else $0.00. Prices are in dollars (0.01-0.99).
The per-contract fee is charged on purchase (Wealthsimple Predict reportedly charges
US$0.02; verify before relying on it).

This module is advisory only: it never places orders.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

DEFAULT_FEE = 0.02


@dataclass(frozen=True)
class Recommendation:
    side: str            # "YES" or "NO"
    price: float         # price per contract you would pay (dollars)
    contracts: int
    p_win: float         # model probability that this side wins
    ev_per_contract: float   # expected profit per contract after fee
    cost: float          # total outlay including fees


def ev_per_contract(p_win: float, price: float, fee: float = DEFAULT_FEE) -> float:
    """Expected profit per contract after the entry fee."""
    return p_win - price - fee


def break_even_edge(price: float, fee: float = DEFAULT_FEE) -> float:
    """Model probability must exceed price by at least this much to break even."""
    return fee


def kelly_fraction(p_win: float, price: float, fee: float = DEFAULT_FEE) -> float:
    """Full-Kelly fraction of bankroll for a binary bet costing price+fee, paying $1."""
    cost = price + fee
    if cost >= 1.0:
        return 0.0
    return max(0.0, (p_win - cost) / (1.0 - cost))


def recommend(
    p_yes: float,
    yes_ask: float,
    no_ask: float,
    bankroll: float,
    *,
    fee: float = DEFAULT_FEE,
    min_ev: float = 0.03,
    kelly_scale: float = 0.25,
    max_stake_frac: float = 0.02,
    max_contracts: Optional[int] = None,
) -> Optional[Recommendation]:
    """Best +EV side to buy at the ask, or None.

    p_yes: model probability the event resolves YES.
    min_ev: required expected profit per contract after fee (cushion for model error).
    kelly_scale: fraction of full Kelly to stake (fractional Kelly).
    max_stake_frac: hard cap on the share of bankroll risked on one trade.
    """
    if not (0.0 < p_yes < 1.0):
        return None
    candidates = []
    for side, p, ask in (("YES", p_yes, yes_ask), ("NO", 1.0 - p_yes, no_ask)):
        if ask is None or not (0.0 < ask < 1.0):
            continue
        ev = ev_per_contract(p, ask, fee)
        if ev >= min_ev:
            candidates.append((ev, side, p, ask))
    if not candidates:
        return None
    ev, side, p, ask = max(candidates)
    stake = bankroll * min(kelly_fraction(p, ask, fee) * kelly_scale, max_stake_frac)
    n = int(stake // (ask + fee))
    if max_contracts is not None:
        n = min(n, max_contracts)
    if n < 1:
        return None
    return Recommendation(side, ask, n, p, ev, round(n * (ask + fee), 4))
