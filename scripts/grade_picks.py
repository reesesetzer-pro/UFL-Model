"""
scripts/grade_picks.py — Backtest the UFL model against completed games.

Loads every parsed game in `data/parsed/`, runs the model's projector against
the latest ratings snapshot, and writes a per-game residuals table to
`data/calibration.csv`. The dashboard's Calibration tab reads this file.

Usage:
    python scripts/grade_picks.py
    python scripts/grade_picks.py --as-of 2026-05-02   # use older snapshot

Notes
-----
This is an *in-sample* backtest — the ratings snapshot was fit using these
same games, so it understates true error. Once we have ≥3 weekly snapshots
per game (mid-May), upgrade to walk-forward (predict each week using only
prior-week ratings).

Output schema:
  game_id, week, date, home, away, actual_h, actual_a,
  pred_h, pred_a, actual_margin, pred_margin, margin_error,
  actual_total, pred_total, total_error, home_won_pred, home_won_actual,
  margin_ats_hit  (would the model have covered against its own line as if it were the market)
"""
from __future__ import annotations
import argparse
import csv
import json
import sys
from datetime import date as _date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.model.projector import project_game, GameProjection
from src.model.elo import TeamElo


def _load_ratings(as_of: str | None = None):
    rdir = ROOT / "data" / "ratings"
    snaps = sorted(rdir.glob("snapshot_*.json"))
    if not snaps:
        raise SystemExit(f"No ratings snapshots in {rdir}")
    if as_of:
        target = rdir / f"snapshot_{as_of}.json"
        if not target.exists():
            raise SystemExit(f"No snapshot for {as_of}; latest is {snaps[-1].name}")
        path = target
    else:
        path = snaps[-1]
    with path.open() as f:
        data = json.load(f)
    elo = {team: TeamElo(code=team, rating=float(r), games=int(data["elo_games_played"].get(team, 0)))
           for team, r in data["elo"].items()}
    return elo, data["ppd_adj"], path.stem


def _load_games() -> list[dict]:
    pdir = ROOT / "data" / "parsed"
    games = []
    for f in sorted(pdir.glob("*.json")):
        with f.open() as h:
            g = json.load(h)
        if g.get("home_totals") and g.get("away_totals") \
                and g["home_totals"].get("linescore_total") is not None \
                and g["away_totals"].get("linescore_total") is not None:
            games.append(g)
    return games


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=None, help="Ratings snapshot date (YYYY-MM-DD)")
    args = ap.parse_args()

    elo, ppd_adj, snap_label = _load_ratings(args.as_of)
    games = _load_games()
    print(f"[grade] {len(games)} parsed games | ratings: {snap_label}")

    rows = []
    margin_abs_errs = []
    total_abs_errs = []
    margin_signed = []
    total_signed = []
    ml_correct = 0
    ml_total = 0
    ats_correct = 0
    ats_eligible = 0

    for g in games:
        h = g["home"]; a = g["away"]
        if h not in elo or a not in elo:
            print(f"  [skip] {g['game_id']} missing rating: {h}/{a}")
            continue
        pred = project_game(h, a, elo, ppd_adj)
        actual_h = float(g["home_totals"]["linescore_total"])
        actual_a = float(g["away_totals"]["linescore_total"])
        actual_margin = actual_h - actual_a
        actual_total = actual_h + actual_a

        # `pred.spread` uses market convention (negative = home favored).
        # Convert to home-margin convention to compare against actual_margin.
        pred_home_margin = pred.home_score_proj - pred.away_score_proj
        margin_err = pred_home_margin - actual_margin   # + means model overpredicts home margin
        total_err = pred.total - actual_total

        # ML accuracy: did the model's favored side actually win?
        home_pred = pred_home_margin > 0
        home_won = actual_margin > 0
        if home_pred == home_won:
            ml_correct += 1
        ml_total += 1

        # "ATS" against the model's own line — sanity check, not real ATS
        if abs(pred_home_margin) > 0.5:
            home_covered = actual_margin > pred_home_margin
            home_pred_to_cover = pred_home_margin > 0
            if home_covered == home_pred_to_cover:
                ats_correct += 1
            ats_eligible += 1

        rows.append({
            "game_id":          g["game_id"],
            "week":             g.get("week"),
            "date":             g.get("game_date"),
            "home":             h,
            "away":             a,
            "actual_h":         actual_h,
            "actual_a":         actual_a,
            "pred_h":           round(pred.home_score_proj, 2),
            "pred_a":           round(pred.away_score_proj, 2),
            "actual_margin":    actual_margin,
            "pred_margin":      round(pred_home_margin, 2),
            "margin_error":     round(margin_err, 2),
            "actual_total":     actual_total,
            "pred_total":       round(pred.total, 2),
            "total_error":      round(total_err, 2),
            "home_pred":        home_pred,
            "home_won":         home_won,
            "ml_correct":       home_pred == home_won,
        })
        margin_abs_errs.append(abs(margin_err))
        total_abs_errs.append(abs(total_err))
        margin_signed.append(margin_err)
        total_signed.append(total_err)

    # Write CSV
    out_path = ROOT / "data" / "calibration.csv"
    if rows:
        with out_path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"[grade] wrote {out_path}")

    # Summary
    n = len(rows)
    if n == 0:
        print("[grade] no graded games (rating mismatch?)")
        return
    print()
    print("=" * 56)
    print(f"  UFL backtest summary  ({n} games, ratings={snap_label})")
    print("=" * 56)
    print(f"  Margin MAE:        {sum(margin_abs_errs)/n:6.2f} pts")
    print(f"  Margin bias:       {sum(margin_signed)/n:+6.2f} pts  (+ = model over-favors home)")
    print(f"  Total MAE:         {sum(total_abs_errs)/n:6.2f} pts")
    print(f"  Total bias:        {sum(total_signed)/n:+6.2f} pts  (+ = model over-projects total)")
    print(f"  ML accuracy:       {ml_correct}/{ml_total} = {100*ml_correct/ml_total:.1f}%")
    if ats_eligible:
        print(f"  Self-line ATS:     {ats_correct}/{ats_eligible} = {100*ats_correct/ats_eligible:.1f}%  (in-sample, sanity check)")

    # σ guidance — observed residual std vs current SIGMA_TOTAL/SIGMA_MARGIN
    import statistics
    if n >= 5:
        sd_m = statistics.pstdev(margin_signed)
        sd_t = statistics.pstdev(total_signed)
        from src.model.projector import SIGMA_MARGIN, SIGMA_TOTAL
        print()
        print(f"  σ_margin observed: {sd_m:5.2f}  (current setting: {SIGMA_MARGIN})")
        print(f"  σ_total  observed: {sd_t:5.2f}  (current setting: {SIGMA_TOTAL})")
        print("  (Observed σ is in-sample — true σ likely 1.1–1.3× this.)")


if __name__ == "__main__":
    main()
