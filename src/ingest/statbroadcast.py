"""StatBroadcast ingestion — the UFL model's primary stats source.

Three URL patterns we use:

    Hub (lists every game with its id):
        http://www.statbroadcast.com/events/statmonitr.php?gid=ufl

    Per-game XML (full structured data — drives, plays, players, weather):
        http://archive.statbroadcast.com/{gameId}.xml

    League stats PDF (stable URL, weekly cross-check):
        https://s3.us-east-1.amazonaws.com/s3.statbroadcast.com/hosted/pdf/ufl/{season}league.pdf

The XML schema is documented in DATA_INVENTORY.md.

All parsers are defensive — missing/malformed fields return None rather than
raising, since we expect the schema to evolve as StatBroadcast updates.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Iterable, Optional
from xml.etree import ElementTree as ET

import requests

HUB_URL = "http://www.statbroadcast.com/events/statmonitr.php?gid=ufl"
ARCHIVE_HUB_URL = "http://www.statbroadcast.com/events/archive.php?gid=ufl&live=1"
SCHEDULE_URL = "http://www.statbroadcast.com/events/schedule.php?live=0&gid=ufl"
ARCHIVE_BASE = "http://archive.statbroadcast.com"
LEAGUE_PDF = "https://s3.us-east-1.amazonaws.com/s3.statbroadcast.com/hosted/pdf/ufl/{season}league.pdf"

DEFAULT_TIMEOUT = 20
DEFAULT_HEADERS = {
    "User-Agent": "UFLModel/0.1",
    "Accept": "*/*",
}

GAME_ID_RE = re.compile(r"archived\.php\?id=(\d+)")
SCORE_RE = re.compile(
    r"([A-Za-z\.\s]+?)\s+(\d+),\s+([A-Za-z\.\s]+?)\s+(\d+)\s*-\s*FINAL", re.I
)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def fetch_hub_html() -> str:
    """Pull the UFL hub HTML — has today's games + this week's results."""
    r = requests.get(HUB_URL, headers=DEFAULT_HEADERS, timeout=DEFAULT_TIMEOUT)
    r.raise_for_status()
    return r.text


def fetch_archive_html() -> str:
    """Pull the historical archive page — every UFL game ever recorded by SB."""
    r = requests.get(ARCHIVE_HUB_URL, headers=DEFAULT_HEADERS, timeout=DEFAULT_TIMEOUT)
    r.raise_for_status()
    return r.text


def parse_game_ids(html: str) -> list[str]:
    """Extract all StatBroadcast game IDs referenced in a hub-style page."""
    return sorted(set(GAME_ID_RE.findall(html)))


# ---------------------------------------------------------------------------
# Per-game fetch + cache
# ---------------------------------------------------------------------------
def xml_url(game_id: str) -> str:
    return f"{ARCHIVE_BASE}/{game_id}.xml"


def fetch_game_xml(
    game_id: str,
    cache_dir: Optional[Path] = None,
    use_cache: bool = True,
) -> str:
    """GET archive.statbroadcast.com/{id}.xml — returns raw XML text."""
    if cache_dir is not None and use_cache:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached = cache_dir / f"{game_id}.xml"
        if cached.exists() and cached.stat().st_size > 1000:
            return cached.read_text(encoding="utf-8")

    r = requests.get(xml_url(game_id), headers=DEFAULT_HEADERS, timeout=DEFAULT_TIMEOUT)
    r.raise_for_status()
    text = r.text

    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / f"{game_id}.xml").write_text(text, encoding="utf-8")
    return text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _attr_int(el: Optional[ET.Element], key: str) -> Optional[int]:
    if el is None:
        return None
    v = el.get(key)
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _attr_num(el: Optional[ET.Element], key: str) -> Optional[float]:
    if el is None:
        return None
    v = el.get(key)
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _attr_str(el: Optional[ET.Element], key: str) -> Optional[str]:
    if el is None:
        return None
    v = el.get(key)
    if v is None or v == "":
        return None
    return v


def _top_to_seconds(top: Optional[str]) -> Optional[int]:
    """Convert 'M:SS' or 'MM:SS' to total seconds."""
    if not top:
        return None
    parts = top.strip().split(":")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        return None


def _spot_to_yardline(spot: Optional[str], possessing: Optional[str]) -> Optional[int]:
    """
    Normalize a 'BHM28' style spot to yards-from-own-goal-line (0..100) for the
    possessing team. If possessing team's abbr is in the spot string, it's their
    own territory (yardline = number). Otherwise it's opponent territory
    (yardline = 100 - number).
    """
    if not spot or not possessing:
        return None
    m = re.match(r"([A-Za-z]+)(\d+)", spot.strip())
    if not m:
        return None
    spot_team = m.group(1).upper()
    yards = int(m.group(2))
    if spot_team == possessing.upper():
        return yards
    return 100 - yards


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------
def parse_venue(root: ET.Element) -> dict[str, Any]:
    venue = root.find("venue")
    if venue is None:
        return {}
    return {
        "vis_id":     _attr_str(venue, "visid"),
        "home_id":    _attr_str(venue, "homeid"),
        "vis_name":   _attr_str(venue, "visname"),
        "home_name":  _attr_str(venue, "homename"),
        "date":       _attr_str(venue, "date"),
        "start_time": _attr_str(venue, "start"),
        "end_time":   _attr_str(venue, "end"),
        "duration":   _attr_str(venue, "duration"),
        "location":   _attr_str(venue, "location"),
        "stadium":    _attr_str(venue, "stadium"),
        "attendance": _attr_int(venue, "attend"),
        "temp_f":     _attr_int(venue, "temp"),
        "wind":       _attr_str(venue, "wind"),
        "weather":    _attr_str(venue, "weather"),
        "week":       _attr_int(venue, "week"),
        "season":     _attr_int(venue, "season"),
        "league":     _attr_str(venue, "type"),
    }


def parse_rules(root: ET.Element) -> dict[str, Any]:
    rules = root.find(".//rules")
    if rules is None:
        return {}
    return {
        "quarters":          _attr_int(rules, "qtrs"),
        "minutes_per":       _attr_int(rules, "mins"),
        "downs":             _attr_int(rules, "downs"),
        "yards_first":       _attr_int(rules, "yds"),
        "ko_spot":           _attr_int(rules, "kospot"),
        "tb_spot":           _attr_int(rules, "tbspot"),
        "ko_tb_spot":        _attr_int(rules, "kotbspot"),
        "pat_spot":          _attr_int(rules, "patspot"),
        "pat2_spot":         _attr_int(rules, "pat2spot"),
        "pat3_spot":         _attr_int(rules, "pat3spot"),
        "safety_spot":       _attr_int(rules, "safspot"),
        "td_points":         _attr_int(rules, "td"),
        "fg_points":         _attr_int(rules, "fg"),
        "fg4_points":        _attr_int(rules, "fg4"),
        "pat_options":       _attr_str(rules, "pat"),
        "field_yards":       _attr_int(rules, "field"),
        "timeouts_per_half": _attr_int(rules, "toh"),
    }


def parse_officials(root: ET.Element) -> list[dict[str, Any]]:
    """Return a list of {role, name, uniform} from the officials element."""
    off = root.find(".//officials")
    if off is None:
        return []
    out = []
    for role, value in off.attrib.items():
        if not value:
            continue
        # Format: "Jason Autrey (5)"  → name + uniform
        m = re.match(r"^(.+?)\s*\((\d+)\)\s*$", value)
        if m:
            name, uni = m.group(1).strip(), int(m.group(2))
        else:
            name, uni = value.strip(), None
        out.append({"role": role, "name": name, "uniform": uni})
    return out


def parse_team_totals(team_el: ET.Element) -> dict[str, Any]:
    """Extract a team's totals + linescore + record."""
    abbr = _attr_str(team_el, "id")
    name = _attr_str(team_el, "name")
    record = _attr_str(team_el, "record")
    is_home = (_attr_str(team_el, "vh") or "").upper() == "H"

    linescore = team_el.find("linescore")
    line_periods = []
    if linescore is not None:
        for lp in linescore.findall("lineprd"):
            line_periods.append({
                "period": _attr_int(lp, "prd"),
                "score": _attr_int(lp, "score"),
            })
    line_periods_padded = line_periods + [{"period": i, "score": 0}
                                          for i in range(len(line_periods)+1, 6)]
    line_periods_padded = line_periods_padded[:5]

    totals = team_el.find("totals")
    out: dict[str, Any] = {
        "team_abbr": abbr,
        "team_name": name,
        "record_text": record,
        "is_home": is_home,
        "linescore_total": _attr_int(linescore, "score") if linescore is not None else None,
        "line_periods": line_periods_padded,
    }
    if totals is None:
        return out

    # Top-level totoff_* attrs on <totals>
    out["plays"] = _attr_int(totals, "totoff_plays")
    out["total_yards"] = _attr_int(totals, "totoff_yards")
    out["yards_per_play"] = _attr_num(totals, "totoff_avg")

    # Sub-elements
    fd = totals.find("firstdowns")
    if fd is not None:
        out["fd_total"] = _attr_int(fd, "no")
        out["fd_rush"] = _attr_int(fd, "rush")
        out["fd_pass"] = _attr_int(fd, "pass")
        out["fd_pen"] = _attr_int(fd, "penalty")

    pen = totals.find("penalties")
    if pen is not None:
        out["penalties"] = _attr_int(pen, "no")
        out["penalty_yds"] = _attr_int(pen, "yds")

    cv = totals.find("conversions")
    if cv is not None:
        out["third_down_conv"] = _attr_int(cv, "thirdconv")
        out["third_down_att"] = _attr_int(cv, "thirdatt")
        out["fourth_down_conv"] = _attr_int(cv, "fourthconv")
        out["fourth_down_att"] = _attr_int(cv, "fourthatt")
        out["goal_to_go_conv"] = _attr_int(cv, "goalgotogconv")
        out["goal_to_go_att"] = _attr_int(cv, "goaltogoatt")
        out["alt_ko_conv"] = _attr_int(cv, "altkoconv")
        out["alt_ko_att"] = _attr_int(cv, "altkoatt")

    fum = totals.find("fumbles")
    if fum is not None:
        out["fumbles"] = _attr_int(fum, "no")
        out["fumbles_lost"] = _attr_int(fum, "lost")

    misc = totals.find("misc")
    if misc is not None:
        out["top_text"] = _attr_str(misc, "top")
        out["top_seconds"] = _top_to_seconds(_attr_str(misc, "top"))
        out["points_off_to"] = _attr_int(misc, "ptsto")

    rz = totals.find("redzone")
    if rz is not None:
        out["redzone_att"] = _attr_int(rz, "att")
        out["redzone_scores"] = _attr_int(rz, "scores")

    rush = totals.find("rush")
    if rush is not None:
        out["rush_att"] = _attr_int(rush, "att")
        out["rush_yds"] = _attr_int(rush, "yds")
        out["rush_gain"] = _attr_int(rush, "gain")
        out["rush_loss"] = _attr_int(rush, "loss")
        out["rush_td"] = _attr_int(rush, "td")
        out["rush_long"] = _attr_int(rush, "long")

    pa = totals.find("pass")
    if pa is not None:
        out["pass_comp"] = _attr_int(pa, "comp")
        out["pass_att"] = _attr_int(pa, "att")
        out["pass_int"] = _attr_int(pa, "int")
        out["pass_yds"] = _attr_int(pa, "yds")
        out["pass_td"] = _attr_int(pa, "td")
        out["pass_long"] = _attr_int(pa, "long")
        out["pass_sacks"] = _attr_int(pa, "sacks")
        out["pass_sack_yds"] = _attr_int(pa, "sackyds")

    rcv = totals.find("rcv")
    if rcv is not None:
        out["rcv_no"] = _attr_int(rcv, "no")
        out["rcv_yds"] = _attr_int(rcv, "yds")
        out["rcv_td"] = _attr_int(rcv, "td")
        out["rcv_long"] = _attr_int(rcv, "long")
        out["rcv_yac"] = _attr_int(rcv, "sb-yac")

    pu = totals.find("punt")
    if pu is not None:
        out["punt_no"] = _attr_int(pu, "no")
        out["punt_yds"] = _attr_int(pu, "yds")
        out["punt_long"] = _attr_int(pu, "long")
        out["punt_inside20"] = _attr_int(pu, "inside20")
        out["punt_avg"] = _attr_num(pu, "avg")

    ko = totals.find("ko")
    if ko is not None:
        out["ko_no"] = _attr_int(ko, "no")
        out["ko_yds"] = _attr_int(ko, "yds")
        out["ko_tb"] = _attr_int(ko, "tb")

    fg = totals.find("fg")
    if fg is not None:
        out["fg_made"] = _attr_int(fg, "made")
        out["fg_att"] = _attr_int(fg, "att")
        out["fg_long"] = _attr_int(fg, "long")
        out["fg_blocked"] = _attr_int(fg, "blkd")

    pat = totals.find("pat")
    if pat is not None:
        out["pat_kick_made"] = _attr_int(pat, "kickmade")
        out["pat_kick_att"] = _attr_int(pat, "kickatt")
        # UFL alternative point system
        out["one_point_made"] = _attr_int(pat, "one_point_successes")
        out["one_point_att"] = _attr_int(pat, "one_point_attempts")
        out["two_point_made"] = _attr_int(pat, "two_point_successes")
        out["two_point_att"] = _attr_int(pat, "two_point_attempts")
        out["three_point_made"] = _attr_int(pat, "three_point_successes")
        out["three_point_att"] = _attr_int(pat, "three_point_attempts")

    df = totals.find("defense")
    if df is not None:
        out["def_solo"] = _attr_int(df, "tackua")
        out["def_assist"] = _attr_int(df, "tacka")
        out["def_total"] = _attr_int(df, "tot_tack")
        out["def_tfl"] = _attr_num(df, "tflua")
        out["def_tfl_yds"] = _attr_num(df, "tflyds")
        out["def_sacks"] = _attr_num(df, "sacks")
        out["def_sack_yds"] = _attr_num(df, "sackyds")
        out["def_brup"] = _attr_int(df, "brup")
        out["def_int"] = _attr_int(df, "int")
        out["def_int_yds"] = _attr_int(df, "intyds")
        out["def_ff"] = _attr_int(df, "ff")
        out["def_fr"] = _attr_int(df, "fr")
        out["def_fr_yds"] = _attr_int(df, "fryds")
        out["def_blocked"] = _attr_int(df, "blkd")
        out["def_safeties"] = _attr_int(df, "saf")

    for ret_tag, prefix in [("kr", "kr"), ("pr", "pr"), ("ir", "ir"), ("fr", "fr")]:
        ret = totals.find(ret_tag)
        if ret is not None:
            out[f"{prefix}_no"] = _attr_int(ret, "no")
            out[f"{prefix}_yds"] = _attr_int(ret, "yds")
            out[f"{prefix}_td"] = _attr_int(ret, "td")
            out[f"{prefix}_long"] = _attr_str(ret, "long")  # text since may be empty

    sc = totals.find("scoring")
    if sc is not None:
        out["score_td"] = _attr_int(sc, "td")
        out["score_fg"] = _attr_int(sc, "fg")
        out["score_pat"] = _attr_int(sc, "patkick")

    return out


