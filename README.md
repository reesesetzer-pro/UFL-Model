# UFL Betting Model

Production-grade Bayesian model for the United Football League (2026 season),
built on StatBroadcast XML play-by-play + The Odds API.

Sister model to your existing **golf**, **MLB F5**, and **CBB** repos.
Same conventions: Streamlit dashboard, Supabase backend, GitHub-hosted,
The Odds API for prices.

---

## Status — May 2, 2026

- **Season:** Week 6 of 10 in progress (5 Thursday games complete)
- **First live slate target:** Week 7, Friday May 8 vs. Columbus @ St. Louis
- **Championship:** Saturday June 13 at Audi Field on ABC

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  INGEST                                                              │
│   ├─ src/ingest/statbroadcast.py     (XML primary — full PBP)        │
│   ├─ src/ingest/espn_api.py          (backup/redundancy)             │
│   ├─ src/ingest/theufl_aggregates.py (season-aggregate validation)   │
│   └─ src/ingest/odds_api.py          (prices, 7 books)               │
│                                                                      │
│  DATA                                                                │
│   └─ src/data/schedule.py            (40-game master schedule)       │
│                                                                      │
│  MODEL                                                               │
│   ├─ src/model/elo.py                (K=24, HFA=50, MOV mult)        │
│   ├─ src/model/efficiency.py         (PPD, opp-adj ridge)            │
│   ├─ src/model/projector.py          (score lines + WP)              │
│   ├─ src/model/derived_markets.py    (1H, team totals)               │
│   └─ src/model/prior_blend.py        (Bayes shrinkage to market)     │
│                                                                      │
│  EDGE                                                                │
│   ├─ src/edge/no_vig.py              (devig + consensus)             │
│   └─ src/edge/edge_calc.py           (1/4 Kelly, 2% cap)             │
│                                                                      │
│  PIPELINE                                                            │
│   ├─ src/pipeline/weekly_update.py    (Mondays 10am ET)              │
│   ├─ src/pipeline/daily_odds_snap.py  (4×/day cron)                  │
│   └─ src/pipeline/prediction_run.py   (slate generator)              │
│                                                                      │
│  STORAGE / UI                                                        │
│   ├─ schema.sql                      (14 tables, Supabase)           │
│   ├─ src/db/load_to_supabase.py      (parsed JSON → DB rows)         │
│   └─ streamlit_app.py                (3-tab dashboard)               │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Setup (Windows)

```cmd
:: From C:\
git clone https://github.com/reesesetzer-pro/ufl-model.git C:\UFL_Model
cd C:\UFL_Model

:: Use the same Python you use for everything else
C:\Python314\python.exe -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements.txt

:: Copy and fill in
copy .env.example .env
notepad .env
```

Then in Supabase SQL editor: paste `schema.sql` and run once.

---

## Day-to-day operation

### Mondays — full refresh (run once)

```cmd
python scripts\run_full_pipeline.py --to-supabase
```

This: backfills any new completed games, scrapes theUFL.com aggregates,
recomputes Elo + opponent-adjusted PPD, snaps odds, generates next slate.

### Tue–Fri — odds-only refreshes (cron, 4×/day)

```cmd
python -m src.pipeline.daily_odds_snap
```

Suggested Task Scheduler triggers: 09:00, 12:00, 16:00, 20:00 ET.

### Pre-slate — regenerate predictions with latest odds

```cmd
python -m src.pipeline.prediction_run --bankroll 1000
```

### Dashboard

```cmd
streamlit run streamlit_app.py
```

Three tabs:
- **Slate** — every upcoming game with model line, market line, +EV picks, Kelly stake
- **Team ratings** — Elo + PPD for all 8 teams, sorted
- **Calibration** — model error vs market error (populates after first results)

---

## Data sources, ranked

| Tier | Source | Key data |
|---|---|---|
| **1 — Primary** | StatBroadcast XML | Drives, full PBP w/ air yards, players, weather, officials |
| **2 — Backup** | ESPN public API | Same shape as NFL/CFB; redundancy if XML drops |
| **3 — Validation** | theUFL.com server-rendered HTML | Season-to-date team aggregates |
| **4 — Odds** | The Odds API | h2h / spreads / totals across 7 books |

Game ID range for 2026: **656640–656679** (sequential).
Hub: `http://www.statbroadcast.com/events/statmonitr.php?gid=ufl`
Per-game XML: `http://archive.statbroadcast.com/{gameId}.xml`

