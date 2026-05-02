"""
Weekly update pipeline.

Run on Mondays at ~10am ET (Mon morning is the lull between weekly slates).

Steps
-----
1. For every completed game in this season:
     - Pull StatBroadcast XML (with file cache)
     - Parse into a structured payload
     - Write parsed JSON to data/parsed/{sb_id}.json
     - Upsert team_game_stats, drives, plays, scoring_plays, players to Supabase
2. Recompute Elo history from scratch (deterministic, no drift)
3. Recompute opponent-adjusted PPD ratings
4. Write team_ratings snapshot to Supabase
5. Print a summary table for sanity

Idempotent — safe to run multiple times in the same week.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

# Make src.* imports work whether you run as a script or with python -m
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.data.schedule import (
    SCHEDULE_2026, games_played_through, schedule_by_id,
    all_team_codes, TEAMS,
)
from src.ingest.statbroadcast import (
    fetch_game_xml, parse_game,
)
from src.model.elo import (
    default_2026_starting_ratings, compute_elo_history, TeamElo,
)
from src.model.efficiency import (
    rollup_team_game, opponent_adjusted_ppd,
)

DATA_RAW = Path("data/raw/statbroadcast")
DATA_PARSED = Path("data/parsed")
RATINGS_OUT = Path("data/ratings")


def load_or_fetch_parsed(sb_id: int, force: bool = False) -> Optional[dict]:
    DATA_PARSED.mkdir(parents=True, exist_ok=True)
    DATA_RAW.mkdir(parents=True, exist_ok=True)
    parsed_path = DATA_PARSED / f"{sb_id}.json"
    if not force and parsed_path.exists():
        try:
            with parsed_path.open() as f:
                return json.load(f)
        except Exception:
            pass
    try:
        xml = fetch_game_xml(sb_id, cache_dir=DATA_RAW, use_cache=not force)
    except Exception as e:
        print(f"[warn] {sb_id}: fetch failed {e}")
        return None
    try:
        payload = parse_game(xml, sb_id)
    except Exception as e:
        print(f"[err] {sb_id}: parse failed {e}")
        return None
    # Stamp schedule metadata so downstream code (ETL, etc.) can read directly
    slot = schedule_by_id().get(int(sb_id))
    if slot is not None:
        payload.setdefault("week", slot.week)
        payload.setdefault("home", slot.home)
        payload.setdefault("away", slot.away)
        payload.setdefault("game_date", slot.date.isoformat())
    with parsed_path.open("w") as f:
        json.dump(payload, f, default=str, indent=2)
    return payload


def run_weekly_update(as_of: Optional[date] = None,
                      force_refetch: bool = False,
                      sleep_sec: float = 0.5) -> dict:
    as_of = as_of or date.today()
    print(f"[weekly] as_of={as_of}")
    completed = games_played_through(as_of)
    print(f"[weekly] {len(completed)} completed games")

    # 1. Ingest
    parsed_games: list[dict] = []
    for slot in completed:
        payload = load_or_fetch_parsed(slot.sb_id, force=force_refetch)
        if not payload:
            continue
        # Stamp game metadata that may not be in XML (week, scheduled date)
        payload.setdefault("week", slot.week)
        payload.setdefault("home", slot.home)
        payload.setdefault("away", slot.away)
        payload.setdefault("game_date", slot.date.isoformat())
        parsed_games.append(payload)
        time.sleep(sleep_sec)

    print(f"[weekly] {len(parsed_games)} games successfully parsed")

    # 2. Elo
    games_for_elo = []
    for p in parsed_games:
        ht = (p.get("home_totals") or {}).get("linescore_total")
        at = (p.get("away_totals") or {}).get("linescore_total")
        if ht is None or at is None:
            continue
        games_for_elo.append({
            "sb_id": p.get("game_id") or p.get("sb_id"),
            "week": p.get("week", 0),
            "home": p["home"], "away": p["away"],
            "home_score": int(ht), "away_score": int(at),
        })

    starting = default_2026_starting_ratings()
    elo_history = compute_elo_history(games_for_elo, starting)
    print(f"[weekly] Elo updated through {len(elo_history)} games")

    # 3. Opponent-adjusted PPD
    efficiencies = []
    for p in parsed_games:
        try:
            efficiencies.append(rollup_team_game(p, "home"))
            efficiencies.append(rollup_team_game(p, "away"))
        except Exception as e:
            print(f"[warn] efficiency rollup failed for {p.get('sb_id')}: {e}")
    ppd_adj = opponent_adjusted_ppd(efficiencies, all_team_codes())

    # 4. Snapshot
    RATINGS_OUT.mkdir(parents=True, exist_ok=True)
    snap = {
        "as_of": as_of.isoformat(),
        "n_games": len(games_for_elo),
        "elo": {code: round(t.rating, 1) for code, t in starting.items()},
        "elo_games_played": {code: t.games for code, t in starting.items()},
        "ppd_adj": {k: v for k, v in ppd_adj.items()},
    }
    out_path = RATINGS_OUT / f"snapshot_{as_of.isoformat()}.json"
    with out_path.open("w") as f:
        json.dump(snap, f, indent=2)

    # 5. Summary
    print()
    print("=== Team ratings snapshot ===")
    print(f"{'TEAM':<5}{'ELO':>8}{'GAMES':>7}{'OFF_PPD':>10}{'DEF_PPD':>10}")
    rows = []
    for code in sorted(starting):
        t = starting[code]
        a = ppd_adj.get(code, {"off": 0.0, "def": 0.0, "n": 0})
        rows.append((code, t.rating, t.games, a["off"], a["def"]))
    rows.sort(key=lambda r: -r[1])  # Elo desc
    for code, r, g, o, d in rows:
        print(f"{code:<5}{r:>8.1f}{g:>7d}{o:>+10.2f}{d:>+10.2f}")
    return snap


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--as-of", type=str, help="YYYY-MM-DD; defaults to today")
    p.add_argument("--force-refetch", action="store_true")
    p.add_argument("--sleep", type=float, default=0.5)
    args = p.parse_args()
    as_of = (date.fromisoformat(args.as_of) if args.as_of else None)
    run_weekly_update(as_of=as_of, force_refetch=args.force_refetch,
                      sleep_sec=args.sleep)