def parse_drives(root: ET.Element) -> list[dict[str, Any]]:
    """One row per drive."""
    drives = root.find("drives")
    if drives is None:
        return []
    out = []
    for d in drives.findall("drive"):
        team_abbr_team = _attr_str(d, "team")
        is_home = (_attr_str(d, "vh") or "").upper() == "H"
        out.append({
            "drive_index":   _attr_int(d, "driveindex"),
            "team_abbr":     team_abbr_team,
            "is_home":       is_home,
            "start_how":     _attr_str(d, "start_how"),
            "start_qtr":     _attr_int(d, "start_qtr"),
            "start_clock":   _attr_str(d, "start_time"),
            "start_spot":    _attr_str(d, "start_spot"),
            "start_yardline": _spot_to_yardline(_attr_str(d, "start_spot"), team_abbr_team),
            "end_how":       _attr_str(d, "end_how"),
            "end_qtr":       _attr_int(d, "end_qtr"),
            "end_clock":     _attr_str(d, "end_time"),
            "end_spot":      _attr_str(d, "end_spot"),
            "end_yardline":  _spot_to_yardline(_attr_str(d, "end_spot"), team_abbr_team),
            "plays":         _attr_int(d, "plays"),
            "yards":         _attr_int(d, "yards"),
            "top_text":      _attr_str(d, "top"),
            "top_seconds":   _top_to_seconds(_attr_str(d, "top")),
            "first_downs":   _attr_int(d, "firsts"),
            "inside_20":     (_attr_str(d, "inside_20") or "").upper() == "Y",
            "is_score":      (_attr_str(d, "scoring") or "").upper() == "Y",
        })
    return out


