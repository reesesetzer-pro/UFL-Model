"""
Supabase ETL: parsed StatBroadcast JSON -> normalized DB rows.

Schema reference: schema.sql at repo root.

Tables loaded
-------------
games                  one row per game
team_game_stats        two rows per game (home/away offense)
drives                 one row per offensive drive
plays                  one row per play
scoring_plays          one row per scoring play
player_game_stats      one row per (player, game)
officials              one row per (game, role)
game_rules             one row per (game, rule_key)

Idempotent — uses upsert with `on_conflict` so re-running over the same
parsed JSON updates rather than duplicates.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.data.schedule import schedule_by_id, TEAMS


def _client():
    from src.db.supabase_client import get_client
    return get_client()


# --------------------------------------------------------------------------
# Helpers

def _sb_id(p: dict) -> int:
    """Parser writes top-level key as 'game_id'; schema column is 'sb_id'."""
    return int(p.get("game_id") or p.get("sb_id"))


def _split_location(loc: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """`venue.location` is `"City,ST"` (e.g. `"Louisville,KY"`)."""
    if not loc:
        return None, None
    parts = [s.strip() for s in loc.split(",")]
    if len(parts) >= 2:
        return parts[0], parts[1]
    return parts[0], None


def _pct(conv: Optional[int], att: Optional[int]) -> Optional[float]:
    if not conv or not att:
        return None
    try:
        return round(100.0 * conv / att, 2)
    except (TypeError, ZeroDivisionError):
        return None


def _drive_num_from_play(play: dict) -> Optional[int]:
    """raw_play_id format `'D,S,P'` where D=drive_index, P=play-in-drive."""
    rid = play.get("raw_play_id") or ""
    parts = rid.split(",") if isinstance(rid, str) else []
    if not parts or not parts[0].lstrip("-").isdigit():
        return None
    return int(parts[0])


def _play_num_from_play(play: dict) -> Optional[int]:
    rid = play.get("raw_play_id") or ""
    parts = rid.split(",") if isinstance(rid, str) else []
    if len(parts) < 3 or not parts[2].lstrip("-").isdigit():
        return None
    return int(parts[2])


def _play_yards(play: dict) -> Optional[int]:
    """Yards gained on the play. Pass plays use pass_gain, rushes use rush_gain."""
    g = play.get("pass_gain")
    if g is not None:
        return int(g)
    g = play.get("rush_gain")
    if g is not None:
        return int(g)
    return None


def _drive_result_pts(d: dict, scoring_by_drive: dict) -> int:
    """Points the offense scored on this drive (excluding PAT, which is on the
    scoring_play row)."""
    end = (d.get("end_how") or "").upper()
    if end == "TD":
        return 6
    if end == "FG":
        sp = scoring_by_drive.get(d.get("drive_index"))
        if sp and (sp.get("yards") or 0) >= 60:
            return 4
        return 3
    return 0


def _scoring_points(s: dict) -> Optional[int]:
    st = (s.get("score_type") or "").upper()
    if st == "TD":
        return 6 + int(s.get("pat_made") or 0)
    if st == "FG":
        return 4 if (s.get("yards") or 0) >= 60 else 3
    if st == "SAF":
        return 2
    return None


def _scoring_text(s: dict) -> str:
    """Synthesize a short description (parser doesn't keep a raw text)."""
    st = s.get("score_type") or ""
    yds = s.get("yards")
    scorer = s.get("scorer") or ""
    how = s.get("how") or ""
    parts = [st]
    if yds is not None:
        parts.append(f"{yds}yd")
    if how:
        parts.append(how)
    if scorer:
        parts.append(f"by {scorer}")
    return " ".join(parts)


def _count_4pt_fg(p: dict, team_code: str) -> tuple[int, int]:
    """Returns (att, made) for 60+yd FG attempts by this team in this game."""
    att = made = 0
    for f in (p.get("fgas") or []):
        if f.get("team_abbr") == team_code and (f.get("points") or 0) == 4:
            att += 1
            if f.get("result") == "GOOD":
                made += 1
    return att, made


# --------------------------------------------------------------------------
# Player stat mapping
#
# Schema columns are stat_*. Parser names differ — explicit map below.
# Anything missing is left None.

_PLAYER_STAT_MAP: dict[str, str] = {
    # passing (QB)
    "stat_pass_att":     "pass_att",
    "stat_pass_comp":    "pass_comp",
    "stat_pass_yds":     "pass_yds",
    "stat_pass_td":      "pass_td",
    "stat_pass_int":     "pass_int",
    "stat_pass_long":    "pass_long",
    "stat_pass_rating":  "pass_rating",
    "stat_throwaways":   "pass_throwaways",
    # rushing (any ball-carrier)
    "stat_rush_att":     "rush_att",
    "stat_rush_yds":     "rush_yds",
    "stat_rush_td":      "rush_td",
    "stat_rush_long":    "rush_long",
    # receiving
    "stat_rec":          "rcv_no",
    "stat_rec_yds":      "rcv_yds",
    "stat_rec_td":       "rcv_td",
    "stat_rec_long":     "rcv_long",
    "stat_yac":          "rcv_yac",
    "stat_targets":      "rcv_targets",
    "stat_drops":        "rcv_drops",
    # defense
    "stat_solo":         "def_solo",
    "stat_total_tackles":"def_total",
    "stat_assists":      "def_assist",
    "stat_sacks":        "def_sacks",
    "stat_tfl":          "def_tfl",
    "stat_int":          "def_int",
    "stat_pd":           "def_brup",
    "stat_fum_forced":   "def_ff",
    "stat_fum_recov":    "def_fr",
    # punting
    "stat_punt_att":     "punt_no",
    "stat_punt_yds":     "punt_yds",
    "stat_punt_long":    "punt_long",
    "stat_punt_avg":     "punt_avg",
    # returns
    "stat_kr":           "kr_no",
    "stat_kr_yds":       "kr_yds",
    "stat_kr_long":      "kr_long",
    "stat_pr":           "pr_no",
    "stat_pr_yds":       "pr_yds",
    "stat_pr_long":      "pr_long",
    # kicking
    "stat_fg_made":      "fg_made",
    "stat_fg_att":       "fg_att",
    "stat_pat_made":     "pat_kick_made",
    "stat_pat_att":      "pat_kick_att",
}


# --------------------------------------------------------------------------
# Row builders

def _games_row(p: dict) -> dict:
    sb_id = _sb_id(p)
    sched = schedule_by_id().get(sb_id)
    venue = p.get("venue") or {}
    home_team = TEAMS.get(p["home"])
    city, state = _split_location(venue.get("location"))
    home_tot = p.get("home_totals") or {}
    away_tot = p.get("away_totals") or {}
    return {
        "sb_id":         sb_id,
        "season":        venue.get("season") or 2026,
        "week":          p.get("week") or (sched.week if sched else None),
        "game_date":     (sched.date.isoformat() if sched else p.get("game_date")),
        "home":          p["home"], "away": p["away"],
        "home_score":    int(home_tot.get("linescore_total") or 0),
        "away_score":    int(away_tot.get("linescore_total") or 0),
        "stadium":       venue.get("stadium"),
        "city":          city,
        "state":         state,
        "attendance":    venue.get("attendance"),
        "temperature_f": venue.get("temp_f"),
        "wind":          venue.get("wind"),
        "weather":       venue.get("weather"),
        "duration":      venue.get("duration"),
        "indoor":        (home_team.indoor if home_team else None),
        "network":       (sched.network if sched else None),
    }


def _team_stat_rows(p: dict) -> list[dict]:
    out = []
    for side, team_code in (("home", p["home"]), ("away", p["away"])):
        tot = p.get(f"{side}_totals") or {}
        fg4_att, fg4_made = _count_4pt_fg(p, team_code)
        out.append({
            "sb_id": _sb_id(p), "team": team_code, "side": side,
            "score":           tot.get("linescore_total"),
            "total_plays":     tot.get("plays"),
            "total_yards":     tot.get("total_yards"),
            "first_downs":     tot.get("fd_total"),
            "first_downs_rush":tot.get("fd_rush"),
            "first_downs_pass":tot.get("fd_pass"),
            "first_downs_pen": tot.get("fd_pen"),
            "third_down_att":  tot.get("third_down_att"),
            "third_down_conv": tot.get("third_down_conv"),
            "third_down_pct":  _pct(tot.get("third_down_conv"), tot.get("third_down_att")),
            "fourth_down_att": tot.get("fourth_down_att"),
            "fourth_down_conv":tot.get("fourth_down_conv"),
            "fourth_down_pct": _pct(tot.get("fourth_down_conv"), tot.get("fourth_down_att")),
            "rush_att":  tot.get("rush_att"),
            "rush_yds":  tot.get("rush_yds"),
            "rush_td":   tot.get("rush_td"),
            "rush_long": tot.get("rush_long"),
            "pass_comp": tot.get("pass_comp"),
            "pass_att":  tot.get("pass_att"),
            "pass_int":  tot.get("pass_int"),
            "pass_yds":  tot.get("pass_yds"),
            "pass_td":   tot.get("pass_td"),
            "pass_long": tot.get("pass_long"),
            "sacks":     tot.get("pass_sacks"),
            "sack_yds":  tot.get("pass_sack_yds"),
            "rec_count": tot.get("rcv_no"),
            "rec_yds":   tot.get("rcv_yds"),
            "rec_yac":   tot.get("rcv_yac"),
            "rec_td":    tot.get("rcv_td"),
            "rec_long":  tot.get("rcv_long"),
            "rz_attempts": tot.get("redzone_att"),
            "rz_tds":      tot.get("redzone_scores"),  # parser doesn't split TD vs FG
            "rz_fgs":      None,
            "penalties":   tot.get("penalties"),
            "penalty_yds": tot.get("penalty_yds"),
            "fumbles":     tot.get("fumbles"),
            "fumbles_lost":tot.get("fumbles_lost"),
            "top_seconds": tot.get("top_seconds"),
            "alt_ko_att":  tot.get("alt_ko_att"),
            "alt_ko_conv": tot.get("alt_ko_conv"),
            "fg_made":     tot.get("fg_made"),
            "fg_att":      tot.get("fg_att"),
            "fg_4pt_made": fg4_made,
            "fg_4pt_att":  fg4_att,
        })
    return out


def _drive_rows(p: dict) -> list[dict]:
    sps = {s.get("drive_index"): s
           for s in (p.get("scoring_plays") or [])
           if s.get("score_type") == "FG"}
    rows = []
    for d in (p.get("drives") or []):
        rows.append({
            "sb_id":          _sb_id(p),
            "drive_num":      d.get("drive_index"),
            "team":           d.get("team_abbr"),
            "quarter":        d.get("start_qtr"),
            "start_time":     d.get("start_clock"),
            "end_time":       d.get("end_clock"),
            "start_how":      d.get("start_how"),
            "end_how":        d.get("end_how"),
            "start_yardline": d.get("start_yardline"),
            "end_yardline":   d.get("end_yardline"),
            "yards":          d.get("yards"),
            "plays_count":    d.get("plays"),  # parser stores plays as int count
            "top_seconds":    d.get("top_seconds"),
            "result_pts":     _drive_result_pts(d, sps),
            "inside_20":      d.get("inside_20"),
        })
    return rows


def _play_rows(p: dict) -> list[dict]:
    rows = []
    for play in (p.get("plays") or []):
        air = play.get("pass_air_yds")
        gain = _play_yards(play)
        yac = (gain - air) if (air is not None and gain is not None) else None
        rows.append({
            "sb_id":     _sb_id(p),
            "drive_num": _drive_num_from_play(play),
            "play_num":  _play_num_from_play(play),
            "team":      play.get("has_ball"),
            "quarter":   play.get("quarter"),
            "clock":     play.get("clock"),
            "down":      play.get("down"),
            "distance":  play.get("togo"),
            "yardline":  play.get("yardline"),
            "play_type": play.get("play_type"),
            "yards":     gain,
            "air_yards": air,
            "yac":       yac,
            "is_td":     bool(play.get("is_score")),
            "is_turnover": bool(play.get("turnover_type")),
            "is_sack":   None,  # parser doesn't expose explicit sack flag
            "is_penalty":bool(play.get("is_no_play")),  # imperfect proxy
            "play_text": play.get("text_desc"),
        })
    return rows


def _scoring_rows(p: dict) -> list[dict]:
    rows = []
    for s in (p.get("scoring_plays") or []):
        rows.append({
            "sb_id":   _sb_id(p),
            "team":    s.get("team_abbr"),
            "quarter": s.get("quarter"),
            "clock":   s.get("clock"),
            "score_type": s.get("score_type"),
            "points":     _scoring_points(s),
            "pat_type":   s.get("pat_type"),
            "pat_made":   bool(s.get("pat_made")),
            "is_4pt_fg":  (s.get("score_type") == "FG" and (s.get("yards") or 0) >= 60),
            "play_text":  _scoring_text(s),
            "home_score_after": s.get("score_h_after"),
            "away_score_after": s.get("score_v_after"),
        })
    return rows


def _player_rows(p: dict) -> list[dict]:
    rows = []
    for side, team_code in (("home", p["home"]), ("away", p["away"])):
        for pl in (p.get(f"{side}_players") or []):
            row = {
                "sb_id":     _sb_id(p),
                "team":      pl.get("team_abbr") or team_code,
                "side":      side,
                "player_id": pl.get("player_code"),
                "name":      pl.get("full_name"),
                "jersey":    pl.get("uniform"),
                "position":  pl.get("position"),
            }
            for col, src in _PLAYER_STAT_MAP.items():
                row[col] = pl.get(src)
            rows.append(row)
    return rows


def _officials_rows(p: dict) -> list[dict]:
    rows = []
    for o in (p.get("officials") or []):
        rows.append({
            "sb_id":   _sb_id(p),
            "role":    o.get("role"),
            "name":    o.get("name"),
            "uniform": o.get("uniform"),
        })
    return rows


def _rules_rows(p: dict) -> list[dict]:
    rows = []
    for k, v in (p.get("rules") or {}).items():
        rows.append({
            "sb_id":      _sb_id(p),
            "rule_key":   k,
            "rule_value": str(v),
        })
    return rows


# --------------------------------------------------------------------------
# Loaders

def load_one(parsed_path: Path, dry_run: bool = False) -> dict:
    p = json.loads(parsed_path.read_text())
    if dry_run:
        client = None
    else:
        client = _client()

    counts = {}
    targets = [
        ("games",             [_games_row(p)],            "sb_id"),
        ("team_game_stats",   _team_stat_rows(p),         "sb_id,team"),
        ("drives",            _drive_rows(p),             "sb_id,drive_num"),
        ("plays",             _play_rows(p),              "sb_id,drive_num,play_num"),
        ("scoring_plays",     _scoring_rows(p),           "sb_id,quarter,clock,team"),
        ("player_game_stats", _player_rows(p),            "sb_id,team,player_id"),
        ("officials",         _officials_rows(p),         "sb_id,role"),
        ("game_rules",        _rules_rows(p),             "sb_id,rule_key"),
    ]
    for table, rows, on_conflict in targets:
        if not rows:
            counts[table] = 0
            continue
        if dry_run:
            counts[table] = len(rows)
            continue
        try:
            client.table(table).upsert(rows, on_conflict=on_conflict).execute()
            counts[table] = len(rows)
        except Exception as e:
            print(f"  [warn] {table}: {e}")
            counts[table] = -1
    return counts


def load_all(parsed_dir: Path = Path("data/parsed"),
             dry_run: bool = False) -> dict:
    if not parsed_dir.exists():
        raise SystemExit(f"No parsed dir at {parsed_dir}")
    files = sorted(parsed_dir.glob("*.json"))
    print(f"[etl] {len(files)} parsed games to load")
    grand = {}
    for f in files:
        c = load_one(f, dry_run=dry_run)
        for k, v in c.items():
            grand[k] = grand.get(k, 0) + (v if v >= 0 else 0)
        print(f"  {f.stem}: {c}")
    print(f"\n[etl] grand totals: {grand}")
    return grand


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--parsed-dir", default="data/parsed")
    ap.add_argument("--dry-run", action="store_true",
                    help="Build rows and count, but don't write to DB.")
    ap.add_argument("--single", help="Load just this parsed JSON path")
    args = ap.parse_args()

    if args.single:
        c = load_one(Path(args.single), dry_run=args.dry_run)
        print(c)
    else:
        load_all(Path(args.parsed_dir), dry_run=args.dry_run)
