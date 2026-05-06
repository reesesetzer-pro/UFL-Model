"""
Daily odds snapshot. Run 4x/day (cron):
    9am  ET — overnight movement
    12pm ET — pre-noon
    4pm  ET — afternoon line settle
    8pm  ET — game-day move check

Writes:
  - data/raw/odds/snapshot_{YYYYMMDD_HHMM}.json  (raw)
  - data/odds/flattened_{YYYYMMDD_HHMM}.csv      (one row per book/market/side)
  - odds_snapshots in Supabase (if env configured)
"""
from __future__ import annotations
import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ImportError:
    pass

from src.ingest.odds_api import (
    fetch_odds, fetch_scores, list_events, flatten_odds,
    APPROVED_BOOKS, SPORT_KEY,
)
UFL_SPORT_KEY = SPORT_KEY  # alias for clarity

RAW_DIR = Path("data/raw/odds")
FLAT_DIR = Path("data/odds")


def run_snapshot(api_key: str | None = None,
                 odds_format: str = "american",
                 markets: list[str] | None = None,
                 ) -> dict:
    api_key = api_key or os.getenv("ODDS_API_KEY")
    if not api_key:
        raise SystemExit("ODDS_API_KEY missing in env")
    markets = markets or ["h2h", "spreads", "totals"]
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    FLAT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")

    raw = fetch_odds(api_key=api_key,
                     bookmakers=list(APPROVED_BOOKS),
                     markets=markets,
                     odds_format=odds_format)

    raw_path = RAW_DIR / f"snapshot_{ts}.json"
    with raw_path.open("w") as f:
        json.dump(raw, f, indent=2, default=str)
    print(f"[odds] wrote {raw_path} ({len(raw)} events)")

    flat = flatten_odds(raw)
    flat_path = FLAT_DIR / f"flattened_{ts}.csv"
    if flat:
        keys = list(flat[0].keys())
        with flat_path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(flat)
        print(f"[odds] wrote {flat_path} ({len(flat)} rows)")
    else:
        print("[odds] no rows flattened (no upcoming games?)")

    # Supabase write — optional, only if SUPABASE_URL and SUPABASE_KEY set.
    # The flat CSV is wide (home_price / away_price / over_price / under_price);
    # the `odds_snapshots` schema is narrow (name + price per row). Reshape.
    try:
        from src.db.supabase_client import get_client
        c = get_client()
        if flat:
            snapshot_ts = datetime.now(timezone.utc).isoformat()
            narrow = []
            for r in flat:
                base = {
                    "snapshot_ts":   snapshot_ts,
                    "event_id":      r.get("game_id"),
                    "home_team":     r.get("home_team"),
                    "away_team":     r.get("away_team"),
                    "commence_time": r.get("commence_time"),
                    "market_key":    r.get("market"),
                    "book":          r.get("book"),
                    "last_update":   r.get("last_update"),
                    "point":         r.get("point"),
                }
                mk = r.get("market")
                if mk in ("h2h", "spreads"):
                    if r.get("home_price") is not None:
                        narrow.append({**base, "name": r.get("home_team"), "price": r.get("home_price")})
                    if r.get("away_price") is not None:
                        narrow.append({**base, "name": r.get("away_team"), "price": r.get("away_price")})
                elif mk == "totals":
                    if r.get("over_price") is not None:
                        narrow.append({**base, "name": "Over",  "price": r.get("over_price")})
                    if r.get("under_price") is not None:
                        narrow.append({**base, "name": "Under", "price": r.get("under_price")})
            if narrow:
                c.table("odds_snapshots").upsert(narrow, on_conflict="snapshot_ts,event_id,market_key,book,name").execute()
                print(f"[odds] supabase upserted {len(narrow)} rows")
    except Exception as e:
        print(f"[odds] supabase skipped: {e}")

    return {"timestamp": ts, "events": len(raw), "rows": len(flat)}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--api-key", default=None)
    p.add_argument("--markets", nargs="*",
                   default=["h2h", "spreads", "totals"])
    args = p.parse_args()
    run_snapshot(api_key=args.api_key, markets=args.markets)
