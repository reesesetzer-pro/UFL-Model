"""
Slate generation pipeline. Produces a complete betting card for any upcoming
slate (Friday-Sunday in regular season, single days in playoffs).

For each upcoming game:
  1. Read latest team ratings snapshot (from weekly_update)
  2. Read latest odds snapshot (from daily_odds_snap)
  3. Project full-game spread + total + win prob (model side)
  4. Devig odds across approved books -> consensus market probs
  5. Bayesian-blend model with market based on games_played
  6. Compute edges + Kelly stakes for:
       - moneyline  (home + away)
       - spread     (home + away at consensus number)
       - total      (over + under at consensus number)
       - 1H spread  (derived)
       - 1H total   (derived)
       - team total (each team)
  7. Filter to candidates that pass edge thresholds + sizing > 0
  8. Write slate_{YYYYMMDD}.json + summary CSV
  9. (Optional) write to Supabase predictions + edge_log
"""
from __future__ import annotations
import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ImportError:
    pass

from src.data.schedule import (
    upcoming_games, schedule_by_id, games_played_through, all_team_codes,
    GameSlot,
)
from src.model.elo import (
    default_2026_starting_ratings, compute_elo_history, TeamElo,
    HFA_ELO, ELO_PER_POINT, expected_win_prob, elo_to_spread,
)
from src.model.efficiency import (
    rollup_team_game, opponent_adjusted_ppd, DEFAULT_TOTAL_DRIVES,
)
from src.model.projector import (
    project_game, cover_prob, total_prob_over, home_ml_prob,
    LEAGUE_MU_PPD, EFFICIENCY_HFA_PPD, ELO_BLEND_WEIGHT,
    SIGMA_TOTAL, SIGMA_MARGIN,
)
from src.model.derived_markets import (
    derive_lines, h1_cover_prob, h1_total_over_prob, team_total_over_prob,
    SIGMA_1H_TOTAL, SIGMA_1H_MARGIN, SIGMA_TEAM_TOTAL,
)
from src.model.prior_blend import blend, model_weight
from src.edge.no_vig import (
    consensus_no_vig, MarketLine, american_to_implied,
    implied_to_american, decimal_to_american, american_to_decimal,
)
from src.edge.edge_calc import evaluate_market, BetCandidate, stake_units


PARSED_DIR = Path("data/parsed")
RATINGS_DIR = Path("data/ratings")
ODDS_DIR = Path("data/odds")
SLATE_DIR = Path("data/slates")


# --------------------------------------------------------------------------
# Latest snapshots

