"""
UFL Model dashboard.

Three tabs:
  1. Slate — upcoming games, model lines, market lines, +EV picks with Kelly
  2. Team Ratings — Elo + opponent-adjusted PPD over time
  3. Calibration — model accuracy vs market by week

Run locally:
    streamlit run streamlit_app.py
Deploy:
    Streamlit Community Cloud, point at this repo's main branch.
"""
from __future__ import annotations
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

# Make src.* imports work when run as `streamlit run streamlit_app.py`
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.data.schedule import (
    SCHEDULE_2026, TEAMS, schedule_by_id, upcoming_games, games_played_through,
    SEASON_START, SEASON_END, PLAYOFFS_START, CHAMPIONSHIP,
)
from src.model.prior_blend import model_weight

# --------------------------------------------------------------------------
_FAVICON = Path(__file__).resolve().parent / "static" / "ufl_favicon.ico"
st.set_page_config(
    page_title="UFL Model",
    page_icon=str(_FAVICON) if _FAVICON.exists() else "🏈",
    layout="wide",
)

DATA_DIR = Path("data")
RATINGS_DIR = DATA_DIR / "ratings"
SLATE_DIR = DATA_DIR / "slates"
ODDS_DIR = DATA_DIR / "odds"


def _latest(p: Path, suffix: str = ".json") -> Path | None:
    """Most-recent file by FILENAME — filenames encode the date
    (e.g. slate_2026-05-21.json). mtime-based sort broke when a rebase
    touched every file with identical timestamps."""
    if not p.exists():
        return None
    files = sorted(p.glob(f"*{suffix}"), key=lambda x: x.name)
    return files[-1] if files else None


def _safe_load_json(p: Path):
    """Load JSON defensively — skip files with merge-conflict markers or other
    corruption. Returns None on failure so callers can try the next-newest."""
    try:
        text = p.read_text()
        if "<<<<<<<" in text or ">>>>>>>" in text:
            return None
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


@st.cache_data(ttl=300)
def load_latest_ratings() -> dict | None:
    if not RATINGS_DIR.exists():
        return None
    for f in sorted(RATINGS_DIR.glob("*.json"), key=lambda x: x.name, reverse=True):
        d = _safe_load_json(f)
        if d is not None:
            return d
    return None


@st.cache_data(ttl=300)
def load_latest_slate() -> dict | None:
    if not SLATE_DIR.exists():
        return None
    for f in sorted(SLATE_DIR.glob("*.json"), key=lambda x: x.name, reverse=True):
        d = _safe_load_json(f)
        if d is not None:
            return d
    return None


@st.cache_data(ttl=120)
def load_latest_odds() -> pd.DataFrame:
    p = _latest(ODDS_DIR, suffix=".csv")
    if not p:
        return pd.DataFrame()
    return pd.read_csv(p)


# --------------------------------------------------------------------------
# Header

st.title("🏈 UFL Betting Model")

ratings = load_latest_ratings()
slate = load_latest_slate()
odds = load_latest_odds()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Today",  date.today().isoformat())
c2.metric("Games played", ratings["n_games"] if ratings else 0)
c3.metric("Current model weight",
          f"{model_weight(ratings['n_games'] if ratings else 0)*100:.0f}%")
c4.metric("Days to championship",
          (CHAMPIONSHIP - date.today()).days)

if ratings is None:
    st.warning("No ratings snapshot found. Run `python src/pipeline/weekly_update.py` first.")
if slate is None:
    st.info("No slate cached yet. Run `python src/pipeline/prediction_run.py` to generate one.")

st.divider()

# --------------------------------------------------------------------------
# Tabs

tab_must, tab_slate, tab_ratings, tab_calib = st.tabs([
    "🎯 MUST TAKE", "📊 Slate", "📈 Team ratings", "🎯 Calibration"])

