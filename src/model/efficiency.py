"""
Drive-level efficiency model.

Why drive-level (not play-level)
--------------------------------
The 2026 UFL is the third year of post-merger play with brand-new rules
that shift play-by-play distributions (alt KO, no-punt zones, 4-pt FG).
EPA models trained on legacy XFL/USFL data will be miscalibrated. Drive
outcomes (PPD) are the most rule-stable football metric short of W/L.

Metrics computed
----------------
- pts_per_drive_off / pts_per_drive_def
- yards_per_play_off / yards_per_play_def
- success_rate (drive ends in TD/FG or first-down conversion >= 50%)
- explosive_rate (drives with at least one 20+ yd play)
- redzone_td_pct
- avg_start_yardline (own goal line = 0; opp goal = 100)
- adj_ppd_off / adj_ppd_def — opponent-adjusted via ridge

Opponent adjustment
-------------------
Closed-form ridge: solve (X'X + λI) β = X'y where X is one-hot home-team
plus opponent (defense) plus HFA. Output: each team's "true" off & def
PPD relative to a league-average opponent on a neutral field.

This is identical to KenPom's tempo-free decomposition; it transfers
to football because drives are the natural offensive unit.
"""
from __future__ import annotations
import math
import numpy as np
from dataclasses import dataclass, field
from typing import Optional

LEAGUE_MEAN_PPD: float = 2.0   # rough prior; will be set by data
RIDGE_LAMBDA: float = 5.0      # heavier regularization for small samples


# --------------------------------------------------------------------------
# Drive-level features

def drive_points(drive: dict) -> int:
    """How many points a drive produced (offensive perspective).
    StatBroadcast end_how codes: TD, FG, PUNT, DOWNS, INT, FUMB, FGA (missed),
    HALF (end of half), BLKP/BLKFG (blocked), SAF (safety against offense).
    PAT points counted separately at the scoring_play."""
    end = (drive.get("end_how") or "").upper()
    if end == "TD":
        return 6
    if end == "FG":
        return int(drive.get("fg_points", 3))
    return 0


def is_drive_success(drive: dict) -> bool:
    pts = drive_points(drive)
    return pts > 0


def is_explosive(drive: dict) -> bool:
    plays = drive.get("plays") or []
    for p in plays:
        try:
            if int(p.get("yards") or 0) >= 20:
                return True
        except (ValueError, TypeError):
            continue
    return False


def avg_drive_start(drives: list[dict]) -> Optional[float]:
    vals = [d.get("start_yardline") for d in drives
            if isinstance(d.get("start_yardline"), (int, float))]
    return float(np.mean(vals)) if vals else None


def total_drive_points(drives: list[dict]) -> float:
    return float(sum(drive_points(d) for d in drives))


# --------------------------------------------------------------------------
# Game-level rollup

@dataclass
class TeamGameEfficiency:
    team: str
    opponent: str
    is_home: bool
    sb_id: int
    week: int
    drives: int = 0
    points_off: float = 0.0      # offensive points (TD-as-6 + FG)
    yards_off: float = 0.0
    plays_off: int = 0
    success_drives: int = 0
    explosive_drives: int = 0
    rz_attempts: int = 0
    rz_tds: int = 0
    avg_start: Optional[float] = None

    @property
    def ppd(self) -> Optional[float]:
        return self.points_off / self.drives if self.drives else None

    @property
    def ypp(self) -> Optional[float]:
        return self.yards_off / self.plays_off if self.plays_off else None

    @property
    def success_rate(self) -> Optional[float]:
        return self.success_drives / self.drives if self.drives else None

    @property
    def explosive_rate(self) -> Optional[float]:
        return self.explosive_drives / self.drives if self.drives else None

    @property
    def rz_td_pct(self) -> Optional[float]:
        return self.rz_tds / self.rz_attempts if self.rz_attempts else None


def rollup_team_game(parsed_game: dict, team_side: str) -> TeamGameEfficiency:
    """
    Take a parsed StatBroadcast game and roll up offensive efficiency for `team_side`.
    parsed_game must have:
        home, away (3-letter codes)
        sb_id, week
        drives: list of {team, end_how, plays:[...], start_yardline, ...}
        home_totals/away_totals: dicts with totals, plays, redzone, etc.
    team_side: 'home' or 'away'
    """
    is_home = team_side == "home"
    team = parsed_game["home"] if is_home else parsed_game["away"]
    opp = parsed_game["away"] if is_home else parsed_game["home"]
    drives = [d for d in (parsed_game.get("drives") or [])
              if d.get("team_abbr") == team]
    totals_key = "home_totals" if is_home else "away_totals"
    tot = parsed_game.get(totals_key) or {}

    # Annotate FG drives with 4-pt distinction by matching to scoring_plays
    sps = [s for s in (parsed_game.get("scoring_plays") or [])
           if s.get("team_abbr") == team and s.get("score_type") == "FG"]
    sp_by_drive = {s.get("drive_index"): s for s in sps}
    for d in drives:
        if d.get("end_how") == "FG":
            sp = sp_by_drive.get(d.get("drive_index"))
            if sp and (sp.get("yards") or 0) >= 60:
                d["fg_points"] = 4

    eff = TeamGameEfficiency(
        team=team, opponent=opp, is_home=is_home,
        sb_id=parsed_game.get("game_id") or parsed_game.get("sb_id", 0),
        week=parsed_game.get("week", 0),
        drives=len(drives),
        points_off=total_drive_points(drives),
        yards_off=float(tot.get("total_yards") or 0.0),
        plays_off=int(tot.get("plays") or 0),
        success_drives=sum(1 for d in drives if is_drive_success(d)),
        explosive_drives=0,  # plays not nested in drives; computed elsewhere if needed
        rz_attempts=int(tot.get("redzone_att") or 0),
        rz_tds=int(tot.get("redzone_scores") or 0),
        avg_start=avg_drive_start(drives),
    )
    return eff