Full inventory: see [`DATA_INVENTORY.md`](DATA_INVENTORY.md).

---

## Modeling notes (2026-specific)

The 2026 UFL is meaningfully different from 2024/25:
- **All 8 rosters were liquidated and redrafted** in January 2026.
  Returning teams get strongly regressed: `seed = 1500 + 0.25*(2025_final − 1500)`.
  Expansion teams (CLB, LOU, ORL) start at 1500.
- **Major rule changes** to "turbocharge offenses":
  4-pt FGs from 60+, no punting inside opp 50, banned tush push,
  1-foot inbounds catches, 1/2/3-pt PAT options.
- **Single 8-team table**, no conferences. Top-4 make playoffs.

Implications:
- Pre-2026 data is **misleading for prediction** even on returning teams.
- σ_total bumped to 14.5 (NFL ~13.5) and σ_margin to 13.5 (NFL ~13.0)
  to reflect rule-induced variance.
- Bayesian shrinkage to market is heavier than a normal football model:
  `weight_model = 0.20 + 0.50 * min(games_played/8, 1.0)`.

---

## Approved books

DraftKings, FanDuel, BetMGM, Caesars (`williamhill_us`),
Bet365, theScore (`thescore`), Hard Rock Bet (`hardrockbet`).

Same as your other models.

---

## Edge thresholds

| Market | Min edge to bet |
|---|---|
| Moneyline / spread / total (full game) | **3.0%** prob points |
| 1H spread / 1H total / team total (derived) | **5.0%** prob points |

Sizing: **¼ Kelly**, capped at **2% of bankroll** per bet.
Minimum stake **0.25%** to filter out noise picks.

---

## Files & structure

```
UFL_Model/
├── README.md                  # this file
├── DATA_INVENTORY.md          # complete data-source catalogue
├── schema.sql                 # v0.3 — 14 tables, Supabase
├── requirements.txt
├── .env.example               # ODDS_API_KEY pre-filled, paste Supabase keys
├── streamlit_app.py           # 3-tab dashboard
│
├── src/
│   ├── data/
│   │   └── schedule.py        # 40-game master schedule + team metadata
│   ├── ingest/
│   │   ├── statbroadcast.py   # XML parser (PRIMARY data)
│   │   ├── espn_api.py        # Backup
│   │   ├── theufl_aggregates.py  # theUFL.com season aggregates
│   │   └── odds_api.py        # The Odds API client
│   ├── model/
│   │   ├── elo.py             # Elo ratings
│   │   ├── efficiency.py      # PPD + opponent adjustment
│   │   ├── projector.py       # Score projection
│   │   ├── derived_markets.py # 1H + team totals
│   │   └── prior_blend.py     # Bayesian blend with market
│   ├── edge/
│   │   ├── no_vig.py          # Devig + consensus
│   │   └── edge_calc.py       # Edge + Kelly sizing
│   ├── pipeline/
│   │   ├── weekly_update.py   # Recompute ratings (Mondays)
│   │   ├── daily_odds_snap.py # Pull odds (4x/day)
│   │   └── prediction_run.py  # Generate slate
│   └── db/
│       ├── supabase_client.py
│       └── load_to_supabase.py
│
└── scripts/
    ├── run_full_pipeline.py   # One-shot Monday refresh
    ├── backfill_2026.py       # Pull all completed XMLs
    ├── test_statbroadcast.py  # Smoke test
    └── test_odds_api.py
```

---

## Roadmap

- [x] Data recon (ESPN, theUFL.com, StatBroadcast, Odds API)
- [x] Master schedule + team metadata
- [x] Schema v0.3 (14 tables)
- [x] StatBroadcast XML parser (full PBP)
- [x] theUFL.com season-aggregate parser
- [x] Odds API client w/ approved books
- [x] Elo + drive-efficiency model
- [x] Score projector + derived markets
- [x] Devig + edge + Kelly engine
- [x] Pipeline orchestration (weekly + daily + slate)
- [x] Supabase ETL
- [x] Streamlit dashboard
- [ ] **First live slate: Week 7 (May 8)**
- [ ] Recalibrate σ_total/σ_margin after Week 8 (~32 games)
- [ ] Live betting v2 (post-season)
- [ ] Add player props (post-season)

---

## License

Personal use; not for redistribution.
StatBroadcast XML feeds are intended for media/event-staff use; the league
publishes them on `theufl.com/ufl-live-stats-media`.