# ===== Tab 0: MUST TAKE — only +EV passing picks, sorted by EV =============
with tab_must:
    st.header("🎯 Must Take")
    st.caption(
        "All +EV candidates passing the model's edge threshold for the upcoming "
        "slate. Sorted by EV, with ¼-Kelly stake suggestions. Tier breakpoints: "
        "🟢 LOCKS (EV ≥ +6%) · 🟡 STRONG (+3 to +6%) · 🔴 EDGE (passing but lighter)."
    )

    # ── Live track record from graded_bets.csv ──────────────────────────────
    # Renders a top banner with current ROI per market (moneyline/spread/total)
    # so the user sees fresh truth instead of stale hardcoded labels. Refresh
    # every 5 min — auto-updates as new picks grade.
    @st.cache_data(ttl=300, show_spinner=False)
    def _ufl_market_roi():
        import csv
        from pathlib import Path
        path = Path("data/graded_bets.csv")
        if not path.exists():
            return {}
        rows = list(csv.DictReader(path.open()))
        out = {}
        for mkt in ("moneyline", "spread", "total"):
            g = [r for r in rows if r.get("market") == mkt and r.get("result") in ("WIN","LOSS","PUSH")]
            if not g: continue
            w = sum(1 for r in g if r["result"] == "WIN")
            l = sum(1 for r in g if r["result"] == "LOSS")
            p = sum(1 for r in g if r["result"] == "PUSH")
            n_dec = w + l
            pnl = sum(float(r.get("pnl") or 0) for r in g)
            out[mkt] = {"w": w, "l": l, "p": p,
                        "win_pct": (w/n_dec*100) if n_dec else 0,
                        "roi": (pnl/n_dec*100) if n_dec else 0}
        return out

    _ufl_roi = _ufl_market_roi()
    if _ufl_roi:
        cards = []
        for mkt, s in sorted(_ufl_roi.items(), key=lambda kv: -kv[1]["roi"]):
            label = {"moneyline":"💰 Moneyline", "spread":"📈 Spread", "total":"🎯 Total"}.get(mkt, mkt)
            roi_pct = s["roi"]
            if roi_pct >= 50:   bg, accent = "#0a4a2a", "#69f0ae"
            elif roi_pct >= 0:  bg, accent = "#2a2a3a", "#ccc"
            else:               bg, accent = "#4a1a1a", "#ff6b35"
            # NOTE: no leading indent on the HTML — Streamlit markdown treats
            # 4+ leading spaces as a code block, which would render the raw
            # HTML as text instead of rendering it.
            cards.append(
                f'<div style="background:{bg};padding:14px 18px;border-radius:10px;'
                f'border-left:4px solid {accent};flex:1;min-width:180px;">'
                f'<div style="font-size:11px;color:#aaa;letter-spacing:1.5px;font-weight:600;">{label}</div>'
                f'<div style="font-size:24px;color:{accent};font-weight:700;'
                f"font-family:'Space Mono',monospace;margin:4px 0;\">"
                f'{roi_pct:+.1f}%</div>'
                f'<div style="font-size:11px;color:#ccc;">'
                f"{s['win_pct']:.0f}% W &middot; {s['w']}-{s['l']}-{s['p']} lifetime"
                f'</div></div>'
            )
        banner_html = (
            '<div style="margin:6px 0 18px 0;">'
            '<div style="font-size:11px;color:#888;letter-spacing:2px;font-weight:600;margin-bottom:8px;">'
            'LIVE TRACK RECORD — UPDATED AFTER EVERY GRADE RUN</div>'
            '<div style="display:flex;gap:10px;flex-wrap:wrap;">'
            + ''.join(cards) +
            '</div></div>'
        )
        st.markdown(banner_html, unsafe_allow_html=True)

    if not slate or not slate.get("games"):
        st.warning("No slate cached. Run `python src/pipeline/prediction_run.py` first.")
    else:
        # Sample-size gate: only surface picks in markets that have ≥20 settled
        # bets in the live track record. New markets with thin samples can show
        # in the Slate tab; they just don't qualify as MUST TAKE yet.
        MIN_MARKET_N = 20
        _proven_markets = {
            mkt for mkt, s in _ufl_roi.items()
            if (s.get("w", 0) + s.get("l", 0)) >= MIN_MARKET_N
        }

        # Flatten passing candidates across the whole slate
        rows = []
        skipped_thin = 0
        for g in slate["games"]:
            for c in (g.get("candidates") or []):
                if not c.get("passes_threshold"):
                    continue
                # Normalize market name to match _ufl_roi keys
                # candidates use "h2h"/"spreads"/"totals", roi map uses
                # "moneyline"/"spread"/"total"
                mkt_raw = c.get("market", "")
                mkt_key = {
                    "h2h": "moneyline",
                    "spreads": "spread",
                    "totals": "total",
                }.get(mkt_raw, mkt_raw)
                if _proven_markets and mkt_key not in _proven_markets:
                    skipped_thin += 1
                    continue
                rows.append({
                    "game":        f"{g['away']} @ {g['home']}",
                    "date":        g.get("date", ""),
                    "label":       c.get("label", ""),
                    "market":      c.get("market", ""),
                    "side":        c.get("side", ""),
                    "book":        c.get("book", ""),
                    "odds":        c.get("american_odds", 0),
                    "p_model":     c.get("p_model", 0.0),
                    "p_market":    c.get("p_market", 0.0),
                    "edge_prob":   c.get("edge_prob", 0.0),
                    "edge_ev":     c.get("edge_ev", 0.0),
                    "stake_pct":   c.get("stake_pct", 0.0),
                })

        if skipped_thin:
            st.caption(
                f"🚧 {skipped_thin} candidate(s) hidden — markets with fewer "
                f"than {MIN_MARKET_N} settled picks are too thin to call yet. "
                f"They'll auto-surface as the sample grows."
            )

        if not rows:
            st.info("No passing picks on this slate. Sit it out.")
        else:
            mt_df = pd.DataFrame(rows).sort_values("edge_ev", ascending=False)
            bankroll_mt = st.sidebar.number_input(
                "MUST TAKE bankroll ($)", min_value=100.0, value=1000.0,
                step=100.0, key="mt_bankroll",
            )

            # Tier the picks by EV
            tiers = [
                ("🟢", "LOCKS",  "EV ≥ +6% — strongest expected value",   0.06,  9.99),
                ("🟡", "STRONG", "+3 to +6% — solid plays",               0.03,  0.06),
                ("🔴", "EDGE",   "Passing threshold but lighter EV",      0.00,  0.03),
            ]

            for emoji, name, desc, lo, hi in tiers:
                tier_df = mt_df[(mt_df["edge_ev"] >= lo) & (mt_df["edge_ev"] < hi)]
                if tier_df.empty:
                    continue

                avg_ev   = tier_df["edge_ev"].mean() * 100
                stake_total = (tier_df["stake_pct"] * bankroll_mt).sum()

                st.markdown(
                    f"### {emoji} {name} &nbsp; "
                    f"<span style='font-size:14px;color:#888;font-weight:400'>"
                    f"({len(tier_df)} picks · avg EV {avg_ev:+.1f}% · "
                    f"¼-Kelly total ${stake_total:.0f})</span>",
                    unsafe_allow_html=True,
                )
                st.caption(desc)

                for _, r in tier_df.iterrows():
                    stake_dollars = r["stake_pct"] * bankroll_mt
                    odds_str = f"+{r['odds']}" if r['odds'] > 0 else str(r['odds'])
                    st.markdown(
                        f"<div style='background:#1A1A2A; border-left:4px solid "
                        f"{'#00FF88' if lo >= 0.06 else '#FFD700' if lo >= 0.03 else '#FF6B35'};"
                        f"padding:10px 14px; margin:6px 0; border-radius:6px;'>"
                        f"<div style='font-size:15px; font-weight:600; color:#E2E2EE;'>"
                        f"{r['label']}"
                        f"<span style='font-size:12px; color:#888; font-weight:400'>"
                        f" — {r['game']} ({r['date']})</span></div>"
                        f"<div style='font-size:13px; color:#B8B8D4; margin-top:4px;'>"
                        f"{r['book']} <strong>{odds_str}</strong> &nbsp;·&nbsp; "
                        f"Model {r['p_model']*100:.1f}% vs Market {r['p_market']*100:.1f}% &nbsp;·&nbsp; "
                        f"Edge <strong>{r['edge_prob']*100:+.1f}pp</strong> &nbsp;·&nbsp; "
                        f"EV <strong style='color:#00FF88'>{r['edge_ev']*100:+.1f}%</strong> &nbsp;·&nbsp; "
                        f"¼-Kelly <strong>${stake_dollars:.0f}</strong>"
                        f"</div></div>",
                        unsafe_allow_html=True,
                    )