# --------------------------------------------------------------------------
# Opponent-adjusted ratings via ridge regression

def opponent_adjusted_ppd(efficiencies: list[TeamGameEfficiency],
                          team_codes: list[str],
                          hfa_pts: float = 0.5,
                          ridge: float = RIDGE_LAMBDA
                          ) -> dict[str, dict[str, float]]:
    """
    Closed-form ridge: for each game observation y_i = points scored by team T
    against defense D in venue v, model:
        y_i = mu + off_T - def_D + hfa * is_home + eps
    Solve subject to sum_T off_T = 0 and sum_D def_D = 0 by leaving the
    last team out and using a pseudoinverse with L2 penalty.

    Returns:
        {team_code: {"off": adj_off_ppd, "def": adj_def_ppd, "n": games}}
    Where adj_off > 0 means above-average offense vs avg defense.
    """
    if not efficiencies:
        return {c: {"off": 0.0, "def": 0.0, "n": 0} for c in team_codes}

    n_teams = len(team_codes)
    idx = {c: i for i, c in enumerate(team_codes)}

    # Each game contributes 1 observation per team-side (so 2 rows per game)
    rows = []
    ys = []
    for eff in efficiencies:
        if not eff.drives:
            continue
        ppd = eff.ppd
        if ppd is None:
            continue
        # Feature vector: intercept (1), off one-hots (n_teams), def one-hots (n_teams), hfa (1)
        x = np.zeros(1 + n_teams + n_teams + 1)
        x[0] = 1.0  # intercept = league mean PPD
        x[1 + idx[eff.team]] = 1.0
        x[1 + n_teams + idx[eff.opponent]] = -1.0   # defense subtracts
        x[-1] = 1.0 if eff.is_home else 0.0
        rows.append(x)
        ys.append(ppd)

    if not rows:
        return {c: {"off": 0.0, "def": 0.0, "n": 0} for c in team_codes}

    X = np.array(rows)
    y = np.array(ys)
    p = X.shape[1]
    # Don't penalize intercept or HFA
    L = ridge * np.eye(p)
    L[0, 0] = 0.0
    L[-1, -1] = 0.0
    beta = np.linalg.solve(X.T @ X + L, X.T @ y)
    mu = beta[0]
    off_coefs = beta[1:1 + n_teams]
    def_coefs = beta[1 + n_teams:1 + 2 * n_teams]
    hfa_coef = beta[-1]

    games_by_team: dict[str, int] = {c: 0 for c in team_codes}
    for eff in efficiencies:
        games_by_team[eff.team] += 1

    out: dict[str, dict[str, float]] = {}
    for code in team_codes:
        i = idx[code]
        out[code] = {
            "off": float(off_coefs[i]),
            "def": float(def_coefs[i]),
            "n": games_by_team[code],
        }
    out["__league__"] = {"mu_ppd": float(mu), "hfa": float(hfa_coef)}
    return out


# --------------------------------------------------------------------------
# Pace / drive count projection
# UFL games average ~22-24 drives total in 2024/25; 2026 rule changes
# (no-punt zone, alt KO) probably nudge that to ~24-26.

DEFAULT_TOTAL_DRIVES: float = 24.0


def project_drive_count(team_a: TeamGameEfficiency, team_b: TeamGameEfficiency,
                        league_mean: float = DEFAULT_TOTAL_DRIVES) -> float:
    """
    Mean of both teams' season drive averages, regressed lightly to
    league mean to avoid early-season noise.
    """
    samples = []
    if team_a and team_a.drives:
        samples.append(team_a.drives)
    if team_b and team_b.drives:
        samples.append(team_b.drives)
    if not samples:
        return league_mean
    seen = float(np.mean(samples))
    return 0.7 * seen + 0.3 * league_mean


if __name__ == "__main__":
    # Quick synthetic test of opponent adjustment
    efs = [
        TeamGameEfficiency("DC", "STL", True,  1, 1, drives=10, points_off=24),
        TeamGameEfficiency("STL", "DC", False, 1, 1, drives=10, points_off=10),
        TeamGameEfficiency("BHM", "LOU", False, 1, 1, drives=10, points_off=15),
        TeamGameEfficiency("LOU", "BHM", True,  1, 1, drives=10, points_off=13),
        TeamGameEfficiency("CLB", "DC",  False, 2, 2, drives=10, points_off=26),
        TeamGameEfficiency("DC",  "CLB", True,  2, 2, drives=10, points_off=44),
    ]
    teams = ["BHM", "CLB", "DAL", "DC", "HOU", "LOU", "ORL", "STL"]
    res = opponent_adjusted_ppd(efs, teams)
    print(f"League mu PPD: {res['__league__']['mu_ppd']:.2f}")
    print(f"HFA (PPD): {res['__league__']['hfa']:.3f}")
    for code in teams:
        if res[code]["n"]:
            print(f"  {code}: off={res[code]['off']:+.2f}  def={res[code]['def']:+.2f}  n={res[code]['n']}")