def _latest(path: Path, suffix: str = ".json") -> Optional[Path]:
    if not path.exists():
        return None
    files = sorted(path.glob(f"*{suffix}"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def load_latest_ratings_snapshot() -> dict:
    p = _latest(RATINGS_DIR)
    if not p:
        raise SystemExit("No ratings snapshot found. Run weekly_update first.")
    with p.open() as f:
        return json.load(f)


def load_latest_odds_csv() -> list[dict]:
    p = _latest(ODDS_DIR, suffix=".csv")
    if not p:
        return []
    with p.open() as f:
        return _expand_wide_odds(list(csv.DictReader(f)))


def _expand_wide_odds(rows: list[dict]) -> list[dict]:
    """flatten_odds writes one row per (game,book,market) with home/away/over/
    under prices columns. build_market_consensus wants one row per side with
    `name` + `price`. Expand here."""
    long_rows: list[dict] = []
    for r in rows:
        market = r.get("market")
        common = {
            "event_id": r.get("game_id") or r.get("id") or r.get("event_id"),
            "home_team": r.get("home_team"),
            "away_team": r.get("away_team"),
            "commence_time": r.get("commence_time"),
            "book": r.get("book"),
            "market_key": market,
            "last_update": r.get("last_update"),
        }
        point = r.get("point")
        if market == "h2h":
            if r.get("home_price") not in (None, ""):
                long_rows.append({**common, "name": r["home_team"], "price": r["home_price"], "point": None})
            if r.get("away_price") not in (None, ""):
                long_rows.append({**common, "name": r["away_team"], "price": r["away_price"], "point": None})
        elif market == "spreads":
            # Both sides share the SAME point so build_market_consensus can
            # pair them in by_book_point groups. The "which team gets which
            # spread" is determined by the side name; the point itself is the
            # home-side spread (negative = home favorite).
            # Previously: away point was negated, which split the group and
            # left both sides with no pair — spreads never made it to the
            # consensus dict, model never generated a spread pick.
            if r.get("home_price") not in (None, ""):
                long_rows.append({**common, "name": r["home_team"], "price": r["home_price"], "point": point})
            if r.get("away_price") not in (None, ""):
                long_rows.append({**common, "name": r["away_team"], "price": r["away_price"], "point": point})
        elif market == "totals":
            if r.get("over_price") not in (None, ""):
                long_rows.append({**common, "name": "Over", "price": r["over_price"], "point": point})
            if r.get("under_price") not in (None, ""):
                long_rows.append({**common, "name": "Under", "price": r["under_price"], "point": point})
    return long_rows


# --------------------------------------------------------------------------
# Build market consensus from flattened odds

def build_market_consensus(odds_rows: list[dict]) -> dict:
    """
    Returns {commence_time: {home_team, away_team, markets: {market_key: ...}}}
    Each market_key has consensus probs and best prices per side.
    """
    games: dict[str, dict] = {}
    by_event_market: dict = defaultdict(list)
    for r in odds_rows:
        event_id = r.get("event_id") or r.get("id")
        market = r.get("market_key") or r.get("market")
        side = r.get("name") or r.get("outcome_name")  # name of outcome
        book = r.get("book") or r.get("bookmaker")
        price = r.get("price") or r.get("american_odds")
        try:
            price = float(price)
        except Exception:
            continue
        by_event_market[(event_id, market)].append({
            "book": book, "side": side, "price": price,
            "point": _maybe_float(r.get("point")),
            "home_team": r.get("home_team"),
            "away_team": r.get("away_team"),
            "commence_time": r.get("commence_time"),
        })

    # Pivot: each (event, market) -> list of MarketLine objects
    consensus: dict = {}
    for (event_id, market), entries in by_event_market.items():
        # Group by (book, point) to find the two-way pair
        by_book_point: dict = defaultdict(dict)
        for e in entries:
            key = (e["book"], e.get("point"))
            by_book_point[key][e["side"]] = e

        if not entries:
            continue
        side_a_label, side_b_label = _identify_sides(entries[0])
        if side_a_label is None:
            continue

        lines: list[MarketLine] = []
        for (book, point), sides in by_book_point.items():
            a = sides.get(side_a_label)
            b = sides.get(side_b_label)
            if not a or not b:
                continue
            try:
                lines.append(MarketLine(
                    book=book,
                    side_a=a["price"], side_b=b["price"],
                    label_a=side_a_label, label_b=side_b_label,
                ))
            except Exception:
                continue

        if not lines:
            continue
        cons = consensus_no_vig(lines, method="power")
        if cons is None:
            continue

        consensus.setdefault(event_id, {
            "home_team": entries[0].get("home_team"),
            "away_team": entries[0].get("away_team"),
            "commence_time": entries[0].get("commence_time"),
            "markets": {},
        })

        # Best prices per side
        best_a = max(lines, key=lambda l: american_to_decimal(l.side_a))
        best_b = max(lines, key=lambda l: american_to_decimal(l.side_b))

        consensus[event_id]["markets"][market] = {
            "side_a_label": side_a_label,
            "side_b_label": side_b_label,
            "consensus_prob_a": cons[side_a_label],
            "consensus_prob_b": cons[side_b_label],
            "n_books": cons["n_books"],
            "books_used": cons["books_used"],
            "consensus_point": _consensus_point(by_book_point),
            "best_price_a": {"book": best_a.book, "odds": best_a.side_a},
            "best_price_b": {"book": best_b.book, "odds": best_b.side_b},
        }

    return consensus


def _maybe_float(x):
    try:
        return float(x)
    except Exception:
        return None


def _identify_sides(entry: dict) -> tuple[Optional[str], Optional[str]]:
    """Determine the two outcome labels for a market based on a sample row."""
    side = entry.get("side")
    home = entry.get("home_team")
    away = entry.get("away_team")
    if not side:
        return None, None
    s = str(side).strip().lower()
    if s in ("over", "under"):
        return "Over", "Under"
    if home and side == home:
        return home, away
    if away and side == away:
        return home, away
    return None, None


def _consensus_point(by_book_point: dict) -> Optional[float]:
    points = [k[1] for k in by_book_point if k[1] is not None]
    if not points:
        return None
    return round(sum(points) / len(points), 2)


# --------------------------------------------------------------------------
# Slate construction

def build_slate(as_of: Optional[date] = None,
                days_ahead: int = 4,
                bankroll: float = 1000.0) -> dict:
    as_of = as_of or date.today()
    SLATE_DIR.mkdir(parents=True, exist_ok=True)

    ratings = load_latest_ratings_snapshot()
    odds_rows = load_latest_odds_csv()
    consensus = build_market_consensus(odds_rows) if odds_rows else {}

    upcoming = upcoming_games(as_of, days_ahead=days_ahead)
    print(f"[slate] {len(upcoming)} games in next {days_ahead}d (as_of={as_of})")

    games_played = ratings["n_games"]
    weight = model_weight(games_played)

    # Reconstruct TeamElo objects from snapshot
    elos = {code: TeamElo(code=code, rating=v,
                          games=ratings["elo_games_played"].get(code, 0))
            for code, v in ratings["elo"].items()}
    ppd_adj = {k: v for k, v in ratings["ppd_adj"].items()}

    # ── Weather pull (outdoor stadiums only) ────────────────────────────────
    # Forecast each upcoming game's kickoff conditions and convert to a
    # scoring multiplier. Dome games (only STL) get neutral 1.0.
    from src.data.schedule import STADIUM_COORDS, TEAMS
    try:
        # Shared weather util at the repo root
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
        from weather import fetch_forecast, football_scoring_multiplier
        _weather_available = True
    except Exception as e:
        print(f"[slate] weather util unavailable: {e}")
        _weather_available = False

    weather_by_sb_id: dict[int, dict] = {}
    if _weather_available:
        for slot in upcoming:
            home_team = TEAMS.get(slot.home)
            if not home_team or home_team.indoor:
                weather_by_sb_id[slot.sb_id] = {"mult": 1.0, "summary": "indoor"}
                continue
            coords = STADIUM_COORDS.get(slot.home)
            if not coords:
                weather_by_sb_id[slot.sb_id] = {"mult": 1.0, "summary": "no coords"}
                continue
            kickoff = datetime.combine(slot.date, datetime.min.time()).replace(hour=20)  # default 8pm ET
            forecast = fetch_forecast(coords[0], coords[1], kickoff)
            if not forecast:
                weather_by_sb_id[slot.sb_id] = {"mult": 1.0, "summary": "fetch failed"}
                continue
            mult = football_scoring_multiplier(
                forecast["temp_f"], forecast["wind_mph"], forecast["precip_pct"]
            )
            summary = (f"{forecast['temp_f']}°F · {forecast['wind_mph']}mph "
                       f"{forecast['wind_dir']} · {forecast['precip_pct']}% precip")
            weather_by_sb_id[slot.sb_id] = {"mult": mult, "summary": summary,
                                            "raw": forecast}
        print(f"[slate] weather fetched for {len(weather_by_sb_id)} games")

    out_games = []
    candidates: list[BetCandidate] = []
    for slot in upcoming:
        wx = weather_by_sb_id.get(slot.sb_id, {"mult": 1.0, "summary": ""})
        proj = project_game(slot.home, slot.away, elos, ppd_adj,
                            weather_mult=wx["mult"], weather_summary=wx["summary"])
        derived = derive_lines(slot.home, slot.away,
                               model_spread_home=proj.spread,
                               model_total=proj.total)

        # Match consensus to this game by team names if possible
        market = _match_consensus_for_game(consensus, slot)

        bets = _evaluate_all_markets(slot, proj, derived, market, games_played)
        candidates.extend(bets)

        out_games.append({
            "sb_id": slot.sb_id,
            "week": slot.week,
            "date": slot.date.isoformat(),
            "home": slot.home, "away": slot.away,
            "model": {
                "spread": proj.spread, "total": proj.total,
                "home_score": proj.home_score_proj,
                "away_score": proj.away_score_proj,
                "home_wp": proj.home_win_prob,
                "components": proj.components,
            },
            "derived": {
                "h1_spread": derived.h1_spread_home,
                "h1_total": derived.h1_total,
                "home_team_total": derived.home_team_total,
                "away_team_total": derived.away_team_total,
            },
            "market": market,
            "bayes_weight": weight,
            "candidates": [c.__dict__ for c in bets],
        })

    keep = [c for c in candidates if c.passes_threshold]
    keep.sort(key=lambda c: c.edge_prob, reverse=True)
    print(f"[slate] {len(keep)}/{len(candidates)} candidates pass thresholds")
    for c in keep:
        units = stake_units(bankroll, c.stake_pct)
        print(f"  ✓ {c.label:<22} {c.american_odds:>6} @ {c.book:<10} "
              f"edge={c.edge_prob*100:+.2f}pp size=${units:.2f}")

    payload = {
        "as_of": as_of.isoformat(),
        "weight_used": weight,
        "games": out_games,
        "n_candidates_passing": len(keep),
        "n_total_candidates": len(candidates),
    }
    out_path = SLATE_DIR / f"slate_{as_of.isoformat()}.json"
    with out_path.open("w") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"\n[slate] wrote {out_path}")
    return payload


def _match_consensus_for_game(consensus: dict,
                              slot: GameSlot) -> Optional[dict]:
    """Look for an event whose home/away match this game by name fragment."""
    if not consensus:
        return None
    from src.data.schedule import TEAMS, to_code
    target_home = TEAMS[slot.home].full_name.lower()
    target_away = TEAMS[slot.away].full_name.lower()
    for evt_id, evt in consensus.items():
        h = (evt.get("home_team") or "").lower()
        a = (evt.get("away_team") or "").lower()
        if (target_home in h or h in target_home) and \
           (target_away in a or a in target_away):
            return evt
    return None


def _evaluate_all_markets(slot: GameSlot, proj, derived, market: Optional[dict],
                          games_played: int) -> list[BetCandidate]:
    """Iterate through h2h, spreads, totals → BetCandidate list."""
    out: list[BetCandidate] = []
    if not market:
        return out
    weight = model_weight(games_played)
    sigma_margin = proj.sigma_margin

    # Helper to grab consensus prob for a side label
    def cprob(mk: str, label_pos: str) -> Optional[float]:
        m = market["markets"].get(mk)
        if not m:
            return None
        if label_pos == "a":
            return m["consensus_prob_a"]
        return m["consensus_prob_b"]

    def bestp(mk: str, label_pos: str) -> Optional[dict]:
        m = market["markets"].get(mk)
        if not m:
            return None
        return m.get("best_price_a") if label_pos == "a" else m.get("best_price_b")

    def cpoint(mk: str) -> Optional[float]:
        m = market["markets"].get(mk)
        return m.get("consensus_point") if m else None

    # ---- h2h (moneyline) ----
    if "h2h" in market["markets"]:
        # market label_a is home_team
        market_p_home = cprob("h2h", "a") or 0.5
        # Blend our model home_wp with market in prob space via blend on spread
        # then converting back. Simpler: blend probabilities by same weight.
        mw = weight
        blended_p_home = mw * proj.home_win_prob + (1 - mw) * market_p_home
        # Home ML
        bp_h = bestp("h2h", "a")
        if bp_h:
            out.append(evaluate_market(
                label=f"{slot.home} ML",
                market="moneyline", side="home_ml",
                p_model=blended_p_home, p_market=market_p_home,
                book=bp_h["book"], american_odds=bp_h["odds"],
            ))
        # Away ML
        bp_a = bestp("h2h", "b")
        market_p_away = cprob("h2h", "b") or (1 - market_p_home)
        if bp_a:
            out.append(evaluate_market(
                label=f"{slot.away} ML",
                market="moneyline", side="away_ml",
                p_model=1 - blended_p_home, p_market=market_p_away,
                book=bp_a["book"], american_odds=bp_a["odds"],
            ))

    # ---- Spreads ----
    if "spreads" in market["markets"]:
        market_spread_home = cpoint("spreads")
        if market_spread_home is not None:
            blended_spread = blend(market_spread=market_spread_home,
                                   market_total=proj.total,
                                   model_spread=proj.spread,
                                   model_total=proj.total,
                                   games_played=games_played).blended_spread
            p_home_cover = cover_prob(market_spread=market_spread_home,
                                      model_spread=blended_spread,
                                      sigma_margin=sigma_margin)
            mp_home_cover = cprob("spreads", "a") or 0.5
            bp = bestp("spreads", "a")
            if bp:
                out.append(evaluate_market(
                    label=f"{slot.home} {market_spread_home:+.1f}",
                    market="spread", side="home_spread",
                    p_model=p_home_cover, p_market=mp_home_cover,
                    book=bp["book"], american_odds=bp["odds"],
                ))
            bp2 = bestp("spreads", "b")
            mp_away_cover = cprob("spreads", "b") or (1 - mp_home_cover)
            if bp2:
                out.append(evaluate_market(
                    label=f"{slot.away} {-market_spread_home:+.1f}",
                    market="spread", side="away_spread",
                    p_model=1 - p_home_cover, p_market=mp_away_cover,
                    book=bp2["book"], american_odds=bp2["odds"],
                ))

    # ---- Totals ----
    if "totals" in market["markets"]:
        market_total = cpoint("totals")
        if market_total is not None:
            blended_total = blend(market_spread=proj.spread,
                                  market_total=market_total,
                                  model_spread=proj.spread,
                                  model_total=proj.total,
                                  games_played=games_played).blended_total
            p_over = total_prob_over(market_total, blended_total)
            mp_over = cprob("totals", "a") or 0.5  # depends on side label order
            bp = bestp("totals", "a")
            if bp:
                out.append(evaluate_market(
                    label=f"Over {market_total:.1f}",
                    market="total", side="over",
                    p_model=p_over, p_market=mp_over,
                    book=bp["book"], american_odds=bp["odds"],
                ))
            bp2 = bestp("totals", "b")
            if bp2:
                out.append(evaluate_market(
                    label=f"Under {market_total:.1f}",
                    market="total", side="under",
                    p_model=1 - p_over, p_market=1 - mp_over,
                    book=bp2["book"], american_odds=bp2["odds"],
                ))

    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--as-of", type=str)
    p.add_argument("--days-ahead", type=int, default=4)
    p.add_argument("--bankroll", type=float, default=1000.0)
    args = p.parse_args()
    as_of = (date.fromisoformat(args.as_of) if args.as_of else None)
    build_slate(as_of=as_of, days_ahead=args.days_ahead, bankroll=args.bankroll)
