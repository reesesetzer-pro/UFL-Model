-- UFL Model database schema (Supabase / Postgres)
-- v0.3 — aligned with ETL row builders in src/db/load_to_supabase.py.
-- Paste into Supabase SQL editor (creates everything; safe to re-run).

-- ============================================================
-- 1. games — one row per game
-- ============================================================
create table if not exists games (
    sb_id          int primary key,
    season         int not null,
    week           int,
    game_date      date not null,
    game_datetime  timestamptz,
    home           text not null,
    away           text not null,
    home_score     int,
    away_score     int,
    stadium        text,
    city           text,
    state          text,
    indoor         bool,
    temperature_f  int,
    wind           text,
    weather        text,
    attendance     int,
    duration       text,
    network        text,
    status         text default 'scheduled',
    espn_event_id  text,
    odds_api_id    text,
    created_at     timestamptz default now(),
    updated_at     timestamptz default now()
);
create index if not exists idx_games_season_week on games(season, week);
create index if not exists idx_games_date         on games(game_date);

-- ============================================================
-- 2. team_game_stats — two rows per game
-- ============================================================
create table if not exists team_game_stats (
    sb_id        int references games(sb_id) on delete cascade,
    team         text not null,
    side         text not null check (side in ('home','away')),
    score        int,
    total_plays  int,
    total_yards  int,
    first_downs  int,
    first_downs_rush int,
    first_downs_pass int,
    first_downs_pen  int,
    third_down_att   int,
    third_down_conv  int,
    third_down_pct   numeric(5,2),
    fourth_down_att  int,
    fourth_down_conv int,
    fourth_down_pct  numeric(5,2),
    rush_att   int, rush_yds   int, rush_td    int, rush_long  int,
    pass_comp  int, pass_att   int, pass_int   int, pass_yds   int,
    pass_td    int, pass_long  int, sacks      int, sack_yds   int,
    rec_count  int, rec_yds    int, rec_yac    int, rec_td     int, rec_long   int,
    rz_attempts int, rz_tds      int, rz_fgs      int,
    penalties     int, penalty_yds   int, fumbles       int, fumbles_lost  int,
    top_seconds   int,
    alt_ko_att   int, alt_ko_conv  int,
    fg_made      int, fg_att       int,
    fg_4pt_made  int, fg_4pt_att   int,
    primary key (sb_id, team)
);
create index if not exists idx_tgs_team on team_game_stats(team);

-- ============================================================
-- 3. drives
-- ============================================================
create table if not exists drives (
    sb_id           int references games(sb_id) on delete cascade,
    drive_num       int not null,
    team            text not null,
    quarter         int,
    start_time      text,
    end_time        text,
    start_how       text,
    end_how         text,
    start_yardline  numeric(5,2),
    end_yardline    numeric(5,2),
    yards           int,
    plays_count     int,
    top_seconds     int,
    result_pts      int,
    inside_20       bool,
    primary key (sb_id, drive_num)
);
create index if not exists idx_drives_team on drives(team);

-- ============================================================
-- 4. plays
-- ============================================================
create table if not exists plays (
    sb_id      int references games(sb_id) on delete cascade,
    drive_num  int,
    play_num   int,
    team       text,
    quarter    int,
    clock      text,
    down       int,
    distance   int,
    yardline   numeric(5,2),
    play_type  text,
    yards      int,
    air_yards  int,
    yac        int,
    is_td      bool, is_turnover bool, is_sack bool, is_penalty bool,
    play_text  text,
    primary key (sb_id, drive_num, play_num)
);
create index if not exists idx_plays_team on plays(team);

-- ============================================================
-- 5. scoring_plays
-- ============================================================
create table if not exists scoring_plays (
    sb_id    int references games(sb_id) on delete cascade,
    team     text,
    quarter  int,
    clock    text,
    score_type text,
    points   int,
    pat_type text,
    pat_made bool,
    is_4pt_fg bool,
    play_text text,
    home_score_after int,
    away_score_after int,
    primary key (sb_id, quarter, clock, team)
);