# ===== Tab 1: Slate ======================================================
with tab_slate:
    st.header("Upcoming slate")

    bankroll = st.sidebar.number_input("Bankroll ($)",
                                        min_value=100.0, value=1000.0, step=100.0)
    only_passing = st.sidebar.toggle("Only show +EV passing edge threshold", value=True)

    if not slate:
        st.warning("Run `python src/pipeline/prediction_run.py` to populate.")
    else:
        slate_as_of = slate.get("as_of", "?")
        n_pass = slate.get("n_candidates_passing", 0)
        n_total = slate.get("n_total_candidates", 0)
        weight = slate.get("weight_used", 0)

        st.caption(f"Slate generated {slate_as_of} • model weight {weight*100:.0f}% • "
                   f"{n_pass}/{n_total} candidates passing edge thresholds")

        for g in slate["games"]:
            with st.container(border=True):
                top1, top2, top3 = st.columns([2, 2, 3])
                with top1:
                    st.subheader(f"{g['away']} @ {g['home']}")
                    st.caption(f"Week {g['week']} • {g['date']}")
                with top2:
                    m = g["model"]
                    st.markdown(f"**Model:** "
                                f"{g['home']} {m['spread']:+.2f}, "
                                f"O/U {m['total']:.1f}")
                    st.caption(f"Score: {g['home']} {m['home_score']} - {g['away']} {m['away_score']}, "
                               f"WP {m['home_wp']*100:.1f}%")
                with top3:
                    if g.get("market"):
                        mk = g["market"]["markets"]
                        parts = []
                        if "spreads" in mk:
                            parts.append(f"Spread: {g['home']} {mk['spreads']['consensus_point']:+.1f}")
                        if "totals" in mk:
                            parts.append(f"Total: {mk['totals']['consensus_point']:.1f}")
                        if "h2h" in mk:
                            parts.append(f"WP cons: {mk['h2h']['consensus_prob_a']*100:.1f}%")
                        st.markdown("**Market:** " + " | ".join(parts))
                        st.caption(f"({mk[next(iter(mk))]['n_books']} books)")
                    else:
                        st.caption("_No market consensus available_")

                # Derived
                d = g["derived"]
                st.caption(
                    f"📐 Derived: 1H spread {d['h1_spread']:+.2f} • "
                    f"1H total {d['h1_total']:.1f} • "
                    f"team totals {g['home']} {d['home_team_total']:.1f} / "
                    f"{g['away']} {d['away_team_total']:.1f}"
                )

                cands = g.get("candidates") or []
                if only_passing:
                    cands = [c for c in cands if c.get("passes_threshold")]
                if cands:
                    rows = []
                    for c in cands:
                        rows.append({
                            "Pick": c["label"],
                            "Mkt": c["market"],
                            "Side": c["side"],
                            "Book": c["book"],
                            "Odds": c["american_odds"],
                            "p_model": f"{c['p_model']*100:.1f}%",
                            "p_market": f"{c['p_market']*100:.1f}%",
                            "Edge": f"{c['edge_prob']*100:+.2f}pp",
                            "EV": f"{c['edge_ev']*100:+.1f}%",
                            "Stake $": round(bankroll * c["stake_pct"], 2),
                            "Stake %": f"{c['stake_pct']*100:.2f}%",
                            "✅": "✓" if c["passes_threshold"] else "",
                        })
                    df = pd.DataFrame(rows)
                    st.dataframe(df, use_container_width=True, hide_index=True)
                elif not only_passing:
                    st.caption("_No candidates evaluated for this game._")
                else:
                    st.caption("_No edges passing threshold._")

