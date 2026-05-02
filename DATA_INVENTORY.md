# UFL Data Inventory

Comprehensive catalogue of every data point available for the UFL betting model.

Last verified: **May 2, 2026**.

---

## TL;DR — what we have

**Tier 1 (primary):** StatBroadcast XML — full play-by-play with air yards,
drive tracking, weather, officials, per-player stats, UFL rule package per
game, all at stable URLs.

**Tier 2 (backup):** ESPN public API — same shape as their NFL/CFB endpoints,
useful as a redundancy check.

**Tier 3 (validation):** theUFL.com server-rendered HTML — season-to-date
team aggregates with offense/defense split, indispensable sanity check.

**Tier 4 (odds):** The Odds API — h2h / spreads / totals across 7 approved
books.

We **do not have**: live betting feeds (sport too small), injury reports
beyond Twitter rumors, sharp-money or line-movement history without paid
services, advanced player tracking. Workable: skip the props market for v1.

---

## Tier 1 — StatBroadcast (the goldmine)

### Hub + archive

| URL | What |
|---|---|
| `http://www.statbroadcast.com/events/statmonitr.php?gid=ufl` | Live hub (today's games + most recent) |
| `http://www.statbroadcast.com/events/archive.php?gid=ufl` | Historical archive (JS-rendered; use direct IDs) |
| `https://www.theufl.com/ufl-live-stats-media` | **Server-rendered table mapping every 2026 game to its StatBroadcast URL** |
| `http://archive.statbroadcast.com/{id}.xml` | Per-game structured data ← **USE THIS** |
| `http://archive.statbroadcast.com/{id}.html` | Browser view of same data |
| `http://archive.statbroadcast.com/{id}.pdf` | PDF box score |
| `http://stats.statbroadcast.com/statmonitr/?id={id}` | Live in-game feed (during games only) |

### 2026 game ID master table (verified via theufl.com)

| Game ID | Date | Matchup | Result |
|---|---|---|---|
| **Week 1** | | | |
| 656640 | Mar 27 | BHM @ LOU | BHM 15, LOU 13 |
| 656641 | Mar 28 | DC  @ STL | STL 16, DC 10 |
| 656642 | Mar 28 | HOU @ DAL | DAL 36, HOU 17 |
| 656643 | Mar 29 | CLB @ ORL | ORL 23, CLB 16 |
| **Week 2** | | | |
| 656644 | Apr 3  | DC  @ CLB | DC 44, CLB 26 |
| 656645 | Apr 4  | LOU @ ORL | ORL 19, LOU 9 |
| 656646 | Apr 5  | BHM @ HOU | HOU 22, BHM 20 |
| 656647 | Apr 7  | STL @ DAL | DAL 31, STL 15 |
| **Week 3** | | | |
| 656648 | Apr 10 | ORL @ LOU | ORL 29, LOU 27 (OT) |
| 656649 | Apr 11 | HOU @ DC  | DC 45, HOU 7 |
| 656650 | Apr 12 | CLB @ DAL | DAL 28, CLB 23 |
| 656651 | Apr 12 | BHM @ STL | STL 34, BHM 30 |
| **Week 4** | | | |
| 656652 | Apr 16 | LOU @ HOU | LOU 24, HOU 22 (OT) |
| 656653 | Apr 17 | DAL @ CLB | CLB 28, DAL 14 |
| 656654 | Apr 18 | STL @ DC  | DC 28, STL 22 |
| 656655 | Apr 18 | ORL @ BHM | ORL 16, BHM 0 |
| **Week 5** | | | |
| 656656 | Apr 24 | DC  @ BHM | DC 45, BHM 28 |
| 656657 | Apr 25 | STL @ ORL | STL 25, ORL 17 |
| 656658 | Apr 26 | CLB @ HOU | HOU 17, CLB 13 |
| 656659 | Apr 26 | LOU @ DAL | LOU 47, DAL 25 |
| **Week 6** | | | |
| 656660 | Apr 30 | STL @ LOU | STL 16, LOU 3 |
| 656661 | May 1  | HOU @ CLB | _Friday 8pm FOX_ |
| 656662 | May 2  | DAL @ DC  | _Saturday 12pm ABC_ |
| 656663 | May 3  | BHM @ ORL | _Sunday 4pm FOX_ |
| **Week 7** | | | |
| 656664 | May 8  | CLB @ STL | _Friday 8pm FOX_ |
| 656665 | May 9  | LOU @ DC  | _Saturday 1:30pm FOX_ |
| 656666 | May 9  | DAL @ BHM | _Saturday 8pm ESPN_ |
| 656667 | May 10 | ORL @ HOU | _Sunday 6pm FS1_ |
| **Week 8** | | | |
| 656668 | May 15 | ORL @ DAL | |
| 656669 | May 16 | DC  @ LOU | |
| 656670 | May 16 | HOU @ STL | |
| 656671 | May 17 | CLB @ BHM | |
| **Week 9** | | | |
| 656672 | May 22 | DC  @ ORL | |
| 656673 | May 23 | BHM @ CLB | |
| 656674 | May 24 | DAL @ LOU | |
| 656675 | May 24 | STL @ HOU | |
| **Week 10** | | | |
| 656676 | May 29 | DAL @ STL | |
| 656677 | May 30 | HOU @ BHM | |
| 656678 | May 31 | ORL @ DC  | |
| 656679 | May 31 | LOU @ CLB | |

This is **encoded as Python ground truth** in `src/data/schedule.py`.

### What's in each XML

| Block | Fields |
|---|---|
| `<venue>`     | gameid, season, week, date, start/end time, **duration**, location, stadium, **attendance**, **temperature_f**, **wind**, **weather** |
| `<rules>`     | 4-pt FG, 1/2/3-pt PAT spots, kospot, tbspot, OT format — full UFL rule dump per game |
| `<officials>` | 8 roles (ref/ump/dj/lj/fj/sj/bj/replay) with names + uniforms |
| `<team>/<totals>` | 50+ fields: by-quarter scoring, total offense/yards/avg, FD splits, conversions (3rd, 4th, alt KO), fumbles, TOP, points off TOs, redzone att/scores, rushing att/yds/td/long, passing comp/att/int/yds/td/long/sacks, receiving cnt/yds/td/long/**YAC** |
| `<drives>`    | start/end yardline, time elapsed, plays count, end_how, inside_20 flag, points |
| `<plays>`     | full PBP w/ down/distance/yardline, play type, **air yards**, YAC, sack/penalty/turnover/td flags, full text |
| `<players>`   | per-player stats incl. **QB rating**, drops, throwaways, targets — split by team |
| `<scoring>`   | per-score: type (TD/FG/SAFETY/PAT), points, **PAT type 1/2/3**, is_4pt_fg flag |
| `<fgas>`      | FG attempts split between 3-pt and 4-pt (UFL-specific) |

### League-wide PDF (stable, no auth)

```
https://s3.us-east-1.amazonaws.com/s3.statbroadcast.com/hosted/pdf/ufl/2026league.pdf
```

Updated weekly, contains all team aggregates + standings.

---

## Tier 2 — ESPN public API (backup)

| Endpoint | Returns |
|---|---|
| `https://site.api.espn.com/apis/site/v2/sports/football/ufl/scoreboard?dates=YYYYMMDD-YYYYMMDD` | Schedule + scores by date range |
| `https://site.api.espn.com/apis/site/v2/sports/football/ufl/summary?event={id}` | Per-game: boxscore, drives, plays, win prob, odds |
| `https://cdn.espn.com/core/ufl/playbyplay?xhr=1&gameId={id}` | Full PBP |

Use as redundancy if StatBroadcast XML is delayed.

---

## Tier 3 — theUFL.com (validation)

| URL pattern | Returns |
|---|---|
| `https://www.theufl.com/teams/{slug}/stats` | **Server-rendered season aggregates** with team/opponent split (47 stat rows) |
| `https://www.theufl.com/teams/{slug}/stats/results` | Game-by-game W/L for the team |
| `https://www.theufl.com/teams/{slug}/stats/individual` | Per-player aggregates |
| `https://www.theufl.com/standings` | League standings |

Slugs: `birmingham`, `columbus`, `dallas`, `dc`, `houston`, `louisville`, `orlando`, `st-louis`.

Schedule, scores, and individual game pages on the front-end of theufl.com are
JS-rendered — **avoid** them as primary sources.

---

## Tier 4 — The Odds API (prices)

Sport key: **`americanfootball_ufl`** (results `+scores=true` works).

Featured markets only: **h2h, spreads, totals**.
1H lines and team totals are **derived from the model**, not pulled from the API.

Approved books (key vault unchanged from your other models):
`draftkings`, `fanduel`, `betmgm`, `williamhill_us` (Caesars),
`bet365`, `thescore`, `hardrockbet`.

API key: `40cfbba84e52cd6da31272d4ac287966` (in `.env.example`).

Historical: from Feb 2023 (use `americanfootball_xfl` slug for pre-2024;
`americanfootball_ufl` from 2024 onward).

---

## Tier 5 — Auxiliary

| Source | Use |
|---|---|
| Weather (already in StatBroadcast venue block) | Skip a separate API |
| Twitter beat reporters | Injury / depth chart updates (manual scrape, low priority) |
| pro-football-reference | Doesn't cover UFL — skip |
| nflfastR | Doesn't exist for UFL — skip |
| FOX Sports article schedule | Cross-reference for results recaps |

---

## What we deliberately skip (v1)

| Skipped | Why |
|---|---|
| Player props | Not in The Odds API for UFL; books publish them on UI only. Manual workflow if needed. |
| Live betting | Adds engineering complexity for marginal +EV in a small market. Post-season target. |
| Sharp-money / line-history | Requires Pinnacle / paid services; we use multi-book consensus instead. |
| Coach-charged offensive metrics | Not in any structured feed; manual tagging won't scale. |

---

## Network policy

Be polite. Default 0.5s sleep between StatBroadcast requests; 1.0s between
theUFL.com requests. Cache locally (`data/raw/`) so we never re-fetch the
same XML twice.
