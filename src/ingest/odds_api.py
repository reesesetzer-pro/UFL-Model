"""The Odds API client — UFL coverage.

UFL gets featured markets only: h2h, spreads, totals.
1H lines and team totals are derived from the model, not pulled from the API.

Docs: https://the-odds-api.com/sports/ufl-odds.html
"""
from __future__ import annotations

import os
from typing import Any, Iterable, Optional

import requests

BASE = "https://api.the-odds-api.com/v4"
SPORT_KEY = "americanfootball_ufl"
DEFAULT_TIMEOUT = 15

# Approved books across Reese's models. The Odds API book keys:
#   - draftkings, fanduel, betmgm, williamhill_us (Caesars), bet365, thescore, hardrockbet
APPROVED_BOOKS = (
    "draftkings",
    "fanduel",
    "betmgm",
    "williamhill_us",
    "bet365",
    "thescore",
    "hardrockbet",
)


def _api_key(api_key: Optional[str]) -> str:
    key = api_key or os.getenv("ODDS_API_KEY")
    if not key:
        raise ValueError("Missing ODDS_API_KEY (env var or arg)")
    return key


def fetch_odds(
    api_key: Optional[str] = None,
    markets: Iterable[str] = ("h2h", "spreads", "totals"),
    regions: str = "us",
    bookmakers: Optional[Iterable[str]] = APPROVED_BOOKS,
    odds_format: str = "american",
) -> list[dict[str, Any]]:
    """Pull current UFL odds for upcoming + live games."""
    params: dict[str, Any] = {
        "apiKey": _api_key(api_key),
        "regions": regions,
        "markets": ",".join(markets),
        "oddsFormat": odds_format,
    }
    if bookmakers:
        params["bookmakers"] = ",".join(bookmakers)

    r = requests.get(
        f"{BASE}/sports/{SPORT_KEY}/odds",
        params=params, timeout=DEFAULT_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def fetch_scores(
    api_key: Optional[str] = None,
    days_from: int = 3,
) -> list[dict[str, Any]]:
    """Final scores for completed UFL games up to `days_from` days ago (max 3)."""
    r = requests.get(
        f"{BASE}/sports/{SPORT_KEY}/scores",
        params={"apiKey": _api_key(api_key), "daysFrom": days_from},
        timeout=DEFAULT_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def list_events(
    api_key: Optional[str] = None,
    date_format: str = "iso",
) -> list[dict[str, Any]]:
    """List upcoming/live UFL events (no quota cost)."""
    r = requests.get(
        f"{BASE}/sports/{SPORT_KEY}/events",
        params={"apiKey": _api_key(api_key), "dateFormat": date_format},
        timeout=DEFAULT_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def flatten_odds(odds_payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten /odds response into rows: one per game/book/market."""
    rows: list[dict[str, Any]] = []
    for game in odds_payload:
        gid = game.get("id")
        home = game.get("home_team")
        away = game.get("away_team")
        commence = game.get("commence_time")
        for book in game.get("bookmakers", []) or []:
            bk = book.get("key")
            updated = book.get("last_update")
            for mk in book.get("markets", []) or []:
                market = mk.get("key")
                outcomes = mk.get("outcomes", []) or []
                row: dict[str, Any] = {
                    "game_id": gid,
                    "home_team": home,
                    "away_team": away,
                    "commence_time": commence,
                    "book": bk,
                    "market": market,
                    "last_update": updated,
                    "home_price": None,
                    "away_price": None,
                    "over_price": None,
                    "under_price": None,
                    "point": None,
                }
                if market == "h2h":
                    row["home_price"] = next(
                        (o.get("price") for o in outcomes if o.get("name") == home), None
                    )
                    row["away_price"] = next(
                        (o.get("price") for o in outcomes if o.get("name") == away), None
                    )
                elif market == "spreads":
                    h = next((o for o in outcomes if o.get("name") == home), {}) or {}
                    a = next((o for o in outcomes if o.get("name") == away), {}) or {}
                    row["home_price"] = h.get("price")
                    row["away_price"] = a.get("price")
                    row["point"] = h.get("point")  # home spread; away = -point
                elif market == "totals":
                    over = next((o for o in outcomes if o.get("name") == "Over"), {}) or {}
                    under = next((o for o in outcomes if o.get("name") == "Under"), {}) or {}
                    row["over_price"] = over.get("price")
                    row["under_price"] = under.get("price")
                    row["point"] = over.get("point")
                rows.append(row)
    return rows
