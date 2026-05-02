"""
Bayesian prior-blend layer.

Why
---
With only ~5-8 games of data per UFL team and brand-new rosters, our Elo
+ drive-efficiency model has wide error bars early in the season. Naively
trusting our spread can produce false +EV signals from sample noise.

The market consensus (devigged across 7 books) is the strongest, lowest-
variance prior we have. So we blend our model with the market, where the
weight on our model grows as we accumulate data:

    model_weight = 0.20 + 0.50 * min(games_played / 8, 1.0)
    final_spread = model_weight * model_spread + (1 - model_weight) * market_spread

At the season opener (0 games): 20% model / 80% market.
After Week 1 (1 game):           26% model / 74% market.
After Week 8 (8 games):          70% model / 30% market.
Playoffs:                        70% model / 30% market.

This is intentionally conservative. Our edges in early season come from
catching books that misprice unusual matchups (rule changes, expansion
teams, new QBs), not from beating the consensus on regular spots.

Calibration: re-fit `model_weight_floor` and `model_weight_growth` after
the season ends.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


MODEL_WEIGHT_FLOOR: float = 0.20
MODEL_WEIGHT_GROWTH: float = 0.50
GAMES_TO_FULL_WEIGHT: int = 8


def model_weight(games_played: int,
                 floor: float = MODEL_WEIGHT_FLOOR,
                 growth: float = MODEL_WEIGHT_GROWTH,
                 to_full: int = GAMES_TO_FULL_WEIGHT) -> float:
    """How much weight to put on our model vs the market."""
    if games_played <= 0:
        return floor
    return floor + growth * min(games_played / to_full, 1.0)


@dataclass
class BlendedLine:
    market_spread: float
    market_total: float
    model_spread: float
    model_total: float
    blended_spread: float
    blended_total: float
    weight_used: float


def blend(market_spread: float, market_total: float,
          model_spread: float, model_total: float,
          games_played: int) -> BlendedLine:
    w = model_weight(games_played)
    return BlendedLine(
        market_spread=round(market_spread, 2),
        market_total=round(market_total, 2),
        model_spread=round(model_spread, 2),
        model_total=round(model_total, 2),
        blended_spread=round(w * model_spread + (1 - w) * market_spread, 2),
        blended_total=round(w * model_total + (1 - w) * market_total, 2),
        weight_used=round(w, 3),
    )


def edge_against_market(market_spread: float, market_total: float,
                        model_spread: float, model_total: float,
                        games_played: int) -> dict:
    """Returns the gap between blended model and current market.
    Positive `spread_edge` = model thinks home should be a bigger favorite."""
    bl = blend(market_spread, market_total, model_spread, model_total, games_played)
    return {
        "blended_spread": bl.blended_spread,
        "blended_total": bl.blended_total,
        "spread_edge": round(bl.blended_spread - market_spread, 2),
        "total_edge": round(bl.blended_total - market_total, 2),
        "weight": bl.weight_used,
    }


if __name__ == "__main__":
    print(f"Game 0 weight: {model_weight(0):.2f}")
    print(f"Game 4 weight: {model_weight(4):.2f}")
    print(f"Game 8 weight: {model_weight(8):.2f}")
    print(f"Playoffs (10): {model_weight(10):.2f}")
    print()
    bl = blend(market_spread=-3.5, market_total=46.5,
               model_spread=-7.0,  model_total=49.0,
               games_played=5)
    print(bl)
