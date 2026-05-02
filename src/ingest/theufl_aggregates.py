"""
theUFL.com season-aggregate scraper.

Each team has a server-rendered stats page at:
    https://www.theufl.com/teams/{slug}/stats

The page renders a 2-column table: Team | Opponent.
We parse it as season-to-date totals and split offense/defense.

Why this is useful:
- Sanity-check vs StatBroadcast XML aggregates
- Get standings + team trends without iterating every game XML
- Background validation if XML drops a game

Notes:
- Stats are season-to-date on whatever the team has played
- "Opponent" column is everything opponents have done vs this team -> defense
- Number of games inferred from "Average Plays Per Game" = total_plays / N
"""
from __future__ import annotations
import re
import time
from dataclasses import dataclass, asdict, field
from typing import Optional

import requests
from bs4 import BeautifulSoup

from src.data.schedule import TEAMS, Team

UA = "UFLBot/0.1 (+https://github.com/reesesetzer-pro)"
TIMEOUT = 20

# Map row label -> field name. Add more as needed.
LABEL_MAP = {
    "Scoring": "points_scored",
    "Points Per Game": "ppg",
    "First Downs": "first_downs",
    "Rushing": "first_downs_rush",
    "Passing": "first_downs_pass",
    "Penalty": "first_downs_penalty",
    "Total Rushing Yards": "rush_yards",
    "Rushing Attempts": "rush_attempts",
    "Average Per Rush": "yards_per_rush",
    "Rushing Touchdowns": "rush_td",
    "Total Passing Yards": "pass_yards",
    "C-A-I": "pass_cai",                # comp-att-int
    "Average Per Attempt": "yards_per_pass_att",
    "Average Per Completion": "yards_per_completion",
    "Average Per Game": "pass_yards_per_game",
    "Passing Touchdowns": "pass_td",
    "Total Offensive Yards": "total_yards",
    "Total Offensive Plays": "total_plays",
    "Average Yards Per Play": "yards_per_play",
    "Average Plays Per Game": "plays_per_game",
    "Average Yards Per Game": "yards_per_game",
    "Kick Returns - Yards": "kr_yds",
    "Kick Return Average": "kr_avg",
    "Punt Returns - Yards": "pr_yds",
    "Punt Return Average": "pr_avg",
    "Interceptions - Return Yards": "int_ret",
    "Interception Return Average": "int_ret_avg",
    "Fumbles - Lost": "fumbles_lost",
    "Penalties - Yards": "penalties_yds",
    "Penalties Per Game": "penalties_per_game",
    "Penalty Yards Per Game": "penalty_yards_per_game",
    "Punts - Yards": "punts_yds",
    "Yards Per Punt": "yards_per_punt",
    "Kickoffs": "kickoffs",
    "Kickoffs Yards": "kickoff_yds",
    "Average Per Kickoff": "yards_per_kickoff",
    "3rd Down Conversions": "third_down_conv",
    "3rd Down Percentage": "third_down_pct",
    "4th Down Conversions": "fourth_down_conv",
    "4th Down Percentage": "fourth_down_pct",
    "Sacks By - Yards": "sacks_yds",
    "Misc. Yards": "misc_yds",
    "Touchdowns Scored": "tds_scored",
    "Field Goals Made - Att.": "fg_made_att",
    "Red Zone Scores": "rz_scores",
    "Red Zone Percentage": "rz_pct",
    "Alternative Kicks": "alt_kicks",
    "Alt. Kick Percentage": "alt_kick_pct",
    "1-point Conversions - Att": "one_pt_conv",
    "2-point Conversions - Att": "two_pt_conv",
    "3-point Conversions - Att": "three_pt_conv",
    "4-Point Field Goals Made - Att.": "four_pt_fg",
}


@dataclass
class TeamAggregates:
    """Season-to-date offense (team) and defense (opponent) split."""
    team_code: str
    season: int
    fetched_at: str
    games_played: Optional[int] = None
    offense: dict = field(default_factory=dict)   # raw label -> value
    defense: dict = field(default_factory=dict)
    parsed_offense: dict = field(default_factory=dict)  # field-mapped values
    parsed_defense: dict = field(default_factory=dict)


