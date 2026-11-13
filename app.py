import time
import pandas as pd
import streamlit as st
from nba_data import CATEGORIES, DB_PATH, combined_table, fetch_category, save_dataset, status_table
from cloud_db import label as database_label
import re
import os
from espn_fantasy import fetch_fantasy, save_fantasy, load_fantasy, merge_fantasy, fetch_player_news
from datetime import datetime, time as clock_time, timedelta, timezone
from zoneinfo import ZoneInfo

st.set_page_config(page_title='NBA Fantasy Analytics', page_icon='🏀', layout='wide')
st.title('🏀 NBA Fantasy Analytics')

with st.sidebar:
    st.header('View filters')
    earliest = st.number_input('Earliest starting year', min_value=1996, max_value=2026, value=2016)
    seasons = [f'{y}-{str(y + 1)[-2:]}' for y in range(int(earliest), 2027)]
    selected_seasons = st.multiselect('Seasons to view', seasons, default=seasons, help='Choose the seasons to load into NBA Statistics.')
    selected_categories = st.multiselect('Categories to view', CATEGORIES, default=CATEGORIES)

# Load saved ESPN metadata before drawing any views so the same team focus
# controls highlighting, fantasy analysis and replacement recommendations.
def secret_or_env(name):
    try:
        return st.secrets.get(name, os.environ.get(name, ''))
    except (FileNotFoundError, KeyError):
        return os.environ.get(name, '')

fantasy_league = secret_or_env('ESPN_LEAGUE_ID') or '1609816826'
fantasy_year = '2027'

# Cache expensive database reads across Streamlit reruns.
@st.cache_data(ttl=600, show_spinner=False)
def cached_fantasy(year, league):
    return load_fantasy(year, league)

@st.cache_data(ttl=120, show_spinner=False)
def cached_status():
    return status_table()


def dataset_revision():
    """Reuse the already-cached status query as a data-change signature."""
    status = cached_status()
    return tuple(status[['Season', 'Category', 'Updated']].itertuples(index=False, name=None)) if not status.empty else ()


@st.cache_data(ttl=600, max_entries=2, show_spinner=False)
def cached_expected_statistics(revision):
    """Read persisted expected stats; rebuild only affected seasons after source changes."""
    from nba_data import expected_statistics_table
    return expected_statistics_table()


@st.cache_data(ttl=600, max_entries=12, show_spinner=False)
def cached_combined(season_tuple, category_tuple, revision=None):
    if revision is None:
        revision = dataset_revision()
    expected = cached_expected_statistics(revision)
    return combined_table(list(season_tuple), list(category_tuple), expected=expected)

@st.cache_data(ttl=600, max_entries=4, show_spinner=False)
def cached_main_fantasy_merge(season_tuple, category_tuple, fantasy_year, fantasy_updated, fantasy_snapshot, revision):
    """Avoid the expensive ESPN name-by-name merge on every Quick View click."""
    source = cached_combined(season_tuple, category_tuple, revision)
    return merge_fantasy(source, fantasy_snapshot, fantasy_year)


@st.cache_data(ttl=1800, show_spinner=False)
def cached_player_news(espn_id):
    return fetch_player_news(espn_id)


def season_end_year(values):
    """Return ending year for NBA season labels such as 2026-27."""
    labels = values.astype(str).str.strip()
    starts = pd.to_numeric(labels.str.extract(r'^(\d{4})[-–]\d{2,4}$', expand=False), errors='coerce')
    numeric = pd.to_numeric(labels, errors='coerce')
    return (starts + 1).fillna(numeric)


@st.cache_data(ttl=600, show_spinner=False)
def cached_quick_view_pool(seasons, revision):
    """Load the historical player pool once for Quick View interactions."""
    pool = cached_combined(seasons, tuple(CATEGORIES)).copy()
    if 'Player ID' in pool:
        pool['_quick_id'] = pool['Player ID'].astype(str)
    if 'Season' in pool:
        pool['_quick_year'] = season_end_year(pool['Season'])
    return pool


@st.cache_data(ttl=600, show_spinner=False)
def cached_quick_player_rows(seasons, player_id, revision):
    """Cache one player's historical rows; avoid repeated full-table scans."""
    pool = cached_quick_view_pool(seasons, dataset_revision())
    if '_quick_id' not in pool or 'Season' not in pool.columns:
        return pool.iloc[0:0].copy()
    return pool.loc[pool['_quick_id'].eq(str(player_id))].drop(
        columns=['_quick_id', '_quick_year'], errors='ignore'
    ).sort_values('Season', ascending=False).reset_index(drop=True)


@st.cache_data(ttl=600, show_spinner=False)
def cached_quick_all_ranks(seasons, year, position_lookup=(), revision=()):
    """Rank players by season-wide Fppg/Fpts metrics, overall and by primary position.

    position_lookup is an immutable (normalized name, primary position) tuple,
    so ESPN roster updates correctly invalidate this cache entry.
    """
    if year is None:
        return {}
    pool = cached_quick_view_pool(seasons, dataset_revision())
    metrics = ('Fppg', 'Fpts', 'xFppg', 'xFpts')
    if not {'_quick_year', '_quick_id'}.issubset(pool.columns):
        return {}
    available = [stat for stat in metrics if stat in pool.columns]
    cols = ['_quick_id', *available]
    if 'Player' in pool.columns:
        cols.append('Player')
    if 'Positions' in pool.columns:
        cols.append('Positions')
    peers = pool.loc[pool['_quick_year'].eq(int(year)), cols].drop_duplicates(subset=['_quick_id']).copy()
    # ESPN's Default Position is preferred; Positions fallback uses the first
    # eligible position. Never treat an unknown position as a shared group.
    from espn_fantasy import _normalized_name
    lookup = dict(position_lookup)
    names = peers['Player'].map(_normalized_name) if 'Player' in peers else pd.Series('', index=peers.index)
    from_espn = names.map(lookup).fillna('')
    fallback = (peers['Positions'].fillna('').astype(str).str.split(',').str[0].str.strip()
                if 'Positions' in peers else pd.Series('', index=peers.index))
    peers['_primary_pos'] = from_espn.where(from_espn.ne(''), fallback)
    peers['_primary_pos'] = peers['_primary_pos'].where(peers['_primary_pos'].isin(('PG', 'SG', 'SF', 'PF', 'C')), '')
    result = {}
    for stat in available:
        values = pd.to_numeric(peers[stat], errors='coerce')
        all_ranks = values.rank(method='min', ascending=False)
        pos_ranks = values.groupby(peers['_primary_pos'].replace('', pd.NA), dropna=True).rank(
            method='min', ascending=False)
        result[stat] = {
            'All': dict(zip(peers['_quick_id'], all_ranks)),
            'Pos': dict(zip(peers['_quick_id'], pos_ranks)),
        }
    return result


@st.cache_data(ttl=600, show_spinner=False)
def cached_player_options(player_frame):
    """Prepare the selector once rather than sorting player names per selection."""
    players = player_frame[['Player', 'Player ID']].dropna().drop_duplicates().sort_values('Player')
    return [(str(name), str(player_id)) for name, player_id in
            players[['Player', 'Player ID']].itertuples(index=False, name=None)]


@st.cache_data(ttl=600, show_spinner=False)
def cached_espn_player_lookup(fantasy_snapshot):
    """Normalize ESPN player names once, then look them up in O(1) per selection."""
    from espn_fantasy import _normalized_name
    if fantasy_snapshot.empty or 'ESPN Player' not in fantasy_snapshot:
        return {}
    matches = {}
    for row in fantasy_snapshot.to_dict('records'):
        key = _normalized_name(row.get('ESPN Player', ''))
        if key:
            matches.setdefault(key, []).append(row)
    return matches

try:
    fantasy_frame, fantasy_updated = cached_fantasy(fantasy_year, fantasy_league)
except Exception:
    fantasy_frame, fantasy_updated = pd.DataFrame(), None

available_fantasy_teams = sorted({str(t).strip() for t in fantasy_frame.get('Fantasy Team', pd.Series(dtype=str)).dropna()
                                  if str(t).strip() and str(t).strip() != 'FA'}, key=str.casefold)
preferred_moneyball = next((t for t in available_fantasy_teams if 'moneyball' in t.casefold()), None)
team_options = ([preferred_moneyball] if preferred_moneyball else []) + [
    t for t in available_fantasy_teams if t != preferred_moneyball]
if not team_options:
    team_options = ['Moneyball']
if st.session_state.get('focus_fantasy_team') not in team_options:
    st.session_state['focus_fantasy_team'] = team_options[0]

def stat_format(column, mode, percent=False):
    # Fpts/xFpts remain season totals even in Per Game mode.
    decimals = (2 if column in ('Fppg', 'xFppg') else
                0 if mode == 'Totals' or column in ('Fpts', 'xFpts', 'GP') else 1)
    def render(value):
        if pd.isna(value):
            return ''
        return f'{float(value):,.{decimals}f}' + ('%' if percent else '')
    return render


# Hover explanations are based on the formulas in nba_data.py.
EXPECTED_STAT_HELP = {
    'xFG%': 'Shot-quality expected field-goal percentage from NBA Shot Difficulty; falls back to actual FG% when unavailable.',
    'x3P%': 'Shot-quality expected three-point percentage; falls back to actual 3PT% when unavailable.',
    'xFT%': 'Expected FT% = (weighted historical FTM + 50 × league FT%) / (weighted historical FTA + 50). History weights: 1.0, 0.75, 0.35.',
    'xFGM': 'Expected field goals made = xFG% × FGA.',
    'x3PM': 'Expected three-pointers made = x3P% × 3PA.',
    'xFTM': 'Expected free throws made = xFT% × FTA.',
    'xPTS': 'Expected points = 2 × xFGM + xFTM + x3PM.',
    'xREB': 'Expected rebounds = (DREB chances − deferred DREB chances) × league DREB conversion rate + (OREB chances − deferred OREB chances) × league OREB conversion rate.',
    'xAST': 'Expected assists = potential assists × league assists / league potential assists (within the season).',
    'xSTL': 'Expected steals = minutes × adjusted player steal rate + deflections × fitted deflection coefficient. Player rate is smoothed toward the league rate using 500 minutes.',
    'xBLK': 'Expected blocks = contested shots × league blocks / league contested shots (within the season).',
    'xTO': 'Expected turnovers = touches × (weighted historical turnovers + 1000 × league turnovers-per-touch) / (weighted historical touches + 1000).',
    'xFpts': 'Expected fantasy points = xPTS + 2×xFGM − FGA + xFTM − FTA + x3PM + xREB + 2×xAST + 4×xSTL + 4×xBLK − 2×xTO.',
    'xFppg': 'Expected fantasy points per game = xFpts / GP.',
    '+-Fppg': 'Expected fantasy points per game minus actual fantasy points per game = xFppg − Fppg.',
}

