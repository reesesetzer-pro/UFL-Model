"""Smoke test for StatBroadcast UFL ingestion.

Verifies:
  1. Hub page returns server-rendered game IDs
  2. Per-game XML loads and parses cleanly
  3. All major data categories are present: venue, rules, officials,
     team totals, drives, plays, players, scoring plays, FG attempts

Run from project root:
    python scripts/test_statbroadcast.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ingest.statbroadcast import (  # noqa: E402
    fetch_hub_html, parse_game_ids,
    fetch_game_xml, parse_game,
)

CACHE = ROOT / "data" / "raw" / "statbroadcast"


def main() -> int:
    print("=" * 64)
    print("StatBroadcast UFL ingestion smoke test")
    print(f"Cache: {CACHE}")
    print("=" * 64)

    # ---- Step 1: discover game IDs from hub
    try:
        hub = fetch_hub_html()
    except Exception as exc:
        print(f"FAIL: hub fetch failed: {exc}")
        return 1
    ids = parse_game_ids(hub)
    print(f"\nDiscovered {len(ids)} game IDs in hub: {ids}")
    if not ids:
        print("FAIL: no game IDs found.")
        return 1

    # ---- Step 2: pull XML for the most recent game
    target = max(ids)
    print(f"\nFetching XML for most-recent game: {target}")
    try:
        xml_text = fetch_game_xml(target, cache_dir=CACHE)
    except Exception as exc:
        print(f"FAIL: XML fetch failed for {target}: {exc}")
        return 1
    print(f"  XML size: {len(xml_text):,} bytes")

    # ---- Step 3: parse
    try:
        game = parse_game(xml_text, game_id=target)
    except Exception as exc:
        print(f"FAIL: parse error: {exc}")
        return 1

    # ---- Step 4: validate every category
    v = game["venue"]
    print(f"\nVenue:")
    print(f"  {v.get('vis_name')} @ {v.get('home_name')}")
    print(f"  {v.get('date')} {v.get('start_time')}-{v.get('end_time')}  duration={v.get('duration')}")
    print(f"  {v.get('stadium')} ({v.get('location')})  attendance={v.get('attendance')}")
    print(f"  weather: temp={v.get('temp_f')}°F wind={v.get('wind')!r} cond={v.get('weather')!r}")
    print(f"  season={v.get('season')} week={v.get('week')} league={v.get('league')}")

    rules = game["rules"]
    print(f"\nRules:")
    print(f"  {rules.get('quarters')}q × {rules.get('minutes_per')}min, "
          f"FG={rules.get('fg_points')}pt FG60+={rules.get('fg4_points')}pt "
          f"PAT options={rules.get('pat_options')}")

    officials = game["officials"]
    print(f"\nOfficials: {len(officials)} crew members")
    for o in officials[:3]:
        print(f"  {o['role']:>6}  {o['name']} (#{o['uniform']})")

    home = game["home_totals"]
    away = game["away_totals"]
    print(f"\nTeam totals:")
    print(f"  home {home.get('team_abbr'):>3} {home.get('team_name')!s:<25} "
          f"plays={home.get('plays')} yds={home.get('total_yards')} "
          f"score={home.get('linescore_total')} TOP={home.get('top_text')}")
    print(f"  away {away.get('team_abbr'):>3} {away.get('team_name')!s:<25} "
          f"plays={away.get('plays')} yds={away.get('total_yards')} "
          f"score={away.get('linescore_total')} TOP={away.get('top_text')}")
    print(f"  home: 3rd-down {home.get('third_down_conv')}/{home.get('third_down_att')}, "
          f"RZ {home.get('redzone_scores')}/{home.get('redzone_att')}, "
          f"TOs {home.get('fumbles_lost')}fum + {home.get('pass_int')}int")
    print(f"  away: 3rd-down {away.get('third_down_conv')}/{away.get('third_down_att')}, "
          f"RZ {away.get('redzone_scores')}/{away.get('redzone_att')}, "
          f"TOs {away.get('fumbles_lost')}fum + {away.get('pass_int')}int")

    drives = game["drives"]
    print(f"\nDrives: {len(drives)} total")
    for d in drives[:3]:
        print(f"  D{d['drive_index']:>2} {d['team_abbr']:>3} "
              f"start={d['start_how']:<5} {d['start_spot']:<7} → "
              f"end={d['end_how']:<6} {d['end_spot']:<7}  "
              f"plays={d['plays']} yds={d['yards']} top={d['top_text']}")

    plays = game["plays"]
    print(f"\nPlays: {len(plays)} total")
    pass_plays = [p for p in plays if p.get("pass_qb")]
    rush_plays = [p for p in plays if p.get("rush_runner")]
    print(f"  passes={len(pass_plays)}  rushes={len(rush_plays)}  "
          f"with_air_yds={sum(1 for p in plays if p.get('pass_air_yds') is not None)}")
    for p in plays[:2]:
        ds = f"{p.get('down')}/{p.get('togo')}@{p.get('spot')}"
        print(f"  Q{p.get('quarter')} {p.get('clock')}  {ds:<14} "
              f"[{p.get('play_type')}] {(p.get('text_desc') or '')[:80]}")

    sp = game["scoring_plays"]
    print(f"\nScoring plays: {len(sp)} total")
    for s in sp[:3]:
        print(f"  Q{s['quarter']} {s['clock']}  {s['team_abbr']} "
              f"{s['score_type']} {s['yards']}yds {s.get('how','')} → {s.get('scorer')}")

    fgas = game["fgas"]
    print(f"\nFG attempts: {len(fgas)} total")
    for f in fgas:
        print(f"  Q{f['quarter']} {f['kicker']} {f['distance']}yd {f['result']} ({f['points']}pt)")

    home_players = game["home_players"]
    away_players = game["away_players"]
    print(f"\nPlayers: home={len(home_players)} away={len(away_players)}")
    qbs = [p for p in (home_players + away_players) if p.get("pass_att")]
    print(f"  QBs with pass attempts: {len(qbs)}")
    for q in qbs[:3]:
        print(f"    {q['team_abbr']:>3} {q['full_name']:<30} "
              f"{q.get('pass_comp')}/{q.get('pass_att')} "
              f"{q.get('pass_yds')}yd {q.get('pass_td')}TD {q.get('pass_int')}INT "
              f"rating={q.get('pass_rating')}")

    print("\n" + "=" * 64)
    checks = {
        "venue.stadium":        bool(v.get("stadium")),
        "venue.weather":        v.get("temp_f") is not None,
        "rules":                bool(rules),
        "officials":            len(officials) > 0,
        "home.totals":          home.get("plays") is not None,
        "away.totals":          away.get("plays") is not None,
        "drives":               len(drives) > 0,
        "plays":                len(plays) > 0,
        "scoring_plays":        len(sp) > 0,
        "fgas":                 isinstance(fgas, list),
        "home_players":         len(home_players) > 0,
        "away_players":         len(away_players) > 0,
    }
    for k, v_ok in checks.items():
        print(f"  [{('PASS' if v_ok else 'FAIL')}]  {k}")
    all_pass = all(checks.values())
    print(f"\n{'PASS' if all_pass else 'FAIL'}: StatBroadcast ingestion {'is fully working' if all_pass else 'has gaps'}")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