def parse_scoring_plays(root: ET.Element) -> list[dict[str, Any]]:
    scores = root.find("scores")
    if scores is None:
        return []
    out = []
    for s in scores.findall("score"):
        out.append({
            "team_abbr":         _attr_str(s, "team"),
            "is_home":           (_attr_str(s, "vh") or "").upper() == "H",
            "quarter":           _attr_int(s, "qtr"),
            "ot":                _attr_int(s, "ot"),
            "clock":             _attr_str(s, "clock"),
            "score_type":        _attr_str(s, "type"),
            "yards":             _attr_int(s, "yds"),
            "scorer":            _attr_str(s, "scorer"),
            "passer":            _attr_str(s, "passer"),
            "how":               _attr_str(s, "how"),
            "pat_attempted":     _attr_int(s, "patatt"),
            "pat_made":          _attr_int(s, "patmade"),
            "pat_by":            _attr_str(s, "patby"),
            "pat_type":          _attr_str(s, "pattype"),
            "pat_result":        _attr_str(s, "patres"),
            "drive_plays":       _attr_int(s, "plays"),
            "drive_yards":       _attr_int(s, "drive"),
            "drive_top":         _attr_str(s, "top"),
            "score_v_after":     _attr_int(s, "vscore"),
            "score_h_after":     _attr_int(s, "hscore"),
            "drive_index":       _attr_int(s, "driveindex"),
        })
    return out


