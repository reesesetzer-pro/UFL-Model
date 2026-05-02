"""
UFL 2026 master schedule + team metadata.

Single source of truth for game IDs (StatBroadcast) and team/venue info.
Pulled from theUFL.com/ufl-live-stats-media + foxsports.com schedule article.

Date verified: May 1, 2026.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import date, datetime
from typing import Optional


# ---------- TEAMS ---------------------------------------------------------

@dataclass(frozen=True)
class Team:
    code: str            # 3-letter
    slug: str            # theufl.com URL slug
    full_name: str
    city: str
    stadium: str
    indoor: bool
    head_coach: str
    is_expansion_2026: bool = False  # New franchises Jan 2026
    is_rebrand_2026: bool = False    # Rebranded from prior franchise
    elo_seed: float = 1500.0         # Starting Elo (will be adjusted in model)


TEAMS: dict[str, Team] = {
    "BHM": Team("BHM", "birmingham", "Birmingham Stallions", "Birmingham, AL",
                "Protective Stadium", False, "A.J. McCarron"),
    "CLB": Team("CLB", "columbus",  "Columbus Aviators",   "Columbus, OH",
                "Historic Crew Stadium", False, "Ted Ginn Jr.",
                is_expansion_2026=True),
    "DAL": Team("DAL", "dallas",    "Dallas Renegades",    "Frisco, TX",
                "Toyota Stadium", False, "TBD",
                is_rebrand_2026=True),  # was Arlington Renegades
    "DC":  Team("DC",  "dc",        "DC Defenders",        "Washington, DC",
                "Audi Field", False, "Shannon Harris"),
    "HOU": Team("HOU", "houston",   "Houston Gamblers",    "Houston, TX",
                "Shell Energy Stadium", False, "TBD",
                is_rebrand_2026=True),  # was Houston Roughnecks
    "LOU": Team("LOU", "louisville","Louisville Kings",    "Louisville, KY",
                "Lynn Family Stadium", False, "TBD",
                is_expansion_2026=True),
    "ORL": Team("ORL", "orlando",   "Orlando Storm",       "Orlando, FL",
                "Inter&Co Stadium", False, "Anthony Becht",
                is_expansion_2026=True),
    "STL": Team("STL", "st-louis",  "St. Louis Battlehawks","St. Louis, MO",
                "America's Center Dome", True, "Ricky Proehl"),
}

# Helpful aliases for matching strings out of feeds
TEAM_ALIASES: dict[str, str] = {
    "Birmingham Stallions": "BHM", "Stallions": "BHM", "BHM": "BHM",
    "Columbus Aviators": "CLB", "Aviators": "CLB", "CLB": "CLB",
    "Dallas Renegades": "DAL", "Renegades": "DAL", "DAL": "DAL",
    "Arlington Renegades": "DAL",  # legacy
    "DC Defenders": "DC", "Defenders": "DC",
    "Houston Gamblers": "HOU", "Gamblers": "HOU", "HOU": "HOU",
    "Houston Roughnecks": "HOU",  # legacy
    "Louisville Kings": "LOU", "Kings": "LOU", "LOU": "LOU",
    "Orlando Storm": "ORL", "Storm": "ORL", "ORL": "ORL",
    "St. Louis Battlehawks": "STL", "Battlehawks": "STL", "STL": "STL",
}


def to_code(name: str) -> Optional[str]:
    """Map a team name fragment to a canonical 3-letter code."""
    if not name:
        return None
    for k, v in TEAM_ALIASES.items():
        if k.lower() in name.lower():
            return v
    return None


# ---------- 2026 SCHEDULE -------------------------------------------------

@dataclass(frozen=True)
class GameSlot:
    sb_id: int           # StatBroadcast game ID
    week: int
    date: date           # game date (US/Eastern)
    home: str            # 3-letter
    away: str
    network: str = ""
    sb_url: str = ""

    def __post_init__(self):
        if not self.sb_url:
            object.__setattr__(self, "sb_url",
                               f"http://archive.statbroadcast.com/{self.sb_id}.xml")

    def matchup(self) -> str:
        return f"{self.away}@{self.home}"


# 40 regular-season games. IDs 656640-656679 sequential.
# Fox Sports schedule article + theufl.com/ufl-live-stats-media mapping.
SCHEDULE_2026: list[GameSlot] = [
    # Week 1
    GameSlot(656640, 1, date(2026, 3, 27), "LOU", "BHM", "FOX"),
    GameSlot(656641, 1, date(2026, 3, 28), "STL", "DC",  "FOX"),
    GameSlot(656642, 1, date(2026, 3, 28), "DAL", "HOU", "FS1"),
    GameSlot(656643, 1, date(2026, 3, 29), "ORL", "CLB", "ABC"),
    # Week 2
    GameSlot(656644, 2, date(2026, 4, 3),  "CLB", "DC",  "FOX"),
    GameSlot(656645, 2, date(2026, 4, 4),  "ORL", "LOU", "FOX"),
    GameSlot(656646, 2, date(2026, 4, 5),  "HOU", "BHM", "FS1"),
    GameSlot(656647, 2, date(2026, 4, 7),  "DAL", "STL", "ESPN"),
    # Week 3
    GameSlot(656648, 3, date(2026, 4, 10), "LOU", "ORL", "FOX"),
    GameSlot(656649, 3, date(2026, 4, 11), "DC",  "HOU", "FOX"),
    GameSlot(656650, 3, date(2026, 4, 12), "DAL", "CLB", "ABC"),
    GameSlot(656651, 3, date(2026, 4, 12), "STL", "BHM", "FS1"),
    # Week 4
    GameSlot(656652, 4, date(2026, 4, 16), "HOU", "LOU", "FOX"),
    GameSlot(656653, 4, date(2026, 4, 17), "CLB", "DAL", "FS1"),
    GameSlot(656654, 4, date(2026, 4, 18), "DC",  "STL", "ABC"),
    GameSlot(656655, 4, date(2026, 4, 18), "BHM", "ORL", "FOX"),
    # Week 5
    GameSlot(656656, 5, date(2026, 4, 24), "BHM", "DC",  "FOX"),
    GameSlot(656657, 5, date(2026, 4, 25), "ORL", "STL", "FOX"),
    GameSlot(656658, 5, date(2026, 4, 26), "HOU", "CLB", "FS1"),
    GameSlot(656659, 5, date(2026, 4, 26), "DAL", "LOU", "FOX"),
    # Week 6
    GameSlot(656660, 6, date(2026, 4, 30), "LOU", "STL", "FS1"),
    GameSlot(656661, 6, date(2026, 5, 1),  "CLB", "HOU", "FOX"),
    GameSlot(656662, 6, date(2026, 5, 2),  "DC",  "DAL", "ABC"),
    GameSlot(656663, 6, date(2026, 5, 3),  "ORL", "BHM", "FOX"),
    # Week 7
    GameSlot(656664, 7, date(2026, 5, 8),  "STL", "CLB", "FOX"),
    GameSlot(656665, 7, date(2026, 5, 9),  "DC",  "LOU", "FOX"),
    GameSlot(656666, 7, date(2026, 5, 9),  "BHM", "DAL", "ESPN"),
    GameSlot(656667, 7, date(2026, 5, 10), "HOU", "ORL", "FS1"),
    # Week 8
    GameSlot(656668, 8, date(2026, 5, 15), "DAL", "ORL", "FOX"),
    GameSlot(656669, 8, date(2026, 5, 16), "LOU", "DC",  "ABC"),
    GameSlot(656670, 8, date(2026, 5, 16), "STL", "HOU", "ABC"),
    GameSlot(656671, 8, date(2026, 5, 17), "BHM", "CLB", "FOX"),
    # Week 9
    GameSlot(656672, 9, date(2026, 5, 22), "ORL", "DC",  "FOX"),
    GameSlot(656673, 9, date(2026, 5, 23), "CLB", "BHM", "ABC"),
    GameSlot(656674, 9, date(2026, 5, 24), "LOU", "DAL", "FOX"),
    GameSlot(656675, 9, date(2026, 5, 24), "HOU", "STL", "ESPN2"),
    # Week 10
    GameSlot(656676, 10, date(2026, 5, 29), "STL", "DAL", "FOX"),
    GameSlot(656677, 10, date(2026, 5, 30), "BHM", "HOU", "ESPN2"),
    GameSlot(656678, 10, date(2026, 5, 31), "DC",  "ORL", "ABC"),
    GameSlot(656679, 10, date(2026, 5, 31), "CLB", "LOU", "FOX"),
]


# ---------- LOOKUPS -------------------------------------------------------

def schedule_by_id() -> dict[int, GameSlot]:
    return {g.sb_id: g for g in SCHEDULE_2026}


def games_by_week(week: int) -> list[GameSlot]:
    return [g for g in SCHEDULE_2026 if g.week == week]


def games_played_through(d: date) -> list[GameSlot]:
    return [g for g in SCHEDULE_2026 if g.date <= d]


def upcoming_games(d: date, days_ahead: int = 7) -> list[GameSlot]:
    """Games scheduled today through `days_ahead` days from now (inclusive on
    both ends — today's games still count as upcoming until kickoff)."""
    from datetime import timedelta
    return [g for g in SCHEDULE_2026
            if d <= g.date <= d + timedelta(days=days_ahead)]


def team(code: str) -> Team:
    return TEAMS[code]


def all_team_codes() -> list[str]:
    return list(TEAMS.keys())


# ---------- KEY DATES -----------------------------------------------------

SEASON_START = date(2026, 3, 27)
SEASON_END = date(2026, 5, 31)
PLAYOFFS_START = date(2026, 6, 7)
CHAMPIONSHIP = date(2026, 6, 13)
CHAMPIONSHIP_VENUE = "Audi Field, Washington DC (DC Defenders host)"

# Playoff format: top 4 single-table → semis → final at Audi Field on ABC


# ---------- 2026 RULE PACKAGE (for narrative + projector inflation) ------

RULE_CHANGES_2026 = {
    "four_point_fg": "60+ yard FG worth 4 pts",
    "no_punt_inside_opp_50": "Punting forbidden inside opponent territory",
    "tush_push_banned": True,
    "one_foot_catch": "1-foot inbounds catch reinstated (was 2-foot in 2025)",
    "pat_options": "1-pt (kick from 15), 2-pt (5-yd line), 3-pt (10-yd line)",
    "alt_kickoff": "Onside-replacement 4th-and-12 from own 25",
    "ot_format": "Best-of-3 alternating possessions from 5-yd line",
    "play_clock": 30,
    "kickoff_spot": 20,
    "touchback_spot": 25,
}


if __name__ == "__main__":
    # Quick sanity check
    print(f"Total games: {len(SCHEDULE_2026)}")
    print(f"Teams: {len(TEAMS)}")
    print(f"Sample game: {SCHEDULE_2026[0]}")
    by_id = schedule_by_id()
    assert len(by_id) == 40, "Expected 40 unique sb_ids"
    print(f"Game ID range: {min(by_id)} - {max(by_id)}")
