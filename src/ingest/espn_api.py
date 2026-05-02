"""ESPN public API client for UFL.

Endpoints
---------
Scoreboard (date or range, YYYYMMDD):
    https://site.api.espn.com/apis/site/v2/sports/football/ufl/scoreboard?dates=YYYYMMDD-YYYYMMDD

Summary (full game package — boxscore, drives, by-quarter scores, odds):
    https://site.api.espn.com/apis/site/v2/sports/football/ufl/summary?event={gameId}

ESPN's API is undocumented and unstable. All parsers are defensive: missing
keys return None / empty list rather than raising.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import requests

BASE = "https://site.api.espn.com/apis/site/v2/sports/football/ufl"
SCOREBOARD_URL = f"{BASE}/scoreboard"
SUMMARY_URL = f"{BASE}/summary"

DEFAULT_TIMEOUT = 15
DEFAULT_HEADERS = {
    "User-Agent": "UFLModel/0.1",
    "Accept": "application/json",
}


# ---------------------------------------------------------------------------
# Low-level fetchers
# ---------------------------------------------------------------------------
def _fmt_date(d: date | datetime | str) -> str:
    if isinstance(d, str):
        return d.replace("-", "")
    return d.strftime("%Y%m%d")


def fetch_scoreboard(
    start: date | datetime | str,
    end: Optional[date | datetime | str] = None,
    cache_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """GET /scoreboard for a date or date range."""
    s = _fmt_date(start)
    date_param = f"{s}-{_fmt_date(end)}" if end is not None else s
    params = {"dates": date_param, "limit": 100}

    r = requests.get(
        SCOREBOARD_URL, params=params,
        headers=DEFAULT_HEADERS, timeout=DEFAULT_TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()

    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / f"scoreboard_{date_param}.json").write_text(
            json.dumps(data, indent=2)
        )
    return data


def fetch_summary(
    game_id: str,
    cache_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """GET /summary?event={id} — full game package."""
    r = requests.get(
        SUMMARY_URL, params={"event": game_id},
        headers=DEFAULT_HEADERS, timeout=DEFAULT_TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()

    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / f"summary_{game_id}.json").write_text(json.dumps(data, indent=2))
    return data


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------
def parse_scoreboard_events(scoreboard: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten scoreboard.events into a simple list."""
    out: list[dict[str, Any]] = []
    for e in scoreboard.get("events", []) or []:
        comps = e.get("competitions") or []
        if not comps:
            continue
        comp = comps[0]
        teams = comp.get("competitors") or []
        home = next((t for t in teams if t.get("homeAway") == "home"), {}) or {}
        away = next((t for t in teams if t.get("homeAway") == "away"), {}) or {}

        def _score(c: dict) -> Optional[int]:
            v = c.get("score")
            if v is None or v == "":
                return None
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        out.append({
            "event_id": e.get("id"),
            "date": e.get("date"),
            "name": e.get("name"),
            "short_name": e.get("shortName"),
            "status": ((e.get("status") or {}).get("type") or {}).get("name"),
            "completed": ((e.get("status") or {}).get("type") or {}).get("completed"),
            "week": (e.get("week") or {}).get("number"),
            "season": (e.get("season") or {}).get("year"),
            "home_team": (home.get("team") or {}).get("displayName"),
            "home_abbr": (home.get("team") or {}).get("abbreviation"),
            "home_score": _score(home),
            "away_team": (away.get("team") or {}).get("displayName"),
            "away_abbr": (away.get("team") or {}).get("abbreviation"),
            "away_score": _score(away),
            "venue": (comp.get("venue") or {}).get("fullName"),
            "neutral_site": comp.get("neutralSite", False),
        })
    return out