def stat_header_config(columns, selected=None, descending=True, overrides=None):
    """Keep native Streamlit click-to-sort; append filter arrow to the chosen label."""
    configs = dict(overrides or {})
    for name in columns:
        if name in configs:
            continue
        label = str(name) + (' ↓' if descending else ' ↑') if name == selected else str(name)
        help_text = EXPECTED_STAT_HELP.get(str(name))
        if help_text or name == selected:
            configs[name] = st.column_config.Column(label=label, help=help_text)
    return configs

def player_headshot(player_id):
    """Public NBA CDN image; no separate API calls or downloads required."""
    if pd.isna(player_id):
        return None
    match = re.fullmatch(r"[0-9]+", str(player_id).strip().removesuffix('.0'))
    return (f"https://cdn.nba.com/headshots/nba/latest/260x190/{match.group()}.png"
            if match else None)



NBA_TEAM_IDS = {
    'ATL': 1610612737, 'BOS': 1610612738, 'BKN': 1610612751,
    'CHA': 1610612766, 'CHI': 1610612741, 'CLE': 1610612739,
    'DAL': 1610612742, 'DEN': 1610612743, 'DET': 1610612765,
    'GSW': 1610612744, 'HOU': 1610612745, 'IND': 1610612754,
    'LAC': 1610612746, 'LAL': 1610612747, 'MEM': 1610612763,
    'MIA': 1610612748, 'MIL': 1610612749, 'MIN': 1610612750,
    'NOP': 1610612740, 'NYK': 1610612752, 'OKC': 1610612760,
    'ORL': 1610612753, 'PHI': 1610612755, 'PHX': 1610612756,
    'POR': 1610612757, 'SAC': 1610612758, 'SAS': 1610612759,
    'TOR': 1610612761, 'UTA': 1610612762, 'WAS': 1610612764,
}

def nba_team_logo(abbreviation):
    abbr = str(abbreviation or '').strip().upper()
    abbr = {'GS': 'GSW', 'NY': 'NYK', 'SA': 'SAS', 'NO': 'NOP',
            'UTAH': 'UTA', 'PHO': 'PHX', 'BRK': 'BKN'}.get(abbr, abbr)
    team_id = NBA_TEAM_IDS.get(abbr)
    return f'https://cdn.nba.com/logos/nba/{team_id}/primary/L/logo.svg' if team_id else None

# Display names are applied only to the UI and exported CSV.
# SQLite keys and formula inputs keep their original names for compatibility.
HEADER_NAMES = {
    'Pot_AST': 'Potential AST',
    'GP_RANK': 'GP Rank',
    'FG3_PCT': '3PT%',
    'FG_PCT': 'FG%',
    'FT_PCT': 'FT%',
    'PLUS_MINUS': '+/-',
    'OREB_CHANCE_PCT_ADJ': 'Adj OREB Chance%',
    'DREB_CHANCE_PCT_ADJ': 'Adj DREB Chance%',
    'REB_CHANCE_PCT_ADJ': 'Adj REB Chance%',
    'AVG_REB_DIST': 'Avg REB Distance',
    'AVG_OREB_DIST': 'Avg OREB Distance',
    'AVG_DREB_DIST': 'Avg DREB Distance',
    'PCT_AST': 'AST%',
    'PCT_REB': 'REB%',
    'PCT_PTS': 'PTS%',
    'PTS_OFF_TOV': 'PTS Off TO',
    'PTS_2ND_CHANCE': '2nd Chance PTS',
    'PTS_FB': 'Fast Break PTS',
    'PTS_PAINT': 'Paint PTS',
    'CFID': 'CF ID',
    'CFPARAMS': 'CF Params',
}


def readable_header(name):
    if name in HEADER_NAMES:
        return HEADER_NAMES[name]
    if '_' not in name:
        return name
    # Preserve stat abbreviations; avoid breaking internal formulas by only
    # applying this to the displayed dataframe and CSV export.
    return re.sub(r'\s+', ' ', name.replace('_', ' ')).strip()


def display_headers(df):
    renamed = df.rename(columns={c: readable_header(c) for c in df.columns})
    # Ensure unique display names when source categories have overlapping labels.
    if renamed.columns.duplicated().any():
        counts = {}
        unique = []
        for col in renamed.columns:
            counts[col] = counts.get(col, 0) + 1
            unique.append(col if counts[col] == 1 else f'{col} ({counts[col]})')
        renamed.columns = unique
    return renamed


# Presentation-only conversion: the stored season totals and expected-stat
# calculations are never modified.  The NBA source is downloaded in Totals mode.
# A rate, percentage, average, ranking or per-touch measurement is not a total.
PER_GAME_UNCHANGED = {
    'Season', 'Player ID', 'Team ID', 'ESPN ID', 'AGE', 'GP', 'Fpts', 'xFpts', 'Fppg', 'xFppg', '+-Fppg',
    'FG%', '3PT%', 'FT%', 'xFG%', 'x3P%', 'xFT%', 'x2P%',
    'Made xFG%', 'Miss xFG%', 'MIN xFG% Made',
    '2PT Made xFG%', '2PT Miss xFG%',
    '3PT Made xFG%', '3PT Miss xFG%',
    'FG%+', '3P%+',
}


def is_count_stat(column):
    """Only season totals are divided by GP; do not scale rates/identifiers."""
    if column in PER_GAME_UNCHANGED:
        return False
    low = column.lower()
    if any(token in low for token in (
        '%', 'pct', 'percent', 'average', 'avg ', 'per ', 'per_', 'per-',
        'ratio', 'rate', 'rank', 'distance', 'dist', 'frequency', 'freq',
        'percentage', 'efficiency', 'speed', 'quality', 'angle',
    )):
        return False
    if low.endswith((' id', '_id', 'id')) or low.startswith('cf'):
        return False
    return True


def as_per_game(frame):
    """Convert total numeric stats to per-game without mutating original data."""
    result = frame.copy()
    if 'GP' not in result.columns:
        return result
    gp = pd.to_numeric(result['GP'], errors='coerce').replace(0, float('nan'))
    for col in result.columns:
        if col in ('GP', 'Season') or not is_count_stat(col):
            continue
        if not pd.api.types.is_numeric_dtype(result[col]):
            continue
        result[col] = (pd.to_numeric(result[col], errors='coerce') / gp).round(2)
    return result


# Display order only; original dataset names and calculations are unchanged.
STAT_PAIRS = [
    ('Fpts', 'xFpts'), ('Fppg', 'xFppg'),
    ('PTS', 'xPTS'), ('FGM', 'xFGM'), ('FTM', 'xFTM'),
    ('3PM', 'x3PM'), ('REB', 'xREB'), ('AST', 'xAST'),
    ('STL', 'xSTL'), ('BLK', 'xBLK'), ('TOV', 'xTO'),
    ('FG%', 'xFG%'), ('3PT%', 'x3P%'), ('FT%', 'xFT%'),
]


def ordered_stats_columns(columns):
    columns = list(columns)
    result = []

    def add(column):
        if column in columns and column not in result:
            result.append(column)

    for column in ('Headshot', 'Season', 'Player', 'Team', 'Fantasy Team', 'Positions', 'AGE', 'GP', 'MIN', 'USG%'):
        add(column)
    for actual, expected in STAT_PAIRS:
        add(actual)
        add(expected)
        if actual == 'Fppg':
            add('+-Fppg')
    for column in columns:
        if column not in ('Player ID', 'Team ID', 'ESPN ID'):
            add(column)
    add('Player ID')
    add('Team ID')
    add('ESPN ID')
    return result



# Presentation-only allowlist. All source fields stay in SQLite and remain
# available to the expected-stat calculations.
VISIBLE_STATS = [
    'Season', 'Player', 'Team', 'Fantasy Team', 'Positions', 'AGE', 'GP', 'MIN', 'USG%',
    'Fpts', 'xFpts', 'Fppg', 'xFppg', '+-Fppg',
    'PTS', 'xPTS', 'FGM', 'xFGM', 'FTM', 'xFTM', '3PM', 'x3PM',
    'REB', 'xREB', 'AST', 'xAST', 'STL', 'xSTL', 'BLK', 'xBLK',
    'TOV', 'xTO', 'FG%', 'xFG%', '3PT%', 'x3P%', 'FT%', 'xFT%',
    'FGA', '3PA', 'FTA', 'OREB', 'DREB', 'PF', '+-', 'DD2', 'TD3',
    'FG%+', '3P%+', 'Contested Shots', 'Contested 2PT',
    'Contested 3PT', 'Deflections', 'Charges Drawn',
    'Contested OREB', 'Uncontested OREB', 'OREB Chances',
    'OREB Chances Deferred', 'Contested DREB', 'Uncontested DREB',
    'DREB Chances', 'DREB Chances Deferred', 'Contested REB',
    'Uncontested REB', 'REB Chances', 'REB Chances Deferred',
    'Passes Made', 'Passes Received', 'Potential AST', 'Touches',
    'Player ID', 'Team ID',
]

# A few versions of the NBA source use alternate names for the same metric.
DISPLAY_ALIASES = {
    '+/-': '+-', 'PLUS_MINUS': '+-', 'Pot AST': 'Potential AST',
    'Pot_AST': 'Potential AST', 'FG3%': '3PT%',
}


def focused_stat_columns(frame):
    """Hide unwanted fields only in the UI/CSV, not in SQLite."""
    frame = frame.rename(columns={
        key: value for key, value in DISPLAY_ALIASES.items()
        if key in frame.columns and value not in frame.columns
    })
    return frame[[c for c in VISIBLE_STATS if c in frame.columns]]


def eastern_now():
    return datetime.now(ZoneInfo('America/New_York'))


def active_season(now):
    # NBA regular seasons begin in October; July–September belong to the
    # preceding season for dashboard freshness purposes.
    year = now.year if now.month >= 10 else now.year - 1
    return f'{year}-{str(year + 1)[-2:]}'


def freshness_cutoff(now):
    # The first 2 AM Eastern that has occurred, including DST transitions.
    day = now.date() if now.time() >= clock_time(2, 0) else now.date() - timedelta(days=1)
    return datetime.combine(day, clock_time(2, 0), tzinfo=ZoneInfo('America/New_York'))