def parse_fgas(root: ET.Element) -> list[dict[str, Any]]:
    fgas = root.find("fgas")
    if fgas is None:
        return []
    out = []
    for f in fgas.findall("fga"):
        out.append({
            "team_abbr":   _attr_str(f, "team"),
            "is_home":     (_attr_str(f, "vh") or "").upper() == "H",
            "kicker":      _attr_str(f, "kicker"),
            "quarter":     _attr_int(f, "qtr"),
            "clock":       _attr_str(f, "clock"),
            "distance":    _attr_int(f, "distance"),
            "result":      _attr_str(f, "result"),
            "points":      _attr_int(f, "points"),
        })
    return out


def parse_plays(root: ET.Element) -> list[dict[str, Any]]:
    """Extract every <play> from the PBP. Drops separator `#` plays."""
    plays_root = root.find("plays")
    if plays_root is None:
        return []
    out = []
    for qtr in plays_root.findall("qtr"):
        quarter = _attr_int(qtr, "number")
        for p in qtr.findall("play"):
            ptype = _attr_str(p, "type")
            if ptype in ("#",) or p.get("text", "").startswith("End Quarter"):
                continue
            row: dict[str, Any] = {
                "quarter":      quarter,
                "clock":        _attr_str(p, "clock"),
                "has_ball":     _attr_str(p, "hasball"),
                "down":         _attr_int(p, "down"),
                "togo":         _attr_int(p, "togo"),
                "spot":         _attr_str(p, "spot"),
                "yardline":     _spot_to_yardline(_attr_str(p, "spot"), _attr_str(p, "hasball")),
                "play_type":    ptype,
                "text_desc":    _attr_str(p, "text"),
                "is_score":     (_attr_str(p, "score") or "").upper() == "Y",
                "score_v":      _attr_int(p, "vscore"),
                "score_h":      _attr_int(p, "hscore"),
                "is_first_down": _attr_str(p, "first") is not None,
                "turnover_type": _attr_str(p, "turnover"),
                "is_no_play":   "NOPLAY" in (_attr_str(p, "tokens") or ""),
                "sequence_id":  _attr_str(p, "sequence"),
                "raw_play_id":  _attr_str(p, "playid"),
            }
            # pass details
            pa = p.find("p_pa")
            if pa is not None:
                row["pass_qb"] = _attr_str(pa, "qb")
                row["pass_result"] = _attr_str(pa, "result")
                row["pass_gain"] = _attr_int(pa, "gain")
                row["pass_rcv"] = _attr_str(pa, "rcv")
                row["pass_air_yds"] = _attr_int(pa, "air")
            # rush details
            ru = p.find("p_ru")
            if ru is not None:
                row["rush_runner"] = _attr_str(ru, "name")
                row["rush_gain"] = _attr_int(ru, "gain")
                row["rush_scramble"] = (_attr_str(ru, "scramble") or "").upper() == "Y"
            # penalty
            pn = p.find("p_pn")
            if pn is not None:
                row["pen_code"] = _attr_str(pn, "code")
                row["pen_type"] = _attr_str(pn, "type")
                row["pen_result"] = _attr_str(pn, "result")
                row["pen_yards"] = _attr_int(pn, "yards")
                row["pen_player"] = _attr_str(pn, "name")
            out.append(row)
    return out