-- ============================================================
-- 6. player_game_stats
-- ============================================================
create table if not exists player_game_stats (
    sb_id     int references games(sb_id) on delete cascade,
    team      text not null,
    side      text not null check (side in ('home','away')),
    player_id text,
    name      text,
    jersey    text,
    position  text,
    stat_pass_att int, stat_pass_comp int, stat_pass_yds int, stat_pass_td int,
    stat_pass_int int, stat_pass_long int, stat_pass_rating numeric(5,2),
    stat_rush_att int, stat_rush_yds int, stat_rush_td int, stat_rush_long int,
    stat_rec int, stat_rec_yds int, stat_rec_td int, stat_rec_long int,
    stat_yac int, stat_targets int, stat_drops int, stat_throwaways int,
    stat_solo int, stat_total_tackles int, stat_assists int,
    stat_sacks numeric(4,1), stat_tfl numeric(4,1),
    stat_int int, stat_pd int,
    stat_fum_forced int, stat_fum_recov int,
    stat_punt_att int, stat_punt_yds int, stat_punt_long int, stat_punt_avg numeric(5,2),
    stat_kr int, stat_kr_yds int, stat_kr_long int,
    stat_pr int, stat_pr_yds int, stat_pr_long int,
    stat_fg_made int, stat_fg_att int,
    stat_fg_4pt_made int, stat_fg_4pt_att int,
    stat_pat_made int, stat_pat_att int,
    primary key (sb_id, team, player_id)
);
create index if not exists idx_pgs_player on player_game_stats(name);

-- ============================================================
-- 7. officials
-- ============================================================
create table if not exists officials (
    sb_id   int references games(sb_id) on delete cascade,
    role    text not null,
    name    text,
    uniform int,
    primary key (sb_id, role)
);

-- ============================================================
-- 8. game_rules
-- ============================================================
create table if not exists game_rules (
    sb_id      int references games(sb_id) on delete cascade,
    rule_key   text not null,
    rule_value text,
    primary key (sb_id, rule_key)
);

-- ============================================================
-- 9. team_ratings
-- ============================================================
create table if not exists team_ratings (
    snapshot_date date not null,
    team          text not null,
    elo           numeric(8,2),
    games_played  int,
    off_ppd_adj   numeric(6,3),
    def_ppd_adj   numeric(6,3),
    n_efficiency  int,
    primary key (snapshot_date, team)
);

-- ============================================================
-- 10. odds_snapshots
-- ============================================================
create table if not exists odds_snapshots (
    snapshot_ts   timestamptz not null,
    event_id      text not null,
    sb_id         int,
    home_team     text,
    away_team     text,
    commence_time timestamptz,
    market_key    text not null,
    book          text not null,
    name          text,
    point         numeric(6,2),
    price         numeric(8,2),
    last_update   timestamptz,
    primary key (snapshot_ts, event_id, market_key, book, name)
);
create index if not exists idx_odds_event on odds_snapshots(event_id);
create index if not exists idx_odds_sb on odds_snapshots(sb_id);

-- ============================================================
-- 11. predictions
-- ============================================================
create table if not exists predictions (
    run_id        text not null,
    sb_id         int references games(sb_id) on delete cascade,
    model_spread  numeric(6,2),
    model_total   numeric(6,2),
    home_wp       numeric(6,4),
    away_wp       numeric(6,4),
    home_score_proj numeric(6,2),
    away_score_proj numeric(6,2),
    components    jsonb,
    weight_used   numeric(4,3),
    created_at    timestamptz default now(),
    primary key (run_id, sb_id)
);

-- ============================================================
-- 12. derived_lines
-- ============================================================
create table if not exists derived_lines (
    run_id        text not null,
    sb_id         int references games(sb_id) on delete cascade,
    h1_spread     numeric(6,2),
    h1_total      numeric(6,2),
    home_team_total numeric(6,2),
    away_team_total numeric(6,2),
    created_at    timestamptz default now(),
    primary key (run_id, sb_id)
);

-- ============================================================
-- 13. edge_log
-- ============================================================
create table if not exists edge_log (
    run_id        text not null,
    sb_id         int references games(sb_id) on delete cascade,
    market        text not null,
    side          text not null,
    label         text,
    book          text,
    american_odds numeric(8,2),
    decimal_odds  numeric(8,4),
    p_model       numeric(7,5),
    p_market      numeric(7,5),
    edge_prob     numeric(7,5),
    edge_ev       numeric(7,5),
    full_kelly_pct numeric(8,5),
    stake_pct     numeric(8,5),
    passes_threshold bool,
    created_at    timestamptz default now(),
    primary key (run_id, sb_id, market, side, book)
);

-- ============================================================
-- 14. results_calibration
-- ============================================================
create table if not exists results_calibration (
    sb_id              int references games(sb_id) on delete cascade primary key,
    closing_spread     numeric(6,2),
    closing_total      numeric(6,2),
    model_spread       numeric(6,2),
    model_total        numeric(6,2),
    actual_margin      int,
    actual_total       int,
    spread_error       numeric(6,2),
    total_error        numeric(6,2),
    market_spread_error numeric(6,2),
    market_total_error  numeric(6,2),
    created_at         timestamptz default now()
);