# ===== Tab 2: Team Ratings ===============================================
with tab_ratings:
    st.header("Team ratings — current snapshot")
    if not ratings:
        st.warning("Run `python src/pipeline/weekly_update.py` to populate.")
    else:
        rows = []
        for code, elo_val in ratings["elo"].items():
            if code.startswith("__"):
                continue
            adj = ratings["ppd_adj"].get(code, {"off": 0.0, "def": 0.0, "n": 0})
            net = adj.get("off", 0) + adj.get("def", 0)  # higher def = better defense
            rows.append({
                "Team": code,
                "Full name": TEAMS[code].full_name,
                "Elo": round(elo_val, 1),
                "Games": ratings["elo_games_played"].get(code, 0),
                "Off PPD adj": round(adj.get("off", 0), 2),
                "Def PPD adj": round(adj.get("def", 0), 2),
                "Net PPD": round(net, 2),
                "Coach": TEAMS[code].head_coach,
                "Stadium": TEAMS[code].stadium,
            })
        df = pd.DataFrame(rows).sort_values("Elo", ascending=False)
        st.dataframe(df, use_container_width=True, hide_index=True)

        league = ratings["ppd_adj"].get("__league__", {})
        if league:
            st.caption(f"League mean PPD: **{league.get('mu_ppd', 0):.2f}** • "
                       f"HFA (PPD): **{league.get('hfa', 0):+.3f}**")

        st.subheader("Schedule status")
        today = date.today()
        completed = games_played_through(today)
        ahead = upcoming_games(today, days_ahead=14)
        cA, cB = st.columns(2)
        with cA:
            st.markdown(f"**{len(completed)}** games played through today")
            st.markdown(f"**{len(ahead)}** games in the next 14 days")
        with cB:
            st.markdown(f"Season: {SEASON_START} → {SEASON_END}")
            st.markdown(f"Playoffs start: {PLAYOFFS_START}")
            st.markdown(f"Championship: {CHAMPIONSHIP} (Audi Field, ABC)")

