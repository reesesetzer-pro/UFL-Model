"""
Score projector. Combines Elo + opponent-adjusted PPD into a score line.

Formula
-------
For each team T playing opponent O at venue V:
    ppd_T = mu_ppd + off_T - def_O + hfa * I(home)
    drives_T = pace_T_O / 2
    points_T = ppd_T * drives_T

Then we have an Elo-derived spread (which is a pure-rating signal that
includes things drive efficiency can miss like turnovers, special teams):
    elo_spread_home = (home_rating + HFA - away_rating) / ELO_PER_POINT

Final spread = w * elo_spread + (1 - w) * efficiency_spread
where w starts at 0.5 (Elo and efficiency get equal weight) and we tune
later via calibration.

Variance
--------
σ_total = 14.5 in 2026 (NFL is ~13.5; UFL inflated by rule changes that
push more scoring + more variance in possession outcomes).
σ_margin = 13.5 (NFL ~13.0; small bump same reason).

Both will be recalibrated weekly off realized residuals.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Optional
import numpy as np

from src.model.elo import (
    TeamElo, expected_win_prob, elo_to_spread,
    HFA_ELO, ELO_PER_POINT,
)
from src.model.efficiency import DEFAULT_TOTAL_DRIVES


# Calibration. Re-fit weekly off realized residuals.
# 2026-05-02 (21 games): realized total RMSE=14.7, margin RMSE=10.8.
# Total sigma left at 14.5 (matches RMSE within noise). Margin tightened from
# 13.5 -> 11.0 to match realized — was inflating spread/ML probabilities.
SIGMA_TOTAL: float = 14.5
SIGMA_MARGIN: float = 11.0
EFFICIENCY_HFA_PPD: float = 0.5    # per-team home boost in PPD space
ELO_BLEND_WEIGHT: float = 0.5      # weight on Elo vs efficiency spread

# League means (re-derive from data each run)
LEAGUE_MU_PPD: float = 2.0


@dataclass
class GameProjection:
    home: str
    away: str
    home_score_proj: float
    away_score_proj: float
    spread: float                 # negative = home favored by abs(spread) pts
    total: float
    home_win_prob: float
    away_win_prob: float
    sigma_total: float = SIGMA_TOTAL
    sigma_margin: float = SIGMA_MARGIN
    components: dict = field(default_factory=dict)  # debug


def project_game(home: str, away: str,
                 elo: dict[str, TeamElo],
                 ppd_adj: dict[str, dict[str, float]],
                 league_mu_ppd: Optional[float] = None,
                 ppd_hfa: Optional[float] = None,
                 expected_drives_total: float = DEFAULT_TOTAL_DRIVES,
                 elo_blend_weight: float = ELO_BLEND_WEIGHT,
                 sigma_total: float = SIGMA_TOTAL,
                 sigma_margin: float = SIGMA_MARGIN,
                 weather_mult: float = 1.0,
                 weather_summary: Optional[str] = None,
                 ) -> GameProjection:
    """
    home/away: team codes
    elo: {code: TeamElo}
    ppd_adj: {code: {"off","def","n"}, "__league__": {"mu_ppd","hfa"}} from
             opponent_adjusted_ppd()

    league_mu_ppd / ppd_hfa default to the regression-learned values in
    ppd_adj["__league__"] so off/def coefs and the baseline live in the same
    space. Override only if you know what you're doing.
    """
    league = ppd_adj.get("__league__") or {}
    if league_mu_ppd is None:
        league_mu_ppd = league.get("mu_ppd", LEAGUE_MU_PPD)
    if ppd_hfa is None:
        ppd_hfa = league.get("hfa", EFFICIENCY_HFA_PPD)

    h_elo = elo[home].rating
    a_elo = elo[away].rating

    # Elo-spread (positive = home favored)
    elo_diff_inc_hfa = (h_elo + HFA_ELO) - a_elo
    spread_elo = elo_to_spread(elo_diff_inc_hfa)
    home_wp_elo = expected_win_prob(h_elo, a_elo, hfa_for_a=HFA_ELO)

    # Efficiency-spread
    h = ppd_adj.get(home, {"off": 0.0, "def": 0.0})
    a = ppd_adj.get(away, {"off": 0.0, "def": 0.0})
    ppd_h = league_mu_ppd + h["off"] - a["def"] + ppd_hfa
    ppd_a = league_mu_ppd + a["off"] - h["def"]
    drives_per_team = expected_drives_total / 2.0
    eff_pts_h = ppd_h * drives_per_team
    eff_pts_a = ppd_a * drives_per_team
    spread_eff = eff_pts_h - eff_pts_a
    total_eff = eff_pts_h + eff_pts_a

    # Blend
    spread = elo_blend_weight * spread_elo + (1 - elo_blend_weight) * spread_eff
    total = total_eff  # totals come purely from efficiency; Elo can't say anything about pace

    # Weather adjustment — applied to TOTAL only. Spread is the diff between
    # team strengths and weather hits both teams symmetrically. Multiplier
    # comes from `scripts.weather.football_scoring_multiplier()` upstream.
    total = total * weather_mult

    # Recover scores from blended spread + efficiency total
    home_score = (total + spread) / 2.0
    away_score = (total - spread) / 2.0

    # Win prob: blended spread → normal CDF on margin
    home_wp_eff = 1.0 - _norm_cdf(0.0, mu=spread_eff, sigma=sigma_margin)
    home_wp = elo_blend_weight * home_wp_elo + (1 - elo_blend_weight) * home_wp_eff

    # Spread sign convention (industry): home favored = negative number
    market_spread = -spread

    return GameProjection(
        home=home, away=away,
        home_score_proj=round(home_score, 1),
        away_score_proj=round(away_score, 1),
        spread=round(market_spread, 2),
        total=round(total, 2),
        home_win_prob=round(home_wp, 4),
        away_win_prob=round(1 - home_wp, 4),
        sigma_total=sigma_total,
        sigma_margin=sigma_margin,
        components={
            "elo_diff_inc_hfa": round(elo_diff_inc_hfa, 1),
            "spread_elo": round(spread_elo, 2),
            "spread_eff": round(spread_eff, 2),
            "ppd_home": round(ppd_h, 2),
            "ppd_away": round(ppd_a, 2),
            "expected_drives_per_team": round(drives_per_team, 2),
            "weather_mult": round(weather_mult, 4),
            "weather": weather_summary or "",
        },
    )


def _norm_cdf(x: float, mu: float = 0.0, sigma: float = 1.0) -> float:
    """Standard-library normal CDF (avoid scipy dep)."""
    if sigma <= 0:
        return 0.5
    return 0.5 * (1 + math.erf((x - mu) / (sigma * math.sqrt(2))))


# --------------------------------------------------------------------------
# Distribution-aware probabilities for derived markets

def cover_prob(market_spread: float, model_spread: float,
               sigma_margin: float = SIGMA_MARGIN) -> float:
    """
    P(home covers `market_spread`).
    market_spread negative = home favored (e.g. -3.5 = home favored by 3.5).
    Home covers if home_margin > -market_spread.
        margin ~ N(model_margin, sigma_margin)
        model_margin = -model_spread (since spread = -margin)
    """
    model_margin = -model_spread
    needed = -market_spread
    return 1 - _norm_cdf(needed, mu=model_margin, sigma=sigma_margin)


def total_prob_over(market_total: float, model_total: float,
                    sigma_total: float = SIGMA_TOTAL) -> float:
    """P(actual total > market_total). Model total normal."""
    return 1 - _norm_cdf(market_total, mu=model_total, sigma=sigma_total)


def home_ml_prob(model_spread: float,
                 sigma_margin: float = SIGMA_MARGIN) -> float:
    """P(home wins) under N(model_margin, sigma_margin)."""
    model_margin = -model_spread
    return 1 - _norm_cdf(0.0, mu=model_margin, sigma=sigma_margin)


if __name__ == "__main__":
    # Synthetic
    from src.model.elo import default_2026_starting_ratings
    elo = default_2026_starting_ratings()
    ppd = {c: {"off": 0.0, "def": 0.0, "n": 0} for c in elo}
    ppd["__league__"] = {"mu_ppd": 2.0, "hfa": 0.0}
    proj = project_game("DC", "STL", elo, ppd)
    print(proj)
    print()
    proj2 = project_game(
        "DC", "BHM",
        elo, {"DC": {"off": 0.5, "def": -0.3, "n": 4},
              "BHM": {"off": -0.2, "def": 0.4, "n": 4},
              "__league__": {"mu_ppd": 2.0, "hfa": 0.0}}
    )
    print(proj2)
    # Cover probability check
    p = cover_prob(market_spread=-3.5, model_spread=-7.0)
    print(f"\nP(home covers -3.5 given we project -7.0): {p*100:.1f}%")