def _linescores(competitor: dict[str, Any], n_periods: int = 5) -> list[int]:
    """Pad linescores out to q1..q4 + ot. Returns ints."""
    raw = competitor.get("linescores") or []
    out: list[int] = []
    for x in raw:
        v = x.get("displayValue") if isinstance(x, dict) else None
        if v is None and isinstance(x, dict):
            v = x.get("value")
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            out.append(0)
    out += [0] * (n_periods - len(out))
    return out[:n_periods]


def _parse_drives_block(drives_block: Any) -> list[dict[str, Any]]:
    """Drives can come as {previous: [...], current: {...}} or just a list."""
    if drives_block is None:
        return []
    if isinstance(drives_block, list):
        return drives_block
    if isinstance(drives_block, dict):
        out: list[dict[str, Any]] = []
        prev = drives_block.get("previous")
        if isinstance(prev, list):
            out.extend(prev)
        elif isinstance(prev, dict):
            out.append(prev)
        cur = drives_block.get("current")
        if isinstance(cur, dict):
            out.append(cur)
        elif isinstance(cur, list):
            out.extend(cur)
        return out
    return []


def parse_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Extract the fields the model needs from a /summary payload."""
    header = summary.get("header") or {}
    competitions = header.get("competitions") or []
    comp = competitions[0] if competitions else {}
    competitors = comp.get("competitors") or []
    home = next((c for c in competitors if c.get("homeAway") == "home"), {}) or {}
    away = next((c for c in competitors if c.get("homeAway") == "away"), {}) or {}

    def _score(c: dict) -> int:
        try:
            return int(c.get("score") or 0)
        except (TypeError, ValueError):
            return 0

    home_ls = _linescores(home)
    away_ls = _linescores(away)

    drives = _parse_drives_block(summary.get("drives"))

    boxscore = summary.get("boxscore") or {}
    team_stats: list[dict[str, Any]] = []
    for t in (boxscore.get("teams") or []):
        team_info = t.get("team") or {}
        stats = {
            (s.get("name") or s.get("label")): s.get("displayValue")
            for s in (t.get("statistics") or [])
            if isinstance(s, dict)
        }
        team_stats.append({
            "team": team_info.get("displayName"),
            "abbr": team_info.get("abbreviation"),
            "stats": stats,
        })

    pickcenter = summary.get("pickcenter") or []

    return {
        "event_id": header.get("id") or summary.get("gameId"),
        "status": ((header.get("status") or {}).get("type") or {}).get("name"),
        "season": (header.get("season") or {}).get("year"),
        "week": (header.get("week")),
        "home_team": (home.get("team") or {}).get("displayName"),
        "home_abbr": (home.get("team") or {}).get("abbreviation"),
        "home_score": _score(home),
        "home_linescores": home_ls,
        "away_team": (away.get("team") or {}).get("displayName"),
        "away_abbr": (away.get("team") or {}).get("abbreviation"),
        "away_score": _score(away),
        "away_linescores": away_ls,
        "drive_count": len(drives),
        "drives": drives,
        "team_stats": team_stats,
        "pickcenter": pickcenter,
        "venue": (comp.get("venue") or {}).get("fullName") if comp else None,
    }


# ---------------------------------------------------------------------------
# Convenience: pull a date range with summaries
# ---------------------------------------------------------------------------
def fetch_week_summaries(
    start: date | datetime | str,
    end: date | datetime | str,
    cache_dir: Optional[Path] = None,
    sleep_between: float = 0.5,
) -> list[dict[str, Any]]:
    """Pull scoreboard + per-game summary for a date range. Returns parsed."""
    sb = fetch_scoreboard(start, end, cache_dir=cache_dir)
    events = parse_scoreboard_events(sb)
    out: list[dict[str, Any]] = []
    for ev in events:
        eid = ev["event_id"]
        if not eid:
            continue
        try:
            summary = fetch_summary(eid, cache_dir=cache_dir)
            parsed = parse_summary(summary)
            parsed["scoreboard_event"] = ev
            out.append(parsed)
        except Exception as exc:
            print(f"  ! summary failed for {eid}: {exc}")
        time.sleep(sleep_between)
    return out
