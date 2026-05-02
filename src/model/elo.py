"""
Elo rating system tuned for UFL.

Design choices and rationale
----------------------------
- K = 24. Football season is short (10 games). NFL uses K=20 over a 17-game
  season, so we bump up slightly to learn faster.
- HFA = 1.5 points (≈ 50 Elo). UFL's "hub model" had been weak in 2024 and is
  fully home-and-away in 2026, but small samples + new venues = small HFA.
- Expansion teams (CLB, LOU, ORL) start at 1500 (league mean). All 8 rosters
  were liquidated and redrafted Jan 2026, so even returning franchises
  (BHM, DAL, DC, HOU, STL) get strongly regressed:
        seed = 1500 + 0.25 * (final_2025 - 1500)
- Margin-of-victory multiplier (Silver/538-style):
        mov = ln(|MOV| + 1) * (2.2 / (elo_diff_winner * 0.001 + 2.2))
  Smooths blowouts vs nail-biters; prevents overcorrection on a 47-3.

Public API
----------
- compute_elo_history(games, prior_seed) -> per-game Elo before/after, ratings
- expected_win_prob(rating_a, rating_b, hfa_for_a)
- elo_to_spread(rating_diff_inc_hfa)        # one-direction Elo->point diff
- spread_to_elo(spread_pts)
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Iterable, Optional

# --------------------------------------------------------------------------
# Tunables — all in one place so we can sweep / grid search later.

K: float = 24.0
HFA_ELO: float = 50.0          # ≈ 1.5 pts; HFA in Elo points
ELO_PER_POINT: float = 33.0    # Calibration: NFL ≈ 25; UFL probably similar
LEAGUE_MEAN: float = 1500.0
RETURNING_REGRESSION: float = 0.25  # weight on prior-year final
EXPANSION_SEED: float = LEAGUE_MEAN

# --------------------------------------------------------------------------

@dataclass
class TeamElo:
    code: str
    rating: float = LEAGUE_MEAN
    games: int = 0


@dataclass
class EloGameRow:
    """Snapshot of an Elo update."""
    sb_id: int
    week: int
    home: str
    away: str
    home_score: int
    away_score: int
    home_elo_pre: float
    away_elo_pre: float
    home_elo_post: float
    away_elo_post: float
    expected_home_wp: float
    actual_home: float        # 1.0/0.5/0.0
    mov_mult: float


def expected_win_prob(rating_a: float, rating_b: float,
                      hfa_for_a: float = 0.0) -> float:
    """Standard Elo logistic. hfa_for_a in Elo points."""
    diff = (rating_a + hfa_for_a) - rating_b
    return 1.0 / (1.0 + 10 ** (-diff / 400.0))


def elo_to_spread(rating_diff_inc_hfa: float) -> float:
    """Translate a (home Elo + HFA - away Elo) diff into a model spread.
    Positive = home favored by that many points."""
    return rating_diff_inc_hfa / ELO_PER_POINT


def spread_to_elo(spread_pts: float) -> float:
    """Inverse: market spread (home favored by X) -> implied Elo diff."""
    return spread_pts * ELO_PER_POINT


def _mov_multiplier(score_diff: int, elo_diff_winner: float) -> float:
    """Silver-style MOV multiplier. score_diff = winner - loser (>=0)."""
    if score_diff <= 0:
        return 1.0
    return math.log(abs(score_diff) + 1) * (2.2 / (elo_diff_winner * 0.001 + 2.2))


def update_elo(home: TeamElo, away: TeamElo, home_score: int, away_score: int,
               k: float = K, hfa: float = HFA_ELO) -> EloGameRow:
    pre_h, pre_a = home.rating, away.rating
    exp_h = expected_win_prob(pre_h, pre_a, hfa_for_a=hfa)
    if home_score > away_score:
        actual_h = 1.0
        winner_elo_diff = (pre_h + hfa) - pre_a
    elif home_score < away_score:
        actual_h = 0.0
        winner_elo_diff = pre_a - (pre_h + hfa)
    else:
        actual_h = 0.5
        winner_elo_diff = abs((pre_h + hfa) - pre_a)
    mov = _mov_multiplier(abs(home_score - away_score), winner_elo_diff)
    delta = k * mov * (actual_h - exp_h)
    home.rating += delta
    away.rating -= delta
    home.games += 1
    away.games += 1
    return EloGameRow(
        sb_id=0, week=0, home=home.code, away=away.code,
        home_score=home_score, away_score=away_score,
        home_elo_pre=pre_h, away_elo_pre=pre_a,
        home_elo_post=home.rating, away_elo_post=away.rating,
        expected_home_wp=exp_h, actual_home=actual_h, mov_mult=mov,
    )


def seed_team_ratings(returning_seeds: dict[str, float],
                      expansion_codes: Iterable[str]) -> dict[str, TeamElo]:
    """
    returning_seeds: {team_code: prior_year_final_elo}
    expansion_codes: codes that start at LEAGUE_MEAN
    """
    out: dict[str, TeamElo] = {}
    for code, final in returning_seeds.items():
        seeded = LEAGUE_MEAN + RETURNING_REGRESSION * (final - LEAGUE_MEAN)
        out[code] = TeamElo(code=code, rating=seeded)
    for code in expansion_codes:
        out[code] = TeamElo(code=code, rating=EXPANSION_SEED)
    return out


def compute_elo_history(games: list[dict],
                        starting_ratings: dict[str, TeamElo]) -> list[EloGameRow]:
    """
    games: list of dicts with keys
        sb_id, week, home, away, home_score, away_score
    Sorted by date/week before calling.
    """
    history: list[EloGameRow] = []
    for g in games:
        h = starting_ratings[g["home"]]
        a = starting_ratings[g["away"]]
        row = update_elo(h, a, g["home_score"], g["away_score"])
        row.sb_id = g.get("sb_id", 0)
        row.week = g.get("week", 0)
        history.append(row)
    return history


# --------------------------------------------------------------------------
# Default seeds for 2026.
#
# Returning teams: pulled from final 2025 Elo. We don't yet have those — so
# we use neutral defaults and let the regression coefficient absorb noise.
# This file should be updated once we have hand-validated 2025 finals.

DEFAULT_2026_SEEDS: dict[str, float] = {
    # All five returning franchises: regress ~75% to the mean by default
    "BHM": 1500.0,   # Stallions: dynasty era ended; redraft
    "DAL": 1500.0,   # Renegades: rebrand
    "DC":  1525.0,   # Defenders: 2025 champs (regressed)
    "HOU": 1490.0,   # Gamblers (rebrand of Roughnecks): bottom in 2025
    "STL": 1510.0,   # Battlehawks: solid but not elite
}
DEFAULT_2026_EXPANSION = ["CLB", "LOU", "ORL"]


def default_2026_starting_ratings() -> dict[str, TeamElo]:
    return seed_team_ratings(DEFAULT_2026_SEEDS, DEFAULT_2026_EXPANSION)


if __name__ == "__main__":
    # Sanity test
    ratings = default_2026_starting_ratings()
    print("Starting ratings:")
    for code, t in sorted(ratings.items()):
        print(f"  {code}: {t.rating:.1f}")
    # Synthetic: home team rated +50 above road team should win ~67% of the time
    p = expected_win_prob(1550, 1500, hfa_for_a=HFA_ELO)
    print(f"\n+50 home vs neutral away: {p*100:.1f}% (expect ~63%)")
    # Spread translation
    print(f"\n100 Elo diff -> spread: {elo_to_spread(100):.1f} pts")
    print(f"7-pt favorite -> Elo diff: {spread_to_elo(7):.0f}")
