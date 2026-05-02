"""
Derived markets: first-half lines and team totals.

The Odds API only returns h2h/spreads/totals for UFL. Books like DraftKings
and FanDuel publish 1H and team-total markets in their own UI but they're
not available via the API. So we compute model fair lines from the full-game
projection and let the user shop them manually.

Calibration ratios (from NFL/college football empirical studies; will be
recalibrated weekly off UFL data once we have ~30 games):

    1H_total  = 0.46 * full_total       (favorites tend to slightly
                                         outscore in H2 but not much)
    1H_spread = 0.55 * full_spread      (favorites pull ahead more in H1)

Team totals decompose the full-game total according to the spread:

    home_team_total = (full_total - full_spread) / 2  if spread is "home-favors-by"
    away_team_total = (full_total + full_spread) / 2

Variance:
    sigma_1H_total ≈ 9.5
    sigma_1H_margin ≈ 9.0
    sigma_team_total ≈ 8.5
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

# Tunables (re-fit later)
H1_TOTAL_RATIO: float = 0.46
H1_SPREAD_RATIO: float = 0.55
SIGMA_1H_TOTAL: float = 9.5
SIGMA_1H_MARGIN: float = 9.0
SIGMA_TEAM_TOTAL: float = 8.5


@dataclass
class DerivedLines:
    home: str
    away: str
    # First half
    h1_spread_home: float       # negative if home favored
    h1_total: float
    # Team totals (offense)
    home_team_total: float
    away_team_total: float
    sigma_1h_total: float = SIGMA_1H_TOTAL
    sigma_1h_margin: float = SIGMA_1H_MARGIN
    sigma_team_total: float = SIGMA_TEAM_TOTAL


def derive_lines(home: str, away: str,
                 model_spread_home: float,
                 model_total: float,
                 h1_total_ratio: float = H1_TOTAL_RATIO,
                 h1_spread_ratio: float = H1_SPREAD_RATIO,
                 ) -> DerivedLines:
    """
    model_spread_home: market-style number (-3.5 means home is 3.5-pt favorite)
    model_total: full-game expected total
    """
    h1_spread = model_spread_home * h1_spread_ratio
    h1_tot = model_total * h1_total_ratio

    # Team totals from spread + total
    home_total = (model_total - model_spread_home) / 2.0
    away_total = (model_total + model_spread_home) / 2.0

    return DerivedLines(
        home=home, away=away,
        h1_spread_home=round(h1_spread, 2),
        h1_total=round(h1_tot, 2),
        home_team_total=round(home_total, 2),
        away_team_total=round(away_total, 2),
    )


# --------------------------------------------------------------------------
# Probability helpers for derived markets

def _norm_cdf(x: float, mu: float, sigma: float) -> float:
    import math
    return 0.5 * (1 + math.erf((x - mu) / (sigma * math.sqrt(2))))


def h1_cover_prob(market_h1_spread: float, model_h1_spread: float,
                  sigma_margin: float = SIGMA_1H_MARGIN) -> float:
    """P(home covers 1H spread). market & model spreads are home-style."""
    model_margin = -model_h1_spread
    needed = -market_h1_spread
    return 1 - _norm_cdf(needed, mu=model_margin, sigma=sigma_margin)


def h1_total_over_prob(market_h1_total: float, model_h1_total: float,
                       sigma_total: float = SIGMA_1H_TOTAL) -> float:
    return 1 - _norm_cdf(market_h1_total, mu=model_h1_total, sigma=sigma_total)


def team_total_over_prob(market_team_total: float, model_team_total: float,
                         sigma_team_total: float = SIGMA_TEAM_TOTAL) -> float:
    return 1 - _norm_cdf(market_team_total, mu=model_team_total, sigma=sigma_team_total)


if __name__ == "__main__":
    d = derive_lines("DC", "STL", model_spread_home=-3.81, model_total=54.0)
    print(d)
    print(f"\n1H spread: {d.h1_spread_home}, 1H total: {d.h1_total}")
    print(f"DC team total: {d.home_team_total}, STL team total: {d.away_team_total}")
    p = h1_cover_prob(market_h1_spread=-1.5, model_h1_spread=-2.1)
    print(f"\nP(DC covers 1H -1.5 vs model -2.1): {p*100:.1f}%")
