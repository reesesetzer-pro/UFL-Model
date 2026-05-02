# UFL Model — Claude Code project memory

This file is loaded automatically by Claude Code at session start.
Read it before doing anything.

---

## What this project is

A Bayesian sports-betting model for the **United Football League** 2026 season.

Sister project to **golf-model**, **mlb-f5-model**, and **cbb-model**
(all Reese's). Same conventions: Streamlit Cloud dashboard, Supabase backend,
GitHub repo under `reesesetzer-pro`, The Odds API for prices.

GitHub target: `https://github.com/reesesetzer-pro/ufl-model` (create if absent).
Streamlit Cloud app: deploy from main branch, mainline file `streamlit_app.py`.

---

## Owner conventions (do not violate)

- **Python:** `C:\Python314\python.exe`. Always use the venv at `.venv/`.
- **Working dir:** `C:\UFL_Model\`.
- **Editor:** VS Code on Windows. Terminal: PowerShell or cmd.
- **No file modifications outside this repo.** Don't touch other Reese projects.
- **Approved sportsbooks** (across every Reese model):
  DraftKings, FanDuel, BetMGM, Caesars (`williamhill_us`),
  Bet365, theScore (`thescore`), Hard Rock Bet (`hardrockbet`).
- **The Odds API key** is in `.env`: `ODDS_API_KEY=40cfbba84e52cd6da31272d4ac287966`
  (shared across models — don't leak in commits).
- **Code style:** match what's already here. Type hints, dataclasses, defensive
  parsing, `from __future__ import annotations` at top of every file.
- **Git:** main branch only, conventional commit messages.

---

## Current state (May 2, 2026)

### What's built and tested
- Master 2026 schedule (40 games, IDs 656640–656679) in `src/data/schedule.py`
- Schema v0.3 in `schema.sql` (14 tables, aligned to ETL)
- Ingestion: StatBroadcast XML parser, ESPN API backup, theUFL.com aggregates, Odds API
- Model: Elo (K=24, HFA=50), drive efficiency w/ ridge regression, projector,
  derived markets (1H + team totals), Bayesian prior-blend
- Edge: power/multiplicative/additive devig, 7-book consensus, 1/4 Kelly + 2% cap
- Pipeline: weekly_update, daily_odds_snap, prediction_run
- Streamlit dashboard with 3 tabs (Slate, Ratings, Calibration)
- Supabase ETL (`src/db/load_to_supabase.py`)
- Baseline ratings snapshot from real W1-6 box scores (so dashboard renders on
  first run before backfill)

### What's NOT YET DONE (your job)
1. **Real backfill** — Pull all 22 completed StatBroadcast XMLs and validate the
   parser. The parser was tested on synthetic XML; real XML may have edge cases
   (especially around UFL-specific fields like alt KO, 4-pt FG, 1/2/3-pt PATs).
2. **Supabase deploy** — Paste `schema.sql`, then run ETL.
3. **Push to GitHub** — Create repo `reesesetzer-pro/ufl-model`, push.
4. **Streamlit Cloud deploy** — Connect GitHub repo, set `ODDS_API_KEY` and
   `SUPABASE_URL`/`SUPABASE_KEY` as secrets.
5. **First live slate: Week 7 (Fri May 8)** — Run prediction_run after odds snap.

---

## Architecture map

```
src/
├── data/schedule.py            ← MASTER GAME ID TABLE (single source of truth)
├── ingest/
│   ├── statbroadcast.py        ← PRIMARY: full PBP from XML
│   ├── espn_api.py             ← Backup
│   ├── theufl_aggregates.py    ← Validation: season aggregates
│   └── odds_api.py             ← The Odds API client
├── model/
│   ├── elo.py                  ← K=24 Elo, expansion adjustment, MOV multiplier
│   ├── efficiency.py           ← PPD + ridge opponent adjustment
│   ├── projector.py            ← Score line + win prob (blend Elo + efficiency)
│   ├── derived_markets.py      ← 1H lines + team totals
│   └── prior_blend.py          ← Bayesian shrinkage to market consensus
├── edge/
│   ├── no_vig.py               ← Devig + multi-book consensus
│   └── edge_calc.py            ← Edge calc + 1/4 Kelly + 2% cap
├── pipeline/
│   ├── weekly_update.py        ← Mondays: ingest + Elo + ratings snapshot
│   ├── daily_odds_snap.py      ← 4×/day: pull odds → CSV + Supabase
│   └── prediction_run.py       ← Slate: ratings × odds → edges + Kelly
└── db/
    ├── supabase_client.py
    └── load_to_supabase.py     ← Parsed JSON → 8 typed tables

scripts/
├── run_full_pipeline.py        ← One-shot Monday refresh
├── backfill_2026.py            ← Pull all XMLs, parse, cache
├── test_statbroadcast.py       ← Smoke test
└── test_odds_api.py
```

---

## Sign convention (CRITICAL — already burned us once)

In opponent-adjusted PPD:
- `off_T > 0` means team T scores more than league average (good offense)
- **`def_T > 0` means team T allows fewer than league average (good defense)**
- Net team strength: `off_T + def_T` (NOT `off_T − def_T`)
- Projector formula: `points_home = mu + off_home − def_away + hfa`
  (subtract opp def because higher def = harder to score on)

The dashboard's "Net PPD" column was wrong in v0.3.0; fixed in v0.3.1.
Never forget: **higher def_T = better defense**.

---

## Tunables (centralize before tweaking)

| File | Tunable | Default | Notes |
|---|---|---|---|
| `src/model/elo.py` | `K`, `HFA_ELO`, `ELO_PER_POINT` | 24, 50, 33 | Recalibrate post-season |
| `src/model/elo.py` | `RETURNING_REGRESSION` | 0.25 | Roster-liquidation regression |
| `src/model/efficiency.py` | `RIDGE_LAMBDA` | 5.0 | Heavy reg for small samples |
| `src/model/efficiency.py` | `DEFAULT_TOTAL_DRIVES` | 24.0 | Bump after Week 4 with real data |
| `src/model/projector.py` | `SIGMA_TOTAL`, `SIGMA_MARGIN` | 14.5, 13.5 | Recalibrate weekly |
| `src/model/projector.py` | `ELO_BLEND_WEIGHT` | 0.5 | Elo vs efficiency weight |
| `src/model/derived_markets.py` | `H1_TOTAL_RATIO`, `H1_SPREAD_RATIO` | 0.46, 0.55 | Re-fit after Week 8 |
| `src/model/prior_blend.py` | `MODEL_WEIGHT_FLOOR`, `MODEL_WEIGHT_GROWTH` | 0.20, 0.50 | Heavier vs other Reese models |
| `src/edge/edge_calc.py` | `KELLY_FRACTION`, `BANKROLL_CAP_PCT` | 0.25, 0.02 | Match Reese's other models |
| `src/edge/edge_calc.py` | `EDGE_THRESHOLD_FULL_GAME` | 0.03 | 3% prob points |
| `src/edge/edge_calc.py` | `EDGE_THRESHOLD_DERIVED` | 0.05 | 5% for 1H + team totals |

---

## Common gotchas

1. **PYTHONPATH for ad-hoc scripts:** When running `python -c "from src..."`,
   prepend `PYTHONPATH=.` (Linux) or set `$env:PYTHONPATH="."` (PowerShell).
   The pipeline scripts do this internally via `sys.path.insert`.

2. **StatBroadcast rate limit:** No documented limit but we use 0.5s sleep.
   Don't go below 0.3s or you'll get 429s on the archive.

3. **The Odds API quota:** 500 requests/month on the free tier. One snapshot
   ≈ 1 request. 4 snapshots/day × 30 days = 120 — well under cap.
   But don't refetch on every page load.

4. **Supabase upsert:** ETL uses `on_conflict="sb_id,team"` etc. If you change
   primary keys, update both schema.sql AND the load_to_supabase row builders.

5. **theUFL.com schedule URL is server-rendered, scoreboard is JS-rendered.**
   Use `/ufl-live-stats-media` (server-rendered, has all 40 game IDs) instead
   of `/scores` or `/schedule`.

6. **Game ID is `int` in schema, NOT `text`.** Old v0.2 schema used text;
   v0.3 uses int. If you find leftover string casts, fix them.

7. **Synthetic Elo from box-scores-only is flawed** — drives are guessed at 12
   per team. Real Elo will differ once we ingest actual XML drive counts.

---

## Suggested first session in Claude Code

```
1. Verify environment: `python --version` (should be 3.14), `pip list | grep -i streamlit`
2. Run smoke tests:
     python scripts/test_odds_api.py        (validates Odds API works)
     python scripts/test_statbroadcast.py   (validates XML parse against live game)
3. If both pass, run real backfill:
     python scripts/backfill_2026.py
   This pulls all 22 completed game XMLs to data/raw/statbroadcast/
   and parses to data/parsed/. Should take ~15 seconds at 0.5s sleep.
4. Inspect 1-2 parsed JSONs against the StatBroadcast HTML view to spot
   any parser misses (especially per-player stat field names).
5. Run weekly_update against real data:
     python -m src.pipeline.weekly_update
   This should produce a fresh ratings snapshot in data/ratings/.
6. Stand up Supabase:
     - Paste schema.sql into Supabase SQL editor
     - Add SUPABASE_URL and SUPABASE_KEY to .env
     - Run: python -m src.db.load_to_supabase --dry-run  (verify counts)
     - Run for real: python -m src.db.load_to_supabase
7. Pull odds + generate slate:
     python -m src.pipeline.daily_odds_snap
     python -m src.pipeline.prediction_run
8. Launch dashboard:
     streamlit run streamlit_app.py
9. Push to GitHub:
     git init && git add . && git commit -m "Initial commit: UFL model v0.3.1"
     gh repo create reesesetzer-pro/ufl-model --public --source=. --push
10. Deploy to Streamlit Cloud, paste secrets in dashboard config.
```

If any step fails, FIX THE UNDERLYING CODE rather than working around it.
This is going to be running unattended — robustness matters.

---

## Schedule reminders

- **Week 6 ends:** Sun May 3 (Birmingham @ Orlando, 4pm ET FOX)
- **Week 7 starts:** Fri May 8 (Columbus @ St. Louis, 8pm ET FOX) — **first live slate target**
- **Regular season ends:** Sun May 31
- **Playoffs start:** Sat Jun 7
- **Championship:** Sat Jun 13 at Audi Field on ABC
- **Recalibrate σ_total/σ_margin:** after Week 8 (~32 games of real data)

---

## Modeling reminders (so you don't forget WHY things are tuned this way)

- **The 2026 UFL is fundamentally different from 2024/25:** all rosters
  liquidated, three new franchises (CLB, LOU, ORL), two rebrands (DAL, HOU),
  major rule changes. Pre-2026 data is LIMITED predictive value even for
  returning teams.
- **Heavy Bayesian shrinkage to market** is intentional. With <8 games per team,
  model variance is huge. Float on market consensus until ~Week 8, then let
  the model breathe.
- **σ values inflated vs NFL** because UFL rules induce more scoring variance:
  4-pt FGs, banned tush push, no-punt zones, 1/2/3-pt PAT options.
- **Indoor venue:** only St. Louis (America's Center Dome). Weather matters
  for the other 7. StatBroadcast XML has weather inline already; don't pull
  a separate weather API.

---

## When in doubt

Read `README.md` first, then `DATA_INVENTORY.md`. Both are kept current.
The smoke tests in `scripts/` are also good "show me how it should work" docs.