def fetch_team_stats_html(team: Team, session: Optional[requests.Session] = None) -> str:
    s = session or requests.Session()
    url = f"https://www.theufl.com/teams/{team.slug}/stats"
    r = s.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def _to_num(s: str):
    """Cast a cell to int/float when possible. Leaves strings alone otherwise."""
    if s is None:
        return None
    s = s.strip().replace(",", "").replace("%", "")
    if not s:
        return None
    try:
        if "." in s:
            return float(s)
        return int(s)
    except ValueError:
        return s  # e.g. "54 - 90 - 4"


def parse_team_stats(html: str, team_code: str, season: int = 2026) -> TeamAggregates:
    soup = BeautifulSoup(html, "html.parser")
    # The page has a single 3-col table (Stats | Team | Opponent) under "Team Stats"
    table = None
    for t in soup.find_all("table"):
        headers = [c.get_text(strip=True) for c in t.find_all("th")]
        if "Team" in headers and "Opponent" in headers and "Stats" in headers:
            table = t
            break
    if table is None:
        raise ValueError(f"Stats table not found for {team_code}")

    agg = TeamAggregates(
        team_code=team_code, season=season,
        fetched_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )
    for tr in table.find_all("tr"):
        cells = [c.get_text(strip=True) for c in tr.find_all("td")]
        if len(cells) < 3:
            continue
        label, team_val, opp_val = cells[0], cells[1], cells[2]
        agg.offense[label] = _to_num(team_val)
        agg.defense[label] = _to_num(opp_val)
        if label in LABEL_MAP:
            f = LABEL_MAP[label]
            agg.parsed_offense[f] = _to_num(team_val)
            agg.parsed_defense[f] = _to_num(opp_val)

    # Infer games played from total_plays / plays_per_game
    tp = agg.parsed_offense.get("total_plays")
    ppg = agg.parsed_offense.get("plays_per_game")
    if tp and ppg:
        try:
            agg.games_played = round(float(tp) / float(ppg))
        except Exception:
            pass
    return agg


def fetch_all_team_aggregates(season: int = 2026,
                              sleep_sec: float = 0.6
                              ) -> dict[str, TeamAggregates]:
    """One pass over the league. Returns {team_code: TeamAggregates}."""
    out: dict[str, TeamAggregates] = {}
    s = requests.Session()
    for code, team in TEAMS.items():
        try:
            html = fetch_team_stats_html(team, session=s)
            out[code] = parse_team_stats(html, code, season=season)
            time.sleep(sleep_sec)
        except Exception as e:
            print(f"[ERR] {code}: {e}")
    return out


# Per-game results URL pattern:
# https://www.theufl.com/teams/{slug}/stats/results
# Player aggregates:
# https://www.theufl.com/teams/{slug}/stats/individual

def fetch_team_results_html(team: Team, session: Optional[requests.Session] = None) -> str:
    s = session or requests.Session()
    url = f"https://www.theufl.com/teams/{team.slug}/stats/results"
    r = s.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def fetch_team_individual_html(team: Team, session: Optional[requests.Session] = None) -> str:
    s = session or requests.Session()
    url = f"https://www.theufl.com/teams/{team.slug}/stats/individual"
    r = s.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


if __name__ == "__main__":
    # Smoke test against a single team's stats page (DC Defenders)
    import json
    from src.data.schedule import TEAMS
    t = TEAMS["DC"]
    html = fetch_team_stats_html(t)
    agg = parse_team_stats(html, "DC")
    print(f"Games played: {agg.games_played}")
    print(f"PPG: {agg.parsed_offense.get('ppg')} (off) / "
          f"{agg.parsed_defense.get('ppg')} (def)")
    print(f"YPG: {agg.parsed_offense.get('yards_per_game')} (off) / "
          f"{agg.parsed_defense.get('yards_per_game')} (def)")
    print(f"3rd down: {agg.parsed_offense.get('third_down_pct')}% (off) / "
          f"{agg.parsed_defense.get('third_down_pct')}% (def)")
