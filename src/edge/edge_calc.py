"""
Edge calculator and bet sizer.

Edge formula:    edge = p_model * decimal_odds - 1
                       = p_model - p_implied  (alternate form, in prob space)

We use prob-space edge so it's directly comparable across odds magnitudes.

Sizing (1/4 Kelly + 2% bankroll cap):
    full_kelly  = (p * (b + 1) - 1) / b     where b = decimal_odds - 1
    quarter     = max(0, full_kelly / 4)
    stake_pct   = min(quarter, 0.02)        # 2% per-bet hard cap
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Optional

from src.edge.no_vig import (
    american_to_decimal, american_to_implied, decimal_to_american,
)

# Sizing tunables
KELLY_FRACTION: float = 0.25
BANKROLL_CAP_PCT: float = 0.02
EDGE_THRESHOLD_FULL_GAME: float = 0.03
EDGE_THRESHOLD_DERIVED: float = 0.05
MIN_BANKROLL_PCT: float = 0.0025  # 0.25% minimum stake to avoid noise


@dataclass
class BetCandidate:
    label: str                       # "DC -3.5", "DC ML", "Over 46.5", etc
    market: str                      # "spread" | "moneyline" | "total" | "h1_spread" | "h1_total" | "team_total"
    side: str                        # "home_ml", "away_ml", "home_spread", ..., "over", "under"
    p_model: float                   # blended model prob
    p_market: float                  # devigged consensus prob
    book: str                        # best-priced book
    american_odds: float
    decimal_odds: float = 0.0
    edge_prob: float = 0.0           # p_model - p_market
    edge_ev: float = 0.0             # p_model * decimal - 1
    stake_pct: float = 0.0           # of bankroll
    full_kelly_pct: float = 0.0
    passes_threshold: bool = False
    reason: str = ""


def edge_and_size(p_model: float, american_odds: float,
                  is_derived: bool = False,
                  kelly_fraction: float = KELLY_FRACTION,
                  bankroll_cap: float = BANKROLL_CAP_PCT,
                  min_size: float = MIN_BANKROLL_PCT,
                  ) -> dict:
    if not (0 < p_model < 1):
        return {"valid": False, "reason": f"p_model out of (0,1): {p_model}"}

    decimal = american_to_decimal(american_odds)
    p_implied = 1.0 / decimal
    b = decimal - 1.0
    full_kelly = (p_model * decimal - 1) / b if b > 0 else 0.0
    sized = max(0.0, full_kelly * kelly_fraction)
    sized = min(sized, bankroll_cap)
    if sized < min_size:
        sized = 0.0

    edge_prob = p_model - p_implied
    edge_ev = p_model * decimal - 1
    threshold = EDGE_THRESHOLD_DERIVED if is_derived else EDGE_THRESHOLD_FULL_GAME
    passes = (edge_prob >= threshold) and (sized > 0)

    return {
        "valid": True,
        "decimal_odds": round(decimal, 4),
        "p_implied": round(p_implied, 4),
        "edge_prob": round(edge_prob, 4),
        "edge_ev": round(edge_ev, 4),
        "full_kelly_pct": round(full_kelly, 4),
        "stake_pct": round(sized, 4),
        "passes_threshold": passes,
        "threshold_used": threshold,
    }


def evaluate_market(*, label: str, market: str, side: str,
                    p_model: float, p_market: float, book: str,
                    american_odds: float, is_derived: bool = False
                    ) -> BetCandidate:
    res = edge_and_size(p_model, american_odds, is_derived=is_derived)
    bc = BetCandidate(
        label=label, market=market, side=side,
        p_model=round(p_model, 4),
        p_market=round(p_market, 4),
        book=book,
        american_odds=american_odds,
        decimal_odds=res.get("decimal_odds", 0.0),
        edge_prob=res.get("edge_prob", 0.0),
        edge_ev=res.get("edge_ev", 0.0),
        stake_pct=res.get("stake_pct", 0.0),
        full_kelly_pct=res.get("full_kelly_pct", 0.0),
        passes_threshold=res.get("passes_threshold", False),
        reason=res.get("reason", ""),
    )
    return bc


def stake_units(bankroll: float, stake_pct: float) -> float:
    return round(bankroll * stake_pct, 2)


if __name__ == "__main__":
    # We model home -3.5 cover at 60% prob; market gives -110.
    # Implied = 52.4%, our edge = +7.6 prob points.
    bc = evaluate_market(
        label="DC -3.5", market="spread", side="home_spread",
        p_model=0.60, p_market=0.524,
        book="dk", american_odds=-110, is_derived=False,
    )
    print(bc)
    bank = 10000
    print(f"\nOn $10k bankroll: stake = ${stake_units(bank, bc.stake_pct):.2f}")

    # Marginal edge case (3% threshold for full-game)
    bc2 = evaluate_market(
        label="STL ML", market="moneyline", side="home_ml",
        p_model=0.55, p_market=0.523,
        book="fd", american_odds=-110,
    )
    print()
    print(bc2)

    # Derived market needs 5%
    bc3 = evaluate_market(
        label="Over 24.5 1H", market="h1_total", side="over",
        p_model=0.56, p_market=0.524,
        book="mgm", american_odds=-110, is_derived=True,
    )
    print()
    print(bc3)
