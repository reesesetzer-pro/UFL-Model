"""
Backfill 2026 — pull every completed game's StatBroadcast XML, parse, cache.

Usage
-----
    # Just fetch + parse + cache JSONs (no DB)
    python scripts/backfill_2026.py

    # Same, but also load to Supabase (requires SUPABASE_URL/SUPABASE_KEY env)
    python scripts/backfill_2026.py --load-supabase

    # Force refetch (skip cache)
    python scripts/backfill_2026.py --force

    # Limit and rate
    python scripts/backfill_2026.py --limit 5 --sleep 1.0
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

from src.data.schedule import SCHEDULE_2026, games_played_through
from src.ingest.statbroadcast import fetch_game_xml, parse_game


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="Max games to process (oldest first)")
    ap.add_argument("--force", action="store_true",
                    help="Skip cache, refetch XML")
    ap.add_argument("--sleep", type=float, default=0.5,
                    help="Seconds between requests (be polite)")
    ap.add_argument("--load-supabase", action="store_true",
                    help="Also write rows to Supabase via ETL")
    ap.add_argument("--as-of", type=str, default=None,
                    help="YYYY-MM-DD; only games on or before this date")
    args = ap.parse_args()

    as_of = (date.fromisoformat(args.as_of) if args.as_of else date.today())
    completed = games_played_through(as_of)
    if args.limit:
        completed = completed[:args.limit]

    raw_dir = Path("data/raw/statbroadcast")
    parsed_dir = Path("data/parsed")
    raw_dir.mkdir(parents=True, exist_ok=True)
    parsed_dir.mkdir(parents=True, exist_ok=True)

    print(f"[backfill] {len(completed)} games to pull (as_of={as_of}, force={args.force})")

    ok, skipped, failed = 0, 0, 0
    for slot in completed:
        target = parsed_dir / f"{slot.sb_id}.json"
        if target.exists() and not args.force:
            skipped += 1
            continue
        try:
            xml = fetch_game_xml(slot.sb_id, cache_dir=raw_dir, use_cache=not args.force)
            payload = parse_game(xml, slot.sb_id)
            # Identity check: StatBroadcast IDs are global across every league
            # they serve — a wrong/guessed sb_id fetches a real XML for some
            # OTHER sport, and stamping UFL teams onto it creates a chimera
            # that grades bets against the wrong game (bit us 2026-06-07).
            venue = payload.get("venue") or {}
            xml_home = str(venue.get("home_id") or "").strip().upper()
            xml_away = str(venue.get("vis_id") or "").strip().upper()
            if (xml_home, xml_away) != (slot.home, slot.away):
                failed += 1
                print(f"  ✗ {slot.sb_id} W{slot.week} {slot.away}@{slot.home}: XML is "
                      f"{xml_away}@{xml_home} — wrong event behind this ID, refusing to write")
                continue
            payload.setdefault("week", slot.week)
            payload.setdefault("home", slot.home)
            payload.setdefault("away", slot.away)
            payload.setdefault("game_date", slot.date.isoformat())
            with target.open("w") as f:
                json.dump(payload, f, default=str, indent=2)
            ok += 1
            print(f"  ✓ {slot.sb_id} W{slot.week} {slot.away}@{slot.home} ({slot.date})")
        except Exception as e:
            failed += 1
            print(f"  ✗ {slot.sb_id} W{slot.week} {slot.away}@{slot.home}: {e}")
        time.sleep(args.sleep)

    print(f"\n[backfill] ok={ok} skipped(cached)={skipped} failed={failed}")

    if args.load_supabase and ok + skipped:
        from src.db.load_to_supabase import load_all
        print("\n[backfill] loading to Supabase...")
        load_all(parsed_dir)


if __name__ == "__main__":
    main()
