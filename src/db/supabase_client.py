"""Supabase client wrapper for UFL Model."""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

from supabase import create_client, Client


@lru_cache(maxsize=1)
def get_client(
    url: Optional[str] = None,
    key: Optional[str] = None,
) -> Client:
    """Return a cached Supabase client. Reads SUPABASE_URL / SUPABASE_KEY from env."""
    url = url or os.getenv("SUPABASE_URL")
    key = key or os.getenv("SUPABASE_KEY")
    if not (url and key):
        raise ValueError("Missing SUPABASE_URL or SUPABASE_KEY")
    return create_client(url, key)


def upsert_games(rows: list[dict]) -> None:
    if not rows:
        return
    get_client().table("games").upsert(rows, on_conflict="game_id").execute()


def upsert_team_game_stats(rows: list[dict]) -> None:
    if not rows:
        return
    get_client().table("team_game_stats").upsert(
        rows, on_conflict="game_id,team"
    ).execute()


def insert_drives(rows: list[dict]) -> None:
    if not rows:
        return
    get_client().table("drives").insert(rows).execute()


def insert_odds_snapshots(rows: list[dict]) -> None:
    if not rows:
        return
    get_client().table("odds_snapshots").insert(rows).execute()


def insert_predictions(rows: list[dict]) -> None:
    if not rows:
        return
    get_client().table("predictions").insert(rows).execute()


def insert_edge_log(rows: list[dict]) -> None:
    if not rows:
        return
    get_client().table("edge_log").insert(rows).execute()