def parse_players(team_el: ET.Element) -> list[dict[str, Any]]:
    """Extract per-player stats from a team element."""
    team_abbr = _attr_str(team_el, "id")
    out = []
    for pl in team_el.findall("player"):
        row: dict[str, Any] = {
            "team_abbr":     team_abbr,
            "player_code":   _attr_str(pl, "code"),
            "uniform":       _attr_str(pl, "uni"),
            "full_name":     _attr_str(pl, "name"),
            "position":      _attr_str(pl, "pos"),
            "games_started": _attr_int(pl, "gs"),
            "games_played":  _attr_int(pl, "gp"),
        }
        # Passing
        pa = pl.find("pass")
        if pa is not None:
            row["pass_comp"] = _attr_int(pa, "comp")
            row["pass_att"] = _attr_int(pa, "att")
            row["pass_yds"] = _attr_int(pa, "yds")
            row["pass_td"] = _attr_int(pa, "td")
            row["pass_int"] = _attr_int(pa, "int")
            row["pass_long"] = _attr_int(pa, "long")
            row["pass_sacks"] = _attr_int(pa, "sacks")
            row["pass_sack_yds"] = _attr_int(pa, "sackyds")
            row["pass_rating"] = _attr_num(pa, "rating")
            row["pass_drops"] = _attr_int(pa, "drops")
            row["pass_throwaways"] = _attr_int(pa, "throwaways")
        # Rushing
        ru = pl.find("rush")
        if ru is not None:
            row["rush_att"] = _attr_int(ru, "att")
            row["rush_yds"] = _attr_int(ru, "yds")
            row["rush_gain"] = _attr_int(ru, "gain")
            row["rush_loss"] = _attr_int(ru, "loss")
            row["rush_td"] = _attr_int(ru, "td")
            row["rush_long"] = _attr_int(ru, "long")
            row["rush_redzone"] = _attr_int(ru, "redzone")
            row["rush_broken"] = _attr_int(ru, "broken")
        # Receiving
        rcv = pl.find("rcv")
        if rcv is not None:
            row["rcv_no"] = _attr_int(rcv, "no")
            row["rcv_yds"] = _attr_int(rcv, "yds")
            row["rcv_td"] = _attr_int(rcv, "td")
            row["rcv_long"] = _attr_int(rcv, "long")
            row["rcv_yac"] = _attr_int(rcv, "sb-yac")
            row["rcv_targets"] = _attr_int(rcv, "tgt")
        # Defense
        df = pl.find("defense")
        if df is not None:
            row["def_solo"] = _attr_int(df, "tackua")
            row["def_assist"] = _attr_int(df, "tacka")
            row["def_total"] = _attr_int(df, "tot_tack")
            row["def_tfl"] = _attr_num(df, "tflua")
            row["def_tfl_yds"] = _attr_num(df, "tflyds")
            row["def_sacks"] = _attr_num(df, "sackua")
            row["def_sack_yds"] = _attr_num(df, "sackyds")
            row["def_brup"] = _attr_int(df, "brup")
            row["def_int"] = _attr_int(df, "int")
            row["def_int_yds"] = _attr_int(df, "intyds")
            row["def_ff"] = _attr_int(df, "ff")
            row["def_fr"] = _attr_int(df, "fr")
            row["def_fr_yds"] = _attr_int(df, "fryds")
            row["def_blocked"] = _attr_int(df, "blkd")
        # Returns
        for tag, prefix in [("kr", "kr"), ("pr", "pr"), ("ir", "ir"), ("fr", "fr")]:
            ret = pl.find(tag)
            if ret is not None:
                row[f"{prefix}_no"] = _attr_int(ret, "no")
                row[f"{prefix}_yds"] = _attr_int(ret, "yds")
                row[f"{prefix}_long"] = _attr_int(ret, "long")
                row[f"{prefix}_td"] = _attr_int(ret, "td")
        # Kicking
        ko = pl.find("ko")
        if ko is not None:
            row["ko_no"] = _attr_int(ko, "no")
            row["ko_yds"] = _attr_int(ko, "yds")
        fg = pl.find("fg")
        if fg is not None:
            row["fg_made"] = _attr_int(fg, "made")
            row["fg_att"] = _attr_int(fg, "att")
            row["fg_long"] = _attr_int(fg, "long")
        pat = pl.find("pat")
        if pat is not None:
            row["pat_kick_made"] = _attr_int(pat, "kickmade")
            row["pat_kick_att"] = _attr_int(pat, "kickatt")
        # Punting
        pu = pl.find("punt")
        if pu is not None:
            row["punt_no"] = _attr_int(pu, "no")
            row["punt_yds"] = _attr_int(pu, "yds")
            row["punt_long"] = _attr_int(pu, "long")
            row["punt_inside20"] = _attr_int(pu, "inside20")
        # Penalties
        pen = pl.find("pen")
        if pen is not None:
            row["pen_no"] = _attr_int(pen, "no")
            row["pen_yds"] = _attr_int(pen, "yds")
        # Scoring rollup
        sc = pl.find("scoring")
        if sc is not None:
            tds = _attr_int(sc, "td") or 0
            fgs = _attr_int(sc, "fg") or 0
            fg4s = _attr_int(sc, "fg4") or 0
            pat_kick = _attr_int(sc, "patkick") or 0
            row["points_scored"] = tds * 6 + fgs * 3 + fg4s * 4 + pat_kick * 1

        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Top-level: parse one game's XML into a structured dict
