"""
scripts/grade_bets.py — Grade UFL passing-threshold picks from slate JSONs
against actual final scores in data/parsed/.

This is the OPERATIONAL grader (separate from scripts/grade_picks.py, which
is the calibration backtester for residuals). It reads every daily slate
written by prediction_run.py, finds picks marked passes_threshold=True, and
checks the actual outcome from the parsed StatBroadcast game JSON.

Picks are deduplicated by (game_date, home, away, market, side, line) so the
same upcoming game appearing in 5 consecutive daily slates only counts once.

Output:
  data/graded_bets.csv  — one row per unique pick with W/L/P + ROI + edge
  prints lifetime W-L-P + ROI broken out by market

Usage:
  python scripts/grade_bets.py
  python scripts/grade_bets.py --since 2026-05-01   # only grade picks from a date onward
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SLATES_DIR = ROOT / "data" / "slates"
PARSED_DIR = ROOT / "data" / "parsed"
OUTPUT_CSV = ROOT / "data" / "graded_bets.csv"


def _parse_line_from_label(label: str) -> float | None:
    """`Over 46.5` → 46.5; `BHM ML` → None."""
    m = re.search(r"\b(\d+(?:\.\d+)?)\b", label or "")
    return float(m.group(1)) if m else None


def _profit_per_unit(american_odds: float, result: str) -> float:
    if result == "WIN":
        return american_odds / 100.0 if american_odds > 0 else 100.0 / abs(american_odds)
    if result == "LOSS":
        return -1.0
    return 0.0  # PUSH / PENDING


def _load_parsed_games() -> dict[tuple[str, str, str], dict]:
    """index by (game_date, home, away) → final score data."""
    idx: dict[tuple[str, str, str], dict] = {}
    if not PARSED_DIR.exists():
        return idx
    for fp in PARSED_DIR.glob("*.json"):
        try:
            d = json.loads(fp.read_text())
        except Exception:
            continue
        gd = d.get("game_date") or ""
        home = d.get("home") or ""
        away = d.get("away") or ""
        home_score = d.get("home_totals", {}).get("linescore_total")
        away_score = d.get("away_totals", {}).get("linescore_total")
        if home and away and gd and home_score is not None and away_score is not None:
            idx[(gd, home, away)] = {
                "home_score": int(home_score),
                "away_score": int(away_score),
                "game_id": d.get("game_id"),
            }
    return idx


def _grade(market: str, side: str, line: float | None,
           home_score: int, away_score: int) -> str:
    """Return WIN / LOSS / PUSH."""
    if market == "moneyline":
        if side == "home_ml":
            return "WIN" if home_score > away_score else ("PUSH" if home_score == away_score else "LOSS")
        if side == "away_ml":
            return "WIN" if away_score > home_score else ("PUSH" if home_score == away_score else "LOSS")
    if market == "spread":
        if line is None:
            return "PUSH"
        home_margin = home_score - away_score
        # Convention: `line` from label is for the side bet (e.g., "HOU -3.5" → home gets -3.5,
        # so home_margin must EXCEED 3.5 for a home -3.5 win).
        if side == "home_spread":
            adj = home_margin + line  # line is negative for fav, positive for dog from home's POV
            return "WIN" if adj > 0 else ("PUSH" if adj == 0 else "LOSS")
        if side == "away_spread":
            adj = -home_margin + line
            return "WIN" if adj > 0 else ("PUSH" if adj == 0 else "LOSS")
    if market == "total":
        if line is None:
            return "PUSH"
        total = home_score + away_score
        if side == "over":
            return "WIN" if total > line else ("PUSH" if total == line else "LOSS")
        if side == "under":
            return "WIN" if total < line else ("PUSH" if total == line else "LOSS")
    return "PUSH"  # unknown market


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None, help="ISO date — only grade slates from this date onward")
    args = ap.parse_args()

    parsed = _load_parsed_games()
    print(f"[grade] loaded {len(parsed)} parsed games with final scores")

    # Walk every slate, collect passing candidates, dedupe by upcoming-game key
    unique_picks: dict[tuple, dict] = {}
    for sf in sorted(SLATES_DIR.glob("*.json")):
        try:
            slate = json.loads(sf.read_text())
        except Exception:
            continue
        for g in slate.get("games", []):
            gd = g.get("date") or ""
            if args.since and gd < args.since:
                continue
            home, away = g.get("home"), g.get("away")
            for c in (g.get("candidates") or []):
                if not c.get("passes_threshold"):
                    continue
                line = _parse_line_from_label(c.get("label", ""))
                key = (gd, home, away, c.get("market"), c.get("side"), line)
                # Keep the earliest slate's pick (closest to opening line) — that's
                # the truest test of model-vs-market.
                if key not in unique_picks:
                    unique_picks[key] = {
                        "slate_date": slate.get("as_of", sf.stem),
                        "game_date": gd, "home": home, "away": away,
                        "label": c.get("label"), "market": c.get("market"),
                        "side": c.get("side"), "line": line,
                        "book": c.get("book"), "odds": c.get("american_odds"),
                        "p_model": c.get("p_model"), "p_market": c.get("p_market"),
                        "edge_prob": c.get("edge_prob"), "edge_ev": c.get("edge_ev"),
                        "stake_pct": c.get("stake_pct"),
                    }

    # Grade each unique pick
    rows = []
    for key, pick in unique_picks.items():
        match = parsed.get((pick["game_date"], pick["home"], pick["away"]))
        if match:
            result = _grade(pick["market"], pick["side"], pick["line"],
                            match["home_score"], match["away_score"])
            pick["home_score"] = match["home_score"]
            pick["away_score"] = match["away_score"]
        else:
            result = "PENDING"
        pick["result"] = result
        pick["pnl"] = round(_profit_per_unit(pick["odds"] or 0, result), 4)
        rows.append(pick)

    # Write CSV
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    cols = ["slate_date", "game_date", "home", "away", "label", "market", "side",
            "line", "book", "odds", "p_model", "p_market", "edge_prob", "edge_ev",
            "stake_pct", "home_score", "away_score", "result", "pnl"]
    with OUTPUT_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})

    # Print summary
    settled = [r for r in rows if r["result"] in ("WIN", "LOSS", "PUSH")]
    pending = [r for r in rows if r["result"] == "PENDING"]
    print(f"[grade] {len(rows)} unique picks: {len(settled)} settled, {len(pending)} pending")
    print()
    print(f"{'Market':12} {'N':>4} {'W-L-P':>10} {'Win%':>7} {'$/$1':>8}")
    by_mkt: dict[str, list[dict]] = defaultdict(list)
    for r in settled:
        by_mkt[r["market"] or ""].append(r)
    for mkt in sorted(by_mkt):
        grp = by_mkt[mkt]
        w = sum(1 for r in grp if r["result"] == "WIN")
        l = sum(1 for r in grp if r["result"] == "LOSS")
        p = sum(1 for r in grp if r["result"] == "PUSH")
        n_dec = w + l
        win_pct = (w / n_dec * 100) if n_dec else 0
        avg_pnl = (sum(r["pnl"] for r in grp) / n_dec * 100) if n_dec else 0
        print(f"{mkt:12} {len(grp):>4} {w}-{l}-{p:>4} {win_pct:>6.1f}% {avg_pnl:>+7.1f}%")
    if settled:
        total_pnl = sum(r["pnl"] for r in settled)
        total_n = sum(1 for r in settled if r["result"] in ("WIN", "LOSS"))
        print(f"\n{'TOTAL':12} {len(settled):>4} {sum(1 for r in settled if r['result']=='WIN')}-"
              f"{sum(1 for r in settled if r['result']=='LOSS')}-{sum(1 for r in settled if r['result']=='PUSH'):>4} "
              f"  {total_pnl/total_n*100 if total_n else 0:+.1f}% lifetime ROI")
    print(f"\nWrote {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
