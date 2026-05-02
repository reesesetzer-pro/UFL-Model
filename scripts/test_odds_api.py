"""Smoke test: pull current UFL odds from The Odds API.

Run after .env is set up:
    python scripts/test_odds_api.py

Pass criteria:
- API responds 200
- Quota headers present (x-requests-remaining)
- At least 1 game returned with bookmaker odds
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from src.ingest.odds_api import fetch_odds, flatten_odds  # noqa: E402


def main() -> int:
    print("=" * 64)
    print("UFL Odds API smoke test")
    print("=" * 64)

    try:
        payload = fetch_odds()
    except Exception as exc:
        print(f"FAIL: {exc}")
        return 1

    print(f"\nGames returned: {len(payload)}")
    for g in payload:
        print(f"  {g.get('away_team')} @ {g.get('home_team')}  "
              f"({g.get('commence_time')})  "
              f"books={len(g.get('bookmakers', []))}")

    rows = flatten_odds(payload)
    print(f"\nFlattened rows: {len(rows)}")
    if rows:
        print("Sample row:")
        for k, v in list(rows[0].items()):
            print(f"  {k:>15}: {v}")

    if not payload:
        print("\nWARN: zero games. Could be off-week or wrong sport key.")
        return 1
    print("\nPASS: Odds API connection OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