# ===== Tab 3: Calibration ================================================
with tab_calib:
    st.header("Model calibration")

    st.caption(
        "Per-week model accuracy vs market. Recomputes after each weekly_update run. "
        "Once we have ~16 games of edge_log data, we can adjust σ_total/σ_margin "
        "and the elo_blend_weight."
    )

    # Look for calibration logs (created by predictions runs comparing
    # closing line vs model + actual outcome)
    calib_path = DATA_DIR / "calibration.csv"
    if not calib_path.exists():
        st.info("No calibration data yet. Will populate after first completed slate "
                "with model predictions logged.")
    else:
        df = pd.read_csv(calib_path)
        st.dataframe(df, use_container_width=True, hide_index=True)

    st.subheader("Tunables (edit src/model/* to change)")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("σ_total",  "14.5")
        st.metric("σ_margin", "13.5")
    with c2:
        st.metric("Elo K",        "24.0")
        st.metric("HFA (Elo)",    "50.0 (~1.5 pts)")
    with c3:
        st.metric("Edge threshold (full-game)", "3.0%")
        st.metric("Edge threshold (derived)",   "5.0%")

    st.divider()
    st.subheader("Bankroll discipline")
    st.markdown(
        """
- 1/4 Kelly sizing
- 2% per-bet bankroll cap
- 0.25% minimum stake (filters noise)
- Edge in **probability points** (p_model − p_implied), not just EV%
        """
    )

st.sidebar.divider()
st.sidebar.caption(f"UFL Model v0.3 • {date.today()}")