# ---------------------------------------------------------------------------
def parse_game(xml_text: str, game_id: Optional[str] = None) -> dict[str, Any]:
    """Parse a complete StatBroadcast game XML into a single dict."""
    root = ET.fromstring(xml_text)
    teams = root.findall("team")

    # Identify home/away
    home_team = next((t for t in teams if (_attr_str(t, "vh") or "").upper() == "H"), None)
    away_team = next((t for t in teams if (_attr_str(t, "vh") or "").upper() == "V"), None)

    return {
        "game_id":        game_id,
        "venue":          parse_venue(root),
        "rules":          parse_rules(root),
        "officials":      parse_officials(root),
        "home_totals":    parse_team_totals(home_team) if home_team is not None else {},
        "away_totals":    parse_team_totals(away_team) if away_team is not None else {},
        "home_players":   parse_players(home_team) if home_team is not None else [],
        "away_players":   parse_players(away_team) if away_team is not None else [],
        "drives":         parse_drives(root),
        "scoring_plays":  parse_scoring_plays(root),
        "fgas":           parse_fgas(root),
        "plays":          parse_plays(root),
    }


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------
def fetch_and_parse(game_id: str, cache_dir: Optional[Path] = None) -> dict[str, Any]:
    xml_text = fetch_game_xml(game_id, cache_dir=cache_dir)
    return parse_game(xml_text, game_id=game_id)


def discover_recent_game_ids() -> list[str]:
    """Pull the StatBroadcast UFL hub and return the recent game IDs found."""
    return parse_game_ids(fetch_hub_html())


def discover_all_archived_game_ids() -> list[str]:
    """Pull the historical archive page — every UFL game ever recorded."""
    return parse_game_ids(fetch_archive_html())
