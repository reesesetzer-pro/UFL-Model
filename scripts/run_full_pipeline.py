"""
Run the full Monday pipeline:
  1. Backfill any new completed games from StatBroadcast
  2. Pull team season aggregates from theUFL.com (validation snapshot)
  3. Recompute Elo + opponent-adjusted PPD ratings
  4. Pull a fresh odds snapshot from The Odds API
  5. Generate the upcoming-week slate with edges + Kelly stakes
  6. (Optional) push everything to Supabase

Usage:
    python scripts/run_full_pipeline.py
    python scripts/run_full_pipeline.py --skip-odds       # if API quota tight
    python scripts/run_full_pipeline.py --to-supabase
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.schedule import games_played_through, upcoming_games


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-backfill", action="store_true")
    ap.add_argument("--skip-aggregates", action="store_true")
    ap.add_argument("--skip-ratings", action="store_true")
    ap.add_argument("--skip-odds", action="store_true")
    ap.add_argument("--skip-slate", action="store_true")
    ap.add_argument("--to-supabase", action="store_true")
    ap.add_argument("--bankroll", type=float, default=1000.0)
    ap.add_argument("--days-ahead", type=int, default=4)
    args = ap.parse_args()

    today = date.today()
    print(f"\n========== UFL MODEL — full pipeline {today} ==========\n")
    completed = games_played_through(today)
    upcoming = upcoming_games(today, days_ahead=args.days_ahead)
    print(f"  completed: {len(completed)} games")
    print(f"  upcoming (next {args.days_ahead}d): {len(upcoming)} games\n")

    # 1. BACKFILL ----------------------------------------------------------
    if not args.skip_backfill:
        print("--- 1. BACKFILL ---")
        from scripts import backfill_2026
        sys.argv = ["backfill_2026.py"]
        if args.to_supabase:
            sys.argv.append("--load-supabase")
        backfill_2026.main()
        print()

    # 2. AGGREGATES (theUFL.com) ------------------------------------------
    if not args.skip_aggregates:
        print("--- 2. THEUFL AGGREGATES ---")
        try:
            from src.ingest.theufl_aggregates import fetch_all_team_aggregates
            data = fetch_all_team_aggregates(season=2026)
            out_dir = Path("data/aggregates"); out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"theufl_{today.isoformat()}.json"
            serial = {code: {
                "team_code": agg.team_code,
                "season": agg.season,
                "fetched_at": agg.fetched_at,
                "games_played": agg.games_played,
                "offense_raw": agg.offense,
                "defense_raw": agg.defense,
                "parsed_offense": agg.parsed_offense,
                "parsed_defense": agg.parsed_defense,
            } for code, agg in data.items()}
            with out_path.open("w") as f:
                json.dump(serial, f, indent=2)
            print(f"  wrote {out_path} ({len(data)} teams)")
        except Exception as e:
            print(f"  [warn] aggregate fetch failed: {e}")
        print()

    # 3. RATINGS ----------------------------------------------------------
    if not args.skip_ratings:
        print("--- 3. RATINGS UPDATE ---")
        from src.pipeline.weekly_update import run_weekly_update
        run_weekly_update(as_of=today)
        print()

    # 4. ODDS -------------------------------------------------------------
    if not args.skip_odds:
        print("--- 4. ODDS SNAPSHOT ---")
        try:
            from src.pipeline.daily_odds_snap import run_snapshot
            run_snapshot()
        except SystemExit as e:
            print(f"  [warn] {e}")
        except Exception as e:
            print(f"  [warn] odds snapshot failed: {e}")
        print()

    # 5. SLATE ------------------------------------------------------------
    if not args.skip_slate and len(upcoming) > 0:
        print("--- 5. SLATE GENERATION ---")
        from src.pipeline.prediction_run import build_slate
        build_slate(as_of=today, days_ahead=args.days_ahead, bankroll=args.bankroll)
        print()

    print("\n========== DONE ==========")
    print(f"To launch dashboard: streamlit run streamlit_app.py")


if __name__ == "__main__":
    main()