def dataset_status(season, category, timestamps, now):
    if category == 'Shot Difficulty' and int(season[:4]) < 2025:
        return 'N/A'
    updated = timestamps.get((season, category))
    if updated is None:
        return '❌ Missing'
    if season == active_season(now):
        try:
            parsed = datetime.fromisoformat(str(updated).replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            if parsed.astimezone(ZoneInfo('America/New_York')) < freshness_cutoff(now):
                return '🔄 Need Update'
        except (ValueError, TypeError, OverflowError):
            return '🔄 Need Update'
    return '✅ Saved'


def completeness_dashboard(season_labels):
    saved = cached_status()
    timestamps = {(str(r['Season']), str(r['Category'])): r['Updated']
                  for _, r in saved.iterrows()}
    downloaded = set(timestamps)
    now = eastern_now()
    current = active_season(now)
    rows = []
    for season in reversed(season_labels):
        row = {'Season': season}
        for category in CATEGORIES:
            row[category] = dataset_status(season, category, timestamps, now)
        rows.append(row)
    table = pd.DataFrame(rows)
    with st.expander('📋 Data Completeness Dashboard', expanded=False):
        st.caption(f'Current season: {current}. After 2:00 AM Eastern each day, current-season datasets last downloaded before that cutoff show **🔄 Need Update** until refreshed. This is a scheduled reminder, not confirmation that the NBA has published new statistics.')
        if table.empty:
            st.info('No seasons in the selected range.')
            return
        st.dataframe(table, height=460, hide_index=True, use_container_width=True)
        total = sum(1 for r in rows for c in CATEGORIES if r[c] != 'N/A')
        have = sum(1 for r in rows for c in CATEGORIES if r[c] == '✅ Saved')
        pending = sum(1 for r in rows for c in CATEGORIES if r[c] == '🔄 Need Update')
        a, b, c, d = st.columns(4)
        a.metric('Up to date', have)
        b.metric('Need update', pending)
        c.metric('Missing', total - have - pending)
        d.metric('Coverage', f'{(have + pending) / total:.0%}' if total else '—')
        st.caption('Need Update datasets remain available for calculations and viewing.')
        outdated = [cat for cat in CATEGORIES if dataset_status(current, cat, timestamps, now) == '🔄 Need Update']
        if st.button(f'🔄 Refresh Outdated Current-Season Data ({len(outdated)})',
                     disabled=not outdated, key='refresh_outdated', type='primary'):
            progress = st.progress(0)
            successes, failures = [], []
            for idx, category in enumerate(outdated, 1):
                with st.spinner(f'Refreshing {current} / {category}...'):
                    try:
                        result = fetch_category(current, category)
                        save_dataset(current, category, result)
                        cached_combined.clear()
                        cached_expected_statistics.clear()
                        cached_status.clear()
                        successes.append(f'{current} / {category}: {len(result)} rows')
                    except Exception as exc:
                        failures.append(f'{current} / {category}: {type(exc).__name__}: {exc}')
                progress.progress(idx / len(outdated))
                if idx < len(outdated):
                    time.sleep(2)
            st.session_state['refresh_outdated_result'] = (successes, failures)
            st.rerun()
        if 'refresh_outdated_result' in st.session_state:
            successes, failures = st.session_state.pop('refresh_outdated_result')
            if successes:
                st.success(f'Refreshed {len(successes)} dataset(s): ' + '; '.join(successes))
            for failure in failures:
                st.error(f'Refresh failed: {failure}')
        st.caption('The button refreshes only saved current-season datasets marked Need Update. Missing datasets must be downloaded using the selection above.')
        st.caption('Historical requirements: xFTM uses Standard; xSTL uses Standard + Hustle; xTO uses Standard + Touches for the current and previous two seasons. Full xFpts also requires the current season’s Shot Difficulty, Rebounding and Passing datasets.')
        focus = st.selectbox('Check expected-stat prerequisites for season', list(reversed(season_labels)), key='completeness_focus')
        year = int(focus[:4])
        required = []
        for offset in range(3):
            y = year - offset
            label = f'{y}-{str(y+1)[-2:]}'
            for category in ('Standard', 'Hustle', 'Touches'):
                if (label, category) not in downloaded:
                    required.append(f'{label} / {category}')
        for category in ('Shot Difficulty', 'Rebounding', 'Passing'):
            if (focus, category) not in downloaded:
                required.append(f'{focus} / {category}')
        if year < 2025:
            st.info('xFpts is unavailable before 2025–26 because Shot Difficulty data is unavailable.')
        elif required:
            st.warning('Datasets needed for complete xFpts: ' + ', '.join(required))
        else:
            st.success('All category/season datasets needed for complete xFpts are saved. Individual player values may still be blank when a source row is absent.')
        if focus == current:
            pending_categories = [c for c in CATEGORIES if dataset_status(focus, c, timestamps, now) == '🔄 Need Update']
            if pending_categories:
                st.info('Current-season refresh suggested: ' + ', '.join(pending_categories))


with st.expander('🏆 ESPN Fantasy League Refresh', expanded=False):
    st.caption('ESPN credentials are used only for the request. They are never stored in SQLite or CSV exports.')
    st.caption(f'League {fantasy_league} • Season {fantasy_year} (set ESPN_LEAGUE_ID in secrets or environment)')
    with st.form('espn_refresh'):
        # Keep private ESPN credentials out of the rendered page entirely.
        cookie = secret_or_env('ESPN_S2')
        swid = secret_or_env('ESPN_SWID')
        refresh_espn = st.form_submit_button('Refresh ESPN Fantasy Data')
    if refresh_espn:
        with st.spinner('Downloading ESPN league rosters and player metadata...'):
            try:
                fantasy_frame, info = fetch_fantasy(fantasy_year, fantasy_league, cookie, swid)
                save_fantasy(fantasy_year, fantasy_league, fantasy_frame)
                cached_fantasy.clear()
                cached_player_news.clear()
                st.success(f"Saved {len(fantasy_frame)} ESPN players, {info['rostered']} rostered entries, {info['teams']} fantasy teams, {info['abbreviations']} ESPN team abbreviations; {info['rostered_abbreviations']} rostered players assigned an abbreviation.")
                if not info['abbreviations']:
                    st.warning('ESPN did not return team abbreviations. Check your league team settings in ESPN.')
                st.info(f"ESPN draft picks retrieved: {info.get('draft_picks', 0)}")
                if info.get('draft_error'):
                    st.warning('ESPN draft request failed: ' + info['draft_error'])
                elif not info.get('draft_picks'):
                    st.warning('ESPN returned no draft picks. Check that the league draft is complete.')

                st.rerun()
            except Exception as exc:
                st.error(f'ESPN refresh failed: {type(exc).__name__}: {exc}')
    if fantasy_updated:
        st.caption(f'Last ESPN refresh (UTC): {fantasy_updated}')
        if 'Draft Round' in fantasy_frame.columns:
            st.caption(f"Saved fantasy draft positions: {fantasy_frame['Draft Round'].notna().sum()}")
        else:
            st.warning('Saved ESPN snapshot predates draft support. Refresh ESPN Fantasy Data.')

        st.caption(f'{len(fantasy_frame)} ESPN players available. Roster ownership and injuries reflect the last refresh, not live changes.')
        if 'Fantasy Abbrev' not in fantasy_frame.columns or not fantasy_frame.get('Fantasy Abbrev', pd.Series(dtype=str)).fillna('').astype(str).str.strip().any():
            st.warning('Saved ESPN data has no fantasy team abbreviations. Refresh ESPN Fantasy Data to populate them.')
    else:
        st.info('No saved ESPN data for this league/season. Enter your ESPN cookies and refresh.')

with st.expander('📥 Download / Refresh Data', expanded=False):
    st.caption('Choose the exact season/category combinations to request. Existing downloads remain saved.')
    download_now = datetime.now(ZoneInfo('America/New_York'))
    download_year = download_now.year if download_now.month >= 10 else download_now.year - 1
    fetch_default = f'{download_year}-{str(download_year + 1)[-2:]}'
    download_seasons = [f'{y}-{str(y + 1)[-2:]}' for y in range(2016, 2027)]
    with st.form('download_selection_form'):
        fetch_seasons = st.multiselect('Seasons to download', download_seasons, default=[fetch_default] if fetch_default in download_seasons else download_seasons[-1:])
        fetch_categories = st.multiselect('Categories to download', CATEGORIES, default=CATEGORIES)
        submit_download = st.form_submit_button('Download / Refresh Selected Data', type='primary')
    if submit_download:
        jobs = [(s, c) for s in fetch_seasons for c in fetch_categories]
        if not jobs:
            st.warning('Select at least one season and category.')
        else:
            progress = st.progress(0)
            for index, (season, category) in enumerate(jobs, 1):
                with st.spinner(f'Downloading {season} / {category}...'):
                    try:
                        data = fetch_category(season, category)
                        save_dataset(season, category, data)
                        cached_combined.clear()
                        cached_expected_statistics.clear()
                        cached_status.clear()
                        st.success(f'{season} / {category}: saved {len(data)} player rows')
                    except Exception as exc:
                        st.error(f'{season} / {category}: {type(exc).__name__}: {exc}')
                progress.progress(index / len(jobs))
                if index < len(jobs):
                    time.sleep(2)

@st.fragment(run_every='5m')
def refresh_completeness():
    completeness_dashboard([f'{y}-{str(y + 1)[-2:]}' for y in range(2016, 2027)])

refresh_completeness()

with st.expander('💾 Saved datasets'):
    status = cached_status()
    if status.empty:
        st.info('No datasets downloaded yet.')
    else:
        st.dataframe(display_headers(status), height=460, hide_index=True, use_container_width=True)


focus_fantasy_team = st.selectbox('🏆 Focus fantasy team', team_options, key='focus_fantasy_team')

st.subheader('🏀 NBA Statistics')
if not selected_seasons or not selected_categories:
    st.info('Select at least one season and category in the sidebar.')
else:
    try:
        df = cached_combined(tuple(selected_seasons), tuple(selected_categories))
    except Exception as exc:
        st.error(f'Unable to combine saved datasets: {exc}')
        df = pd.DataFrame()
    if df.empty:
        st.info('No matching saved data yet. Download one or more datasets above.')
    else:
        if not fantasy_frame.empty:
            df, fantasy_matches = cached_main_fantasy_merge(
                tuple(selected_seasons), tuple(selected_categories), fantasy_year,
                str(fantasy_updated), fantasy_frame, dataset_revision())
            fantasy_unmatched = max(0, len(df) - fantasy_matches)
        # Carry current ESPN eligible positions across saved historical seasons,
        # but only for players uniquely matched to the ESPN player pool.
        if not fantasy_frame.empty and 'ESPN Player' in fantasy_frame and 'Positions' in fantasy_frame:
            from espn_fantasy import _normalized_name as _espn_norm
            _position_pool = fantasy_frame[['ESPN Player', 'Positions']].copy()
            _position_pool['_key'] = _position_pool['ESPN Player'].map(_espn_norm)
            _position_pool = _position_pool.loc[
                _position_pool['_key'].ne('') & ~_position_pool['_key'].duplicated(keep=False)
            ].set_index('_key')['Positions']
            _positions_by_player = df['Player'].map(_espn_norm).map(_position_pool)
            _has_position = _positions_by_player.notna() & _positions_by_player.astype(str).str.strip().ne('')
            df.loc[_has_position, 'Positions'] = _positions_by_player.loc[_has_position]
        # Filters are applied after computing the rank, so a team/position
        # filter does not renumber the league-wide player rankings.
        search = st.text_input('Search players')
        _f1, _f2, _f3, _f4, _f5 = st.columns(5)
        with _f1:
            _saved_seasons = sorted(df['Season'].dropna().astype(str).unique(), reverse=True) if 'Season' in df else []
            _season_filter = st.multiselect('Seasons', _saved_seasons,
                default=_saved_seasons[:1], key='main_season_filter_v46')
        with _f2:
            _fantasy_options = sorted(df['Fantasy Team'].dropna().astype(str).unique(), key=str.casefold) if 'Fantasy Team' in df else []
            _fantasy_filter = st.selectbox('Fantasy team', ['All teams'] + _fantasy_options,
                key='main_fantasy_filter_v39')
        with _f3:
            _eligible_filter = st.selectbox('Eligible position',
                ['All', 'PG', 'SG', 'SF', 'PF', 'C', 'G', 'F'], key='main_position_filter_v39')
        with _f4:
            _sort_choices = [c for c in ('Fppg', 'Fpts', 'xFppg', 'xFpts', 'PTS', 'xPTS',
                'REB', 'xREB', 'AST', 'xAST', 'STL', 'xSTL', 'BLK', 'xBLK',
                'TOV', 'xTO', 'GP', 'MIN', 'AGE', 'FG%', 'xFG%', '3PT%', 'x3P%',
                'FT%', 'xFT%') if c in df.columns]
            _sort_stat = st.selectbox('Sort statistic', _sort_choices,
                index=_sort_choices.index('Fppg') if 'Fppg' in _sort_choices else 0,
                key='main_sort_stat_v39') if _sort_choices else None
        with _f5:
            _sort_order = st.selectbox('Sort Order', ['Descending', 'Ascending'],
                key='main_sort_order_v39')
        stat_mode = st.segmented_control('Stat display', ['Per Game', 'Totals'],
            default='Per Game', key='stat_display_mode_v39') or 'Per Game'
        # Always show headshots when a Player ID exists. No toggle.
        table_data = as_per_game(df) if stat_mode == 'Per Game' else df.copy()
        # Restrict the ranking pool to the seasons selected in the table.
        # Do this BEFORE ranking, but BEFORE fantasy team, position and search
        # filters so those filters preserve each player's league-wide rank.
        if 'Season' in table_data:
            table_data = table_data.loc[
                table_data['Season'].astype(str).isin(_season_filter)
            ].copy()
        # Rank the selected-season pool using the chosen display mode and
        # direction. Keep ties on the same rank; missing values stay unranked.
        # Header-click sorting is presentation-only and does not change Rk.
        if _sort_stat and _sort_stat in table_data:
            _sort_numbers = pd.to_numeric(table_data[_sort_stat], errors='coerce')
            table_data['Rk'] = _sort_numbers.rank(method='min',
                ascending=_sort_order == 'Ascending').astype('Int64')
            table_data['_sort_value'] = _sort_numbers
        else:
            table_data['Rk'] = pd.Series(pd.NA, index=table_data.index, dtype='Int64')
            table_data['_sort_value'] = pd.Series(float('nan'), index=table_data.index)
        if _fantasy_filter != 'All teams' and 'Fantasy Team' in table_data:
            table_data = table_data[table_data['Fantasy Team'].fillna('').astype(str).eq(_fantasy_filter)]
        if _eligible_filter != 'All' and 'Positions' in table_data:
            _pos_groups = {'G': {'PG', 'SG'}, 'F': {'SF', 'PF'}}
            _wanted = _pos_groups.get(_eligible_filter, {_eligible_filter})
            table_data = table_data[table_data['Positions'].fillna('').astype(str).map(
                lambda x: bool(set(re.findall(r'\b(?:PG|SG|SF|PF|C)\b', x.upper())) & _wanted))]
        if search and 'Player' in table_data:
            table_data = table_data[table_data['Player'].astype(str).str.contains(
                search, case=False, regex=False, na=False)]
        table_data = table_data.sort_values('_sort_value',
            ascending=_sort_order == 'Ascending', na_position='last', kind='stable')
        # Preserve the filtered fantasy ownership mask for player-name styling.
        filtered_fantasy_team = (table_data['Fantasy Team'].copy() if 'Fantasy Team' in table_data
                                 else pd.Series('', index=table_data.index))
        table_data = table_data.drop(columns=['_sort_value'])
        # Show only ESPN-eligible positions and append injury status to the
        # player's name. Keep the original df untouched for calculations.
        if 'Player' in table_data.columns and 'Injury' in table_data.columns:
            injury_labels = table_data['Injury'].fillna('').astype(str).str.strip()
            injury_labels = injury_labels.where(~injury_labels.str.lower().isin(['nan', 'none', '<na>']), '')
            table_data['Player'] = (table_data['Player'].fillna('').astype(str)
                                    + injury_labels.map(lambda status: f' ({status})' if status else ''))
        table_data = table_data.drop(columns=['Fantasy Team', 'Injury', 'Roster Slot', 'ESPN ID'], errors='ignore')
        # Keep Usage in SQLite, but show only USG% in the combined table.
        # Other categories and all expected-stat inputs are unaffected.
        table_data = table_data.drop(
            columns=[c for c in table_data.columns if c.startswith('Usage ')],
            errors='ignore',
        )
        # Show ESPN's fantasy-team abbreviation, not the full team name.
        # Older snapshots may lack this field; warn above and refresh ESPN.
        abbrev = (table_data.pop('Fantasy Abbrev') if 'Fantasy Abbrev' in table_data.columns
                  else pd.Series('', index=table_data.index))
        abbrev = abbrev.fillna('').astype(str).str.strip().str.upper()
        # A player without a fantasy owner is a free agent; historical seasons
        # and unmatched ESPN names remain blank rather than falsely labeled FA.
        if 'Fantasy Team' in df.columns:
            free_agent = df['Fantasy Team'].fillna('').astype(str).eq('FA')
            abbrev = abbrev.mask(free_agent & abbrev.eq(''), 'FA')
        insert_at = list(table_data.columns).index('Team') + 1 if 'Team' in table_data else 0
        table_data['Fantasy'] = abbrev
        visible = display_headers(table_data)
        column_settings = {}
        if 'Player ID' in table_data.columns:
            column_settings['Headshot'] = st.column_config.ImageColumn('Headshot', width='small')
        column_settings['Player'] = st.column_config.TextColumn('Player', width=250)
        # Display name · NBA team · eligible positions without brackets.
        # The underlying table_data retains Team and Positions for filtering,
        # exports, and Player Quick View row selection.
        if 'Player' in visible:
            _team_labels = table_data['Team'].fillna('').astype(str).str.strip() if 'Team' in table_data else pd.Series('', index=table_data.index)
            _pos_labels = table_data['Positions'].fillna('').astype(str).str.strip() if 'Positions' in table_data else pd.Series('', index=table_data.index)
            _identity = pd.Series([
                ' · '.join(part for part in (team, pos) if part)
                for team, pos in zip(_team_labels, _pos_labels)
            ], index=table_data.index)
            _focused_players = filtered_fantasy_team.fillna('').astype(str).str.casefold().eq(focus_fantasy_team.casefold())
            visible['Player'] = (visible['Player'].fillna('').astype(str).to_numpy()
                                 + _identity.map(lambda label: f' · {label}' if label else '').to_numpy())
        visible = focused_stat_columns(visible)
        # The native row-selection control appears before the first data column.
        # Rk is therefore the first data column, followed by headshot and identity.
        visible.insert(0, 'Rk', table_data['Rk'].to_numpy())
        if 'Positions' in visible:
            visible = visible.rename(columns={'Positions': 'Pos'})
        _front = ['Rk', 'Headshot', 'Player', 'AGE', 'Season']
        visible = visible[[c for c in _front if c in visible] +
                          [c for c in visible if c not in _front and c not in ('Fantasy', 'Team', 'Pos')] +
                          (['Fantasy'] if 'Fantasy' in visible else [])]
        if 'Headshot' in column_settings:
            visible.insert(1, 'Headshot', table_data['Player ID'].map(player_headshot).to_numpy())
        # Display percentages as 45.2% instead of the underlying 0.452.
        # Preserve the original values for calculations and fantasy comparisons.
        percent_columns = [c for c in visible.columns if '%' in str(c)]
        percent_view = visible.copy()
        for c in percent_columns:
            percent_view[c] = pd.to_numeric(percent_view[c], errors='coerce') * 100
        # Use a stable key for each stat mode to avoid stale table layouts.
        table_key = f'combined_stats_v17_{stat_mode.replace(" ", "_").lower()}'
        # Pandas Styler defaults to six decimals for floating-point values.
        # Explicitly format only numeric columns, leaving the underlying data
        # (and CSV export) unchanged.  Strip unnecessary trailing zeroes.
        numeric_columns = [
            c for c in percent_view.select_dtypes(include='number').columns
            if c not in ('Season', 'Player ID', 'Team ID', 'Rk')
        ]
        formats = {c: stat_format(c, stat_mode, c in percent_columns)
                   for c in numeric_columns}
        formats['Rk'] = lambda value: '' if pd.isna(value) else str(int(value))
        if 'AGE' in percent_view:
            formats['AGE'] = lambda value: '' if pd.isna(value) else f'{float(value):.0f}'
        styled = percent_view.style.format(formats, na_rep='')

        # Preserve injury colors and shade only the Player cells belonging
        # to the currently highlighted fantasy team.
        def injury_name_style(value):
            name = str(value)
            if re.search(r'\s\(DTD\)(?=\s|$)', name, flags=re.IGNORECASE):
                return 'color: #f4c542; font-weight: 700'
            return 'color: #e53935' if re.search(
                r'\s\((?:O|IR|D|Q|SUSP|OUT)\)(?=\s|$)', name, flags=re.IGNORECASE
            ) else ''

        if 'Player' in visible:
            styled = styled.map(injury_name_style, subset=['Player'])
            # A positional list avoids index-alignment issues after sorting/filtering.
            _highlight_flags = _focused_players.fillna(False).to_numpy()
            def highlight_fantasy_player_cells(column):
                return ['background-color: #51321c' if highlighted else ''
                        for highlighted in _highlight_flags]
            styled = styled.apply(highlight_fantasy_player_cells, axis=0, subset=['Player'])
        # Header labels are presentation-only: the underlying column names stay unchanged.
        _selected_header = HEADER_NAMES.get(_sort_stat, _sort_stat) if _sort_stat else None
        column_settings = stat_header_config(
            percent_view.columns, selected=_selected_header,
            descending=_sort_order == 'Descending', overrides=column_settings)
        selection = st.dataframe(
            styled, height=600, hide_index=True, use_container_width=True,
            column_config=column_settings,
            key=f"combined_stats_v084_{stat_mode.replace(' ', '_')}_{st.session_state.get('main_selection_epoch', 0)}",
            row_height=35,
            on_select='rerun', selection_mode='single-row',
        )
        st.caption(f'{len(df):,} available player-season rows • {len(df.columns)} columns • {database_label()}')
        if not fantasy_frame.empty:
            st.caption(f'ESPN fantasy matched {fantasy_matches:,} NBA player-season rows by unique player name • {fantasy_unmatched:,} unmatched player-season rows (ESPN fields blank).')
        selected_player_id = None
        selected_player_season = None
        if selection.selection.rows and 'Player ID' in table_data.columns:
            selected_row = selection.selection.rows[0]
            if 0 <= selected_row < len(table_data):
                selected_player_id = str(table_data.iloc[selected_row]['Player ID'])
                selected_player_season = table_data.iloc[selected_row]['Season'] if 'Season' in table_data else None
                # Clear the checked row after routing its player to Quick View.
                # Changing the widget key recreates the table with no selection.
                st.session_state['regression_quick_view_request'] = selected_player_id
                st.session_state['main_selection_epoch'] = (
                    st.session_state.get('main_selection_epoch', 0) + 1
                )
                st.rerun(scope='app')
        @st.fragment
        def render_player_quick_view():
            if 'Player' in df.columns and 'Player ID' in df.columns:
                with st.expander('🏀 Player Quick View', expanded=True):
                    options = cached_player_options(df[['Player', 'Player ID']])
                    if options:
                        # Regression table can request a player from below this fragment.
                        # Consume once so ordinary Quick View navigation remains independent.
                        requested_id = st.session_state.pop('regression_quick_view_request', None)
                        current_index = next((i for i, option in enumerate(options)
                                              if option[1] == (requested_id or selected_player_id)), None)
                        if current_index is not None:
                            st.session_state['quick_view_player'] = options[current_index]
                        elif st.session_state.get('quick_view_player') not in options:
                            st.session_state['quick_view_player'] = options[0]
                        _player_select_col, _year_select_col, _rank_select_col, _scope_select_col = st.columns([3, 1, 3, 1.4], vertical_alignment='bottom')
                        with _player_select_col:
                            choice = st.selectbox('Search player', options, format_func=lambda x: x[0],
                                                  key='quick_view_player')
                        # Show every saved season for this player, independent of
                        # the sidebar's selected seasons/categories. The selected
                        # season is displayed first, followed by other years.
                        quick_seasons = tuple(sorted(set(cached_status()['Season'].dropna().astype(str))))
                        player_rows = cached_quick_player_rows(quick_seasons, choice[1], dataset_revision())
                        # The year selector is scoped to the player, so switching
                        # players automatically selects that player's latest saved season.
                        season_values = (season_end_year(player_rows['Season'])
                                         if 'Season' in player_rows.columns
                                         else pd.Series(dtype='float64'))
                        available_years = sorted({int(y) for y in season_values.dropna()}, reverse=True)
                        with _year_select_col:
                            if available_years:
                                selected_year = st.selectbox(
                                    'Year', available_years,
                                    format_func=lambda y: f'{y - 1}-{str(y)[-2:]}',
                                    key=f'quick_view_year_{choice[1]}',
                                )
                            else:
                                st.selectbox('Year', ['No saved seasons'], disabled=True)
                                selected_year = None
                        with _rank_select_col:
                            rank_stat = st.segmented_control(
                                'Rank by', ('Fppg', 'Fpts', 'xFppg', 'xFpts'),
                                default='Fppg', key='quick_view_rank_stat',
                            ) or 'Fppg'
                        with _scope_select_col:
                            rank_scope = st.segmented_control(
                                'Rank scope', ('All', 'Pos'), default='All',
                                key='quick_view_rank_scope',
                                help='All ranks against every player; Pos ranks against players with the same primary ESPN position.',
                            ) or 'All'
                        # Keep every saved season for the historical stats table; only
                        # the overview and identity card follow the Year dropdown.
                        overview_rows = (player_rows.loc[
                            season_end_year(player_rows['Season']).eq(selected_year)
                        ].copy() if selected_year is not None else player_rows.iloc[0:0].copy())
                        photo, details = st.columns([1, 4])
                        with photo:
                            url = player_headshot(choice[1])
                            if url:
                                st.image(url, width=190)
                            else:
                                st.caption('No headshot ID available')

                            st.subheader(choice[0])
                            if not overview_rows.empty:
                                _identity_row = overview_rows.iloc[0]
                                _nba_team = str(_identity_row.get('Team', '')).strip()
                                _logo = nba_team_logo(_nba_team)
                                if _nba_team and _nba_team.lower() not in ('nan', 'none'):
                                    _logo_col, _team_col = st.columns([1, 3], gap='small', vertical_alignment='center')
                                    with _logo_col:
                                        if _logo:
                                            st.image(_logo, width=36)
                                    with _team_col:
                                        st.markdown(f'**{_nba_team}**')
                                _position = str(_identity_row.get('Positions', '')).strip()
                                if not _position or _position.lower() in ('nan', 'none'):
                                    _position = ''
                                if not _position and not fantasy_frame.empty and 'Positions' in fantasy_frame:
                                    from espn_fantasy import _normalized_name as _norm_position
                                    _position_matches = cached_espn_player_lookup(fantasy_frame).get(_norm_position(choice[0]), [])
                                    if _position_matches:
                                        _position = str(_position_matches[0].get('Positions', '')).strip()
                                _age = pd.to_numeric(_identity_row.get('AGE'), errors='coerce')
                                _identity_bits = []
                                if _position and _position.lower() not in ('nan', 'none'):
                                    _identity_bits.append(f'Pos: {_position}')
                                if pd.notna(_age):
                                    _identity_bits.append(f'Age: {_age:.0f}')
                                if _identity_bits:
                                    st.caption(' · '.join(_identity_bits))

                            # ESPN league draft position (not the NBA entry draft).
                            # Historical player rows are matched by NBA ID, but
                            # the draft pick is for the current ESPN league season.
                            # Match the current ESPN player pool by normalized name.
                            # This works even when the selected row is a historical
                            # NBA season (which has no merged ESPN ID).
                            from espn_fantasy import _normalized_name, player_news_fallback_url
                            _player_name = str(choice[0])
                            if not fantasy_frame.empty and 'ESPN Player' in fantasy_frame.columns:
                                _matches = cached_espn_player_lookup(fantasy_frame).get(_normalized_name(_player_name), [])
                                _draft_rows = pd.DataFrame(_matches) if len(_matches) == 1 else pd.DataFrame()
                            else:
                                _draft_rows = pd.DataFrame()
                            _espn_id = None
                            if not _draft_rows.empty:
                                _draft = _draft_rows.iloc[0]
                                _espn_id = str(_draft.get('ESPN ID', ''))
                                _rnd, _pick = _draft.get('Draft Round'), _draft.get('Draft Pick')
                                if pd.notna(_rnd) and pd.notna(_pick):
                                    st.caption(f"🏆 Fantasy draft: Rnd {int(_rnd)}, Pick {int(_pick)}"
                                               + (f" (Overall {int(_draft['Draft Overall'])})"
                                                  if pd.notna(_draft.get('Draft Overall')) else ''))
                                else:
                                    st.caption('Fantasy draft: Not drafted / not available')
                            else:
                                st.caption('Fantasy draft: Player not matched to ESPN; refresh ESPN data.')

                            with st.expander('Latest ESPN News', expanded=False):
                                if _espn_id and _espn_id.isdigit():
                                    try:
                                        _articles = cached_player_news(_espn_id)
                                        if _articles:
                                            # Scroll only the news, keeping it inside Player Quick View.
                                            # Height shows approximately one story at a time.
                                            with st.container(height=240, border=True):
                                                for _index, _item in enumerate(_articles):
                                                    if _index:
                                                        st.divider()
                                                    st.markdown(f"**{_item['title']}**")
                                                    if _item.get('date'):
                                                        st.caption(f"{_item.get('source', 'ESPN')} · {_item['date']} UTC")
                                                    if _item.get('summary'):
                                                        st.caption(_item['summary'])
                                                    if _item.get('url'):
                                                        st.link_button('Read on ESPN', _item['url'])
                                        else:
                                            st.caption('No news returned by the ESPN Fantasy news endpoint.')
                                    except Exception as _news_error:
                                        st.caption(f'ESPN Fantasy news unavailable: {type(_news_error).__name__}.')
                                    _fallback = player_news_fallback_url(_espn_id)
                                    if _fallback:
                                        st.link_button('Open ESPN player page / news', _fallback)
                                else:
                                    st.caption('Refresh ESPN data to enable player news.')
                        with details:
                            # Compact season summary instead of the former overview table.
                            # The selected season is the first row, followed by saved history.
                            per_game_cols = ['PTS', 'xPTS', 'REB', 'xREB', 'AST', 'xAST',
                                             'STL', 'xSTL', 'BLK', 'xBLK', 'TOV', 'xTO']
                            rate_cols = ['FG%', 'xFG%', '3PT%', 'x3P%', 'FT%', 'xFT%']
                            quick_pg = player_rows.copy()
                            games = (pd.to_numeric(quick_pg['GP'], errors='coerce').replace(0, float('nan'))
                                     if 'GP' in quick_pg else pd.Series(float('nan'), index=quick_pg.index))
                            for col in per_game_cols + ['MIN']:
                                if col in quick_pg:
                                    quick_pg[col] = pd.to_numeric(quick_pg[col], errors='coerce') / games

                            def quick_display(frame, columns):
                                view = frame[columns].copy()
                                for col in columns:
                                    if col in rate_cols or col == 'USG%':
                                        view[col] = pd.to_numeric(view[col], errors='coerce').map(
                                            lambda v: '' if pd.isna(v) else f'{v * 100:.1f}'.rstrip('0').rstrip('.') + '%')
                                    elif col not in ('Season', 'Team', 'Positions'):
                                        view[col] = pd.to_numeric(view[col], errors='coerce').map(
                                            lambda v: '' if pd.isna(v) else f'{v:,.1f}'.rstrip('0').rstrip('.'))
                                return view

                            if not overview_rows.empty:
                                summary = overview_rows.iloc[0]
                                season = str(summary.get('Season', '')).strip()
                                if season and season.lower() not in ('nan', 'none'):
                                    st.markdown(f'**{season} - Overview**')
                                # Compare with every saved player in the selected year;
                                # rankings are cached per year and statistic.
                                from espn_fantasy import _normalized_name as _rank_normalize
                                _position_pairs = []
                                if not fantasy_frame.empty and 'ESPN Player' in fantasy_frame:
                                    _pos_frame = fantasy_frame.copy()
                                    _pos_frame['_name'] = _pos_frame['ESPN Player'].map(_rank_normalize)
                                    _pos_col = (_pos_frame['Default Position'].fillna('').astype(str).str.upper().str.strip()
                                                if 'Default Position' in _pos_frame else pd.Series('', index=_pos_frame.index))
                                    _fallback = (_pos_frame['Positions'].fillna('').astype(str).str.split(',').str[0].str.strip()
                                                 if 'Positions' in _pos_frame else pd.Series('', index=_pos_frame.index))
                                    _pos_frame['_primary'] = _pos_col.where(_pos_col.isin(('PG', 'SG', 'SF', 'PF', 'C')), _fallback)
                                    _pos_frame = _pos_frame.loc[_pos_frame['_primary'].isin(('PG', 'SG', 'SF', 'PF', 'C'))]
                                    _pos_frame = _pos_frame.drop_duplicates('_name', keep=False)
                                    _position_pairs = list(zip(_pos_frame['_name'], _pos_frame['_primary']))
                                _rank_maps = cached_quick_all_ranks(quick_seasons, selected_year, tuple(sorted(_position_pairs)), dataset_revision())
                                season_ranks = _rank_maps.get(rank_stat, {}).get(rank_scope, {})
                                rank_label = f'{rank_stat} Rank'
                                summary = summary.copy()
                                summary[rank_label] = season_ranks.get(choice[1], float('nan'))
                                summary_stats = [('GP', 'GP'), ('MIN', 'Min'), ('USG%', 'Usg'), (rank_label, rank_label),
                                                 ('Fpts', 'Fpts'), ('xFpts', 'xFpts'),
                                                 ('Fppg', 'Fppg'), ('xFppg', 'xFppg')]
                                available_stats = [(key, label) for key, label in summary_stats if key in summary.index]
                                for offset in range(0, len(available_stats), 4):
                                    metric_cols = st.columns(4)
                                    for metric_col, (key, label) in zip(metric_cols, available_stats[offset:offset + 4]):
                                        value = pd.to_numeric(summary.get(key), errors='coerce')
                                        if key == 'MIN':
                                            gp = pd.to_numeric(summary.get('GP'), errors='coerce')
                                            value = value / gp if pd.notna(gp) and gp > 0 else float('nan')
                                        elif key == 'USG%' and pd.notna(value):
                                            value *= 100
                                        if pd.isna(value):
                                            shown = '—'
                                        elif key == 'USG%':
                                            shown = f'{value:.1f}%'
                                        elif key in ('Fppg', 'xFppg'):
                                            shown = f'{value:,.1f}'
                                        elif key in ('GP', 'Fpts', 'xFpts', rank_label):
                                            shown = f'{value:,.0f}'
                                        else:
                                            shown = f'{value:,.1f}'
                                        metric_col.metric(label, shown)

                            st.markdown('**Season - Stats**')
                            pg_columns = [c for c in ['Season', 'Team', 'GP', 'MIN', 'USG%', 'Fpts', 'Fppg'] +
                                          [stat for pair in [('FG%', 'xFG%'), ('3PT%', 'x3P%'),
                                                            ('FT%', 'xFT%'), ('PTS', 'xPTS'),
                                                            ('REB', 'xREB'), ('AST', 'xAST'),
                                                            ('STL', 'xSTL'), ('BLK', 'xBLK'),
                                                            ('TOV', 'xTO')] for stat in pair]
                                          if c in quick_pg]
                            if pg_columns:
                                st.dataframe(quick_display(quick_pg, pg_columns), height=min(420, 38 * (len(player_rows) + 1) + 10), hide_index=True,
                                             use_container_width=True)
        render_player_quick_view()
        export_view = percent_view.drop(columns=['Headshot'], errors='ignore').copy()
        for c in percent_columns:
            export_view[c] = export_view[c].map(
                lambda v: '' if pd.isna(v) else f'{v:.2f}'.rstrip('0').rstrip('.') + '%')
        st.download_button('Export current view to CSV', export_view.to_csv(index=False).encode('utf-8-sig'),
                           file_name=f'nba_combined_{stat_mode.lower().replace(" ", "_")}.csv', mime='text/csv')


# Isolate fantasy filters from the expensive main table rerun. The source data
# and full player pool are cached; only the small display is rebuilt on changes.
from fantasy_analysis import fantasy_analysis_table

@st.cache_data(ttl=600, show_spinner=False)
def cached_fantasy_analysis(season_tuple, category_tuple, fantasy_year, refresh_stamp, dataset_stamp, fantasy_snapshot):
    source = cached_combined(season_tuple, category_tuple)
    return fantasy_analysis_table(source, fantasy_snapshot, fantasy_year)

@st.fragment
def fantasy_team_analysis():
    with st.expander('🏆 Fantasy Team Analysis', expanded=True):
        if fantasy_frame.empty:
            st.info('Refresh ESPN Fantasy data above to enable fantasy analysis.')
            return
        try:
            # Reuse the already cached combined stats, with all saved seasons.
            _status = cached_status()
            _cached = _status['Season'].dropna().astype(str).unique().tolist()
            _dataset_stamp = tuple(_status[['Season', 'Category', 'Updated']].astype(str).itertuples(index=False, name=None))
            _table = cached_fantasy_analysis(tuple(sorted(_cached)), tuple(CATEGORIES),
                                             fantasy_year, str(fantasy_updated), _dataset_stamp, fantasy_frame)
        except Exception as exc:
            st.warning(f'Fantasy analysis unavailable: {exc}')
            return
        if _table.empty:
            st.info('No matching players with saved NBA stats for this ESPN season.')
            return

        _teams = sorted(x for x in _table['Fantasy Team'].dropna().unique() if x != 'FA')
        _left, _middle, _right, _fourth = st.columns(4)
        with _left:
            _mode = st.segmented_control('Display', ['Per Game', 'Totals'],
                                         default='Per Game', key='fantasy_mode_v38')
            _mode = _mode or 'Per Game'
        with _middle:
            _selected_team = st.selectbox('Fantasy team',
                [focus_fantasy_team, 'Free Agents', 'All teams'] +
                [t for t in _teams if t != focus_fantasy_team], key='fantasy_team_filter_v38')
        with _right:
            _position = st.selectbox('Eligible position', ['All', 'PG', 'SG', 'SF', 'PF', 'C'],
                                     key='fantasy_position_filter_v38')
        with _fourth:
            _rank_stat = st.selectbox('Rank', ['Fpts', 'Fppg', 'xFpts', 'xFppg',
                'PTS', 'xPTS', 'AST', 'xAST', 'REB', 'xREB', 'STL', 'xSTL',
                'BLK', 'xBLK', 'TOV', 'xTO'], key='fantasy_rank_stat_v38', index=1)

        # Work on a copy: raw season totals remain intact for ranking and exports.
        _ranked = _table.copy()
        if _mode == 'Per Game':
            _gp = pd.to_numeric(_ranked['GP'], errors='coerce').replace(0, float('nan'))
            for _c in ('PTS', 'xPTS', 'AST', 'xAST', 'REB', 'xREB', 'STL', 'xSTL',
                       'BLK', 'xBLK', 'TOV', 'xTO'):
                if _c in _ranked:
                    _ranked[_c] = pd.to_numeric(_ranked[_c], errors='coerce') / _gp
        # Rank the full season-wide player pool, independent of position and
        # fantasy-team filters. Keep the Rank selector's metric/direction.
        _ranked['Rk'] = pd.Series(pd.NA, index=_ranked.index, dtype='Int64')
        if _rank_stat in _ranked:
            _rank_values = pd.to_numeric(_ranked[_rank_stat], errors='coerce')
            _ranked['Rk'] = _rank_values.rank(
                method='min', ascending=_rank_stat in ('TOV', 'xTO')
            ).astype('Int64')
        _view = _ranked
        if _selected_team == 'Free Agents':
            _view = _view.loc[_view['Fantasy Team'].eq('FA')]
        elif _selected_team != 'All teams':
            _view = _view.loc[_view['Fantasy Team'].eq(_selected_team)]
        if _position != 'All':
            _view = _view.loc[_view['Positions'].fillna('').apply(
                lambda value: _position in [p.strip() for p in str(value).split(',')])]
        # Sort by the selected ranking statistic, keeping the league-wide
        # ranks calculated before fantasy-team and position filters.
        # Stable sorting preserves the original order of tied players.
        if _rank_stat in _view.columns:
            _view = _view.sort_values(
                _rank_stat, ascending=_rank_stat in ('TOV', 'xTO'),
                na_position='last', kind='stable'
            )
        else:
            _view = _view.sort_values('Rk', ascending=True, na_position='last', kind='stable')
        _display = _view.copy()
        if 'Player' in _display and 'Injury' in _display:
            _injuries = _display['Injury'].fillna('').astype(str).str.strip()
            _injuries = _injuries.mask(_injuries.str.lower().isin(['nan', 'none', '<na>']), '')
            _display['Player'] = (_display['Player'].fillna('').astype(str)
                                  + _injuries.map(lambda value: f' ({value})' if value else ''))
        if 'Player ID' in _display:
            _display['Headshot'] = _display['Player ID'].map(player_headshot)
        # Mirror the main table's compact Player identity in Fantasy Team Viewer.
        # Keep Team and Positions in _display for filters and calculations; only
        # remove them from the columns presented in the table.
        if 'Player' in _display:
            _viewer_teams = (_display['Team'].fillna('').astype(str).str.strip()
                             if 'Team' in _display else pd.Series('', index=_display.index))
            _viewer_positions = (_display['Positions'].fillna('').astype(str).str.strip()
                                 if 'Positions' in _display else pd.Series('', index=_display.index))
            _viewer_identity = pd.Series([
                ' · '.join(part for part in (team, positions) if part)
                for team, positions in zip(_viewer_teams, _viewer_positions)
            ], index=_display.index)
            _display['Player'] = (_display['Player'].fillna('').astype(str)
                                  + _viewer_identity.map(lambda identity: f' · {identity}' if identity else ''))
        _display = _display.rename(columns={'Positions': 'Pos'})
        _show = [c for c in ['Rk', 'Headshot', 'Player', 'GP',
            'Fpts', 'xFpts', 'Fppg', 'xFppg', 'PTS', 'xPTS', 'AST', 'xAST',
            'REB', 'xREB', 'STL', 'xSTL', 'BLK', 'xBLK', 'TOV', 'xTO',
            'FG%', 'xFG%', 'FT%', 'xFT%', '3PT%', 'x3P%'] if c in _display]
        _percent_cols = [c for c in _show if '%' in c]
        for _c in _percent_cols:
            _display[_c] = pd.to_numeric(_display[_c], errors='coerce') * 100
        _number_cols = [c for c in _show if c not in
                        ('Rk', 'Headshot', 'Player', 'Team', 'Pos')]
        _formats = {c: stat_format(c, _mode, c in _percent_cols) for c in _number_cols}
        _formats['Rk'] = lambda value: '' if pd.isna(value) else str(int(value))
        _styled = _display[_show].style.format(_formats, na_rep='')
        if 'Player' in _show:
            # Fantasy Team Viewer uses regular names, with injury colors only.
            _styled = _styled.map(injury_name_style, subset=['Player'])
        _fantasy_column_config = stat_header_config(
            _show, selected=_rank_stat,
            descending=_rank_stat not in ('TOV', 'xTO'),
            overrides={**({'Headshot': st.column_config.ImageColumn('Headshot', width='small')}
                          if 'Headshot' in _show else {}),
                       'Player': st.column_config.TextColumn('Player', width=250)})
        # Match the main statistics table: selecting the row's checkbox opens
        # the corresponding player in Quick View, then clears the selection.
        _viewer_selection = st.dataframe(
            _styled, height=600, hide_index=True, use_container_width=True,
            row_height=35, column_config=_fantasy_column_config,
            key=f"fantasy_viewer_selection_v096_{st.session_state.get('fantasy_viewer_selection_epoch', 0)}",
            on_select='rerun', selection_mode='single-row',
        )
        _selected_rows = _viewer_selection.selection.rows
        if _selected_rows and 'Player ID' in _view.columns:
            _row_number = _selected_rows[0]
            if 0 <= _row_number < len(_view):
                _player_id = _view.iloc[_row_number]['Player ID']
                if pd.notna(_player_id):
                    st.session_state['regression_quick_view_request'] = str(_player_id)
                    st.session_state['fantasy_viewer_selection_epoch'] = (
                        st.session_state.get('fantasy_viewer_selection_epoch', 0) + 1
                    )
                    st.rerun(scope='app')

fantasy_team_analysis()


@st.cache_data(ttl=600, max_entries=4, show_spinner=False)
def cached_positive_regression_v094(seasons, categories, fantasy_year, refresh_stamp, dataset_stamp, fantasy_snapshot):
    from fantasy_analysis import positive_regression_table
    source = cached_combined(seasons, categories)
    return positive_regression_table(source, fantasy_snapshot, fantasy_year)

@st.fragment
def positive_regression_view():
    with st.expander('📈 Positive Regression Finder', expanded=False):
        st.caption('Expected-stat-first projections with historically calibrated role baselines. Next-season MPG and usage calibration passed held-out testing; rest-of-season Fppg remains unvalidated.')
        if not st.toggle('Calculate regression rankings', value=False, key='regression_run_v73', help='Loads historical data only when enabled, keeping normal app startup fast.'):
            st.info('Enable calculation to load historical player seasons and display both rankings.')
            return
        try:
            _status = cached_status()
            _seasons = tuple(sorted(_status['Season'].dropna().astype(str).unique().tolist()))
            _stamp = tuple(_status[['Season', 'Category', 'Updated']].astype(str).itertuples(index=False, name=None))
            data = cached_positive_regression_v094(_seasons, tuple(CATEGORIES), fantasy_year,
                                              str(fantasy_updated), _stamp, fantasy_frame)
        except Exception as exc:
            st.warning(f'Regression analysis unavailable: {exc}')
            return
        if data.empty:
            st.info('Download current and historical Standard data to enable regression analysis.')
            return
        # Keep controls above the rankings for both tabs.
        left, middle, right, fourth = st.columns(4)
        with left:
            teams = sorted(t for t in data['Fantasy Team'].dropna().astype(str).unique() if t != 'FA')
            owner = st.selectbox('Fantasy ownership', ['All players', 'Free agents', *teams], key='regression_owner')
        with middle:
            minimum_gp = st.number_input('Minimum GP', min_value=3, max_value=82, value=10, step=1, key='regression_min_gp')
        with right:
            confidence = st.selectbox('Minimum confidence', ['Any', 'Medium', 'High'], key='regression_confidence')
        with fourth:
            top_n = st.number_input('Top projected players', min_value=50, max_value=500,
                                    value=100, step=25, key='regression_top_n',
                                    help='Only show players ranked in the top N by projected Fppg across the current-season pool.')
        buy_tab, sell_tab = st.tabs(['📈 Buy Low', '📉 Sell High'])
        # Current rank uses the complete current-season pool, never the visible subset.
        # Ties use the same minimum-rank convention as projected rank.
        _current_ranks = pd.to_numeric(data['Fppg'], errors='coerce').rank(method='min', ascending=False)
        current_rank_lookup = pd.Series(_current_ranks.to_numpy(), index=data['Player ID'].astype('string')).to_dict()
        # Projected rank is computed across the full pool, before UI filters.
        shown = data.loc[data['GP'].ge(minimum_gp) & data['Projected Rank'].le(top_n)].copy()
        if owner == 'Free agents':
            shown = shown.loc[shown['Fantasy Team'].eq('FA')]
        elif owner != 'All players':
            shown = shown.loc[shown['Fantasy Team'].eq(owner)]
        if confidence == 'Medium':
            shown = shown.loc[shown['Confidence'].isin(['Medium', 'High'])]
        elif confidence == 'High':
            shown = shown.loc[shown['Confidence'].eq('High')]
        def render_regression_tab(tab_name, metric):
            # Use only the relevant direction; all ranks are calculated before filtering.
            required = {'Projected Fppg', 'Projected Rank', 'Fppg', 'xFppg', 'MPG', metric}
            missing = required.difference(data.columns)
            if missing:
                st.warning('Regression data is missing columns: ' + ', '.join(sorted(missing)) +
                           '. Update fantasy_analysis.py, clear Streamlit cache, and restart.')
                return
            results = shown.loc[pd.to_numeric(shown[metric], errors='coerce').gt(0)].copy()
            results = results.sort_values(metric, ascending=False)
            if results.empty:
                st.info('No players meet the selected criteria.')
                return
            # Lightweight, deterministic explanation based on the actual signals.
            # No AI calls, network traffic, or recomputation of projections.
            def explain(row):
                def val(col):
                    v = pd.to_numeric(row.get(col), errors='coerce')
                    return float(v) if pd.notna(v) else None
                actual, expected, historical = val('Fppg'), val('xFppg'), val('Historical Fppg')
                projected, minutes, projected_minutes = val('Projected Fppg'), val('MPG'), val('Projected MPG')
                usage_change = val('Usage Change')
                if actual is None or projected is None:
                    return 'Insufficient data to explain the projection.'
                diff = projected-actual
                direction = 'higher' if diff > 0 else 'lower'
                pieces = [f'Projected {abs(diff):.1f} Fppg {direction} than current production']
                if expected is not None:
                    gap = expected-actual
                    if gap > 1:
                        pieces.append(f'expected stats suggest +{gap:.1f} Fppg')
                    elif gap < -1:
                        pieces.append(f'expected stats suggest {gap:.1f} Fppg')
                    else:
                        pieces.append('expected stats are near actual production')
                if historical is not None:
                    hist_gap = historical-actual
                    if hist_gap > 2:
                        pieces.append('historical production supports a rebound')
                    elif hist_gap < -2:
                        pieces.append('historical production is below the current pace')
                    else:
                        pieces.append('historical production is similar')
                shooting = [(label, val(col)) for label, col in (
                    ('FG%', 'FG Expected Gap'), ('3P%', '3P Expected Gap'),
                    ('FT%', 'FT Expected Gap'))]
                shooting = [(label, gap) for label, gap in shooting if gap is not None]
                if shooting:
                    label, gap = max(shooting, key=lambda item: abs(item[1]))
                    if abs(gap) >= 2:
                        pieces.append(f'{label} expected vs actual {gap:+.1f} percentage points')
                if minutes is not None and projected_minutes is not None and abs(projected_minutes-minutes) >= 1.5:
                    pieces.append(f'minutes projected {projected_minutes-minutes:+.1f} MPG vs current')
                if usage_change is not None and abs(usage_change) >= 2:
                    pieces.append(f'usage {usage_change:+.1f} pts vs last season')
                return '. '.join([pieces[0], '; '.join(pieces[1:])]).rstrip('.') + '.'

            results['Why'] = results.apply(explain, axis=1)
            results['Player'] = (results['Player'].fillna('').astype(str) + ' · ' +
                                 results.get('Team', pd.Series('', index=results.index)).fillna('').astype(str))
            results['Headshot'] = results['Player ID'].map(player_headshot)
            # Rank all eligible current-season players by actual Fppg, not the filtered subset.
            # Display and sorting use native numeric types; formatting happens in column_config.
            results['Current Rank'] = pd.to_numeric(results['Player ID'].astype('string').map(current_rank_lookup), errors='coerce')
            columns = ['Headshot', 'Player', 'Fantasy Team', 'Current Rank', 'Projected Rank', 'GP',
                       'Fppg', 'xFppg', 'Historical Fppg', 'Projected Fppg',
                       'MPG', 'Projected MPG', 'USG%', 'Usage Change', 'Confidence',
                       metric, 'Why']
            display = results[[c for c in columns if c in results.columns]].copy()
            numeric_cols = {'Current Rank', 'Projected Rank', 'GP', 'Fppg', 'xFppg', 'Historical Fppg',
                            'Projected Fppg', 'Projected MPG', 'MPG', 'USG%',
                            'Usage Change', 'ROS Upside', 'Sell-High Gap'}
            for col in numeric_cols.intersection(display.columns):
                display[col] = pd.to_numeric(display[col], errors='coerce')
            # Usage is stored as a fraction (e.g. 0.24); show it as 24.0%.
            for usage_col in ('USG%', 'Usage Change'):
                if usage_col in display.columns:
                    display[usage_col] = display[usage_col] * 100
            descriptions = {
                'Current Rank': ('Rk', 'Current-season rank by actual fantasy points per game across all players.'),
                'Fantasy Team': ('F. Team', 'ESPN fantasy team; FA means free agent.'),
                'Projected Rank': ('Proj Rk', 'Rank by projected Fppg across the entire current-season player pool.'),
                'Historical Fppg': ('Hist Fppg', 'Historical fantasy production adjusted for age and playing time.'),
                'Projected Fppg': ('Proj Fppg', 'Rest-of-season fantasy points per game forecast.'),
                'Projected MPG': ('Proj MPG', 'Projected MPG: current-season role weighted most, with a games-played-weighted three-season prior.'),
                'MPG': ('MPG', 'Current-season minutes per game.'),
                'Usage Change': ('USG Δ', 'Current usage minus a games-played-weighted recent historical baseline, in percentage points.'),
                'ROS Upside': ('Buy Δ', 'Projected Fppg minus current Fppg. Positive values suggest buy-low upside.'),
                'Sell-High Gap': ('Sell Δ', 'Current Fppg minus projected Fppg. Positive values suggest sell-high downside.'),
                'Why': ('Why', 'Short explanation of the projection based on expected stats, history, minutes, and usage.'),
            }
            config = {
                'Headshot': st.column_config.ImageColumn('Headshot', width='small'),
                'Player': st.column_config.TextColumn('Player', width=250),
                'Why': st.column_config.TextColumn('Why', width='large', help=descriptions['Why'][1]),
            }
            for col in display.columns:
                if col in config:
                    continue
                label, tip = descriptions.get(col, (col, EXPECTED_STAT_HELP.get(col, col)))
                if col in numeric_cols:
                    config[col] = st.column_config.NumberColumn(
                        label, format='%d' if col in {'Current Rank', 'Projected Rank', 'GP'} else ('%.1f%%' if col in {'USG%', 'Usage Change'} else '%.2f'), help=tip)
                else:
                    config[col] = st.column_config.TextColumn(label, help=tip)
            selected = st.dataframe(
                display, hide_index=True, use_container_width=True, height=600, row_height=35,
                key=f"regression_{tab_name}_selection_v087_{st.session_state.get('regression_selection_epoch', 0)}",
                on_select='rerun', selection_mode='single-row', column_config=config)
            rows = selected.selection.rows
            if rows:
                idx = rows[0]
                if 0 <= idx < len(results):
                    target_id = str(results.iloc[idx]['Player ID'])
                    st.session_state['regression_quick_view_request'] = target_id
                    st.session_state['regression_selection_epoch'] = (
                        st.session_state.get('regression_selection_epoch', 0) + 1)
                    st.rerun(scope='app')
            st.download_button('Export ' + tab_name + ' rankings to CSV',
                               display.drop(columns=['Headshot'], errors='ignore').to_csv(index=False).encode('utf-8-sig'),
                               file_name=f'nba_{tab_name}_regression.csv', mime='text/csv',
                               key=f'regression_export_{tab_name}')

        with buy_tab:
            st.caption('Players projected to outperform their current Fppg. Useful for waiver pickups and buy-low trades.')
            render_regression_tab('buy_low', 'ROS Upside')
        with sell_tab:
            st.caption('Players currently outperforming their projected Fppg. Potential sell-high trade candidates.')
            render_regression_tab('sell_high', 'Sell-High Gap')
        st.markdown('**How the projections work**')
        st.caption(
            'Proj Fppg estimates future fantasy points per game, led by expected performance '
            '(xFppg and expected shooting: xFG%, x3P%, xFT%). Recent actual performance and '
            'historical seasons provide a limited, role-aware stabilizer, especially early in the season; expected stats carry more weight than history. '
            'Historical fantasy production is adjusted for age/experience and playing time; '
            'current MPG dominates Proj MPG; the prior three seasons are GP-weighted and the latest prior season receives a historically calibrated MPG/usage correction. Sustained role reductions weaken the historical prior. '
            'Buy Low ranks positive (Proj Fppg − Fppg) gaps; Sell High ranks negative gaps. '
            'Rk ranks current Fppg, whereas Proj Rk ranks projected Fppg, both across the full current-season player pool. '
            'Confidence reflects sample size and data availability, not the probability a projection is correct. '
            'Next-season MPG and usage corrections were evaluated on 809 held-out player-season pairs, but full rest-of-season Fppg forecasts have not been backtested.'
        )

positive_regression_view()

# Dynasty trade recommendations are calculated only when opened.
@st.fragment
def trade_calculator_view():
    with st.expander('🔄 Dynasty Trade Calculator', expanded=False):
        st.caption('Choose players to offer. Find up to 10 achievable trades across the league, ranked by your estimated dynasty gain.')
        if fantasy_frame.empty:
            st.info('Refresh ESPN Fantasy data to load team rosters.')
            return
        if not st.toggle('Find trade opportunities', value=False, key='trade_run_v098'):
            return
        try:
            status = cached_status()
            seasons = tuple(sorted(status['Season'].dropna().astype(str).unique().tolist()))
            stamp = tuple(status[['Season', 'Category', 'Updated']].astype(str).itertuples(index=False, name=None))
            projections = cached_positive_regression_v094(
                seasons, tuple(CATEGORIES), fantasy_year, str(fantasy_updated), stamp, fantasy_frame)
        except Exception as exc:
            st.warning(f'Trade projections unavailable: {exc}')
            return
        if projections.empty or not {'Player ID', 'Player', 'Fantasy Team', 'Fppg', 'Projected Fppg'}.issubset(projections.columns):
            st.info('Save current NBA stats and refresh ESPN rosters to calculate trades.')
            return
        mine = projections.loc[projections['Fantasy Team'].eq(focus_fantasy_team)].copy()
        mine['Fppg'] = pd.to_numeric(mine['Fppg'], errors='coerce')
        mine['Projected Fppg'] = pd.to_numeric(mine['Projected Fppg'], errors='coerce')
        mine = mine.dropna(subset=['Player ID', 'Fppg', 'Projected Fppg']).drop_duplicates('Player ID')
        if mine.empty:
            st.info('No projected players found on the focused fantasy team.')
            return
        choices = {str(r['Player ID']): f"{r['Player']} · {r['Fppg']:.1f} current / {r['Projected Fppg']:.1f} projected"
                   for _, r in mine.sort_values('Fppg', ascending=False).iterrows()}
        outgoing = st.multiselect('Players you would trade', options=list(choices),
                                  format_func=lambda pid: choices[pid], max_selections=2,
                                  key='trade_outgoing_v098')
        if not outgoing:
            st.info('Select one or two players to see possible offers from opposing teams.')
            return
        from trade_calculator import suggest_trades
        # Reuse the cached NBA dataset: historical rank reputation needs no API call.
        historical_stats = cached_combined(seasons, ('Standard',), stamp)
        suggestions = suggest_trades(
            projections, focus_fantasy_team, outgoing, fantasy=fantasy_frame,
            historical_stats=historical_stats, current_season=fantasy_year, max_results=10)
        if suggestions.empty:
            st.info('No trades passed the dynasty-value and perceived-fairness checks. Try different outgoing players.')
            return
        st.caption(f'{len(suggestions)} potential offers across the league · ranked by your estimated dynasty gain')
        player_lookup = projections.drop_duplicates('Player ID').set_index(projections.drop_duplicates('Player ID')['Player ID'].astype(str))
        def player_tile(pid):
            if pid not in player_lookup.index:
                st.write('Player unavailable')
                return
            row = player_lookup.loc[pid]
            pic, details = st.columns([1, 3], gap='small')
            with pic:
                url = player_headshot(pid)
                if url:
                    st.image(url, width=64)
            with details:
                st.markdown(f"**{row['Player']}**")
                current = pd.to_numeric(row.get('Fppg'), errors='coerce')
                future = pd.to_numeric(row.get('Projected Fppg'), errors='coerce')
                st.caption(f'{current:.1f} Fppg · {future:.1f} projected')
        for i, trade in suggestions.iterrows():
            with st.container(border=True):
                st.markdown(f"#### {trade['Fantasy Team']}")
                left, right = st.columns(2, gap='large')
                with left:
                    st.caption('YOU SEND')
                    for pid in trade['Give IDs']:
                        player_tile(pid)
                with right:
                    st.caption('YOU RECEIVE')
                    for pid in trade['Get IDs']:
                        player_tile(pid)
                metric_a, metric_b = st.columns(2)
                metric_a.metric('Your dynasty value gain', f"{trade['Your Dynasty Gain']:+.1f}")
                metric_b.metric('Their dynasty value gain', f"{trade['Their Dynasty Gain']:+.1f}")
                st.caption(f"Perceived value difference: {trade['Perceived Difference']:+.1f}"
                           + (f" · {trade['Roster Note']}" if trade['Roster Note'] else ''))
        st.caption('Trade values account for age, elite-player scarcity, draft reputation, decayed historical Fppg ranks, and the cost of an extra roster spot in uneven trades. ' 
                   'Starting-lineup positions are not evaluated. Available free agents provide a conservative replacement-value estimate when present. ' 
                   'IR eligibility, draft picks, and manager-specific preferences are not modeled. Offers are suggestions, not guaranteed accepted trades.')

trade_calculator_view()
