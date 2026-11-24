import time
import json
import pandas as pd
import streamlit as st
from nba_data import CATEGORIES, DB_PATH, SCORING, combined_table, fetch_category, save_dataset, status_table
from cloud_db import label as database_label, connect as scoring_db_connect
import re
import os
from espn_fantasy import fetch_fantasy, save_fantasy, load_fantasy, merge_fantasy, fetch_player_news, fetch_nba_injury_report
from datetime import datetime, time as clock_time, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
import streamlit.components.v1 as components
from nba_compact_table import render_nba_html

_nba_compact_click = components.declare_component('nba_compact_click', path=str(Path(__file__).resolve().parent / 'compact_click'))

st.set_page_config(page_title='NBA Fantasy Analytics', page_icon='🏀', layout='wide')
st.markdown('''<style>
/* Reduce the unused space above the page title without hiding Streamlit controls. */
.stAppViewContainer .block-container { padding-top: 1.5rem; }
</style>''', unsafe_allow_html=True)
st.title('🏀 NBA Fantasy Analytics')

# Custom scoring is presentation-only: never rewrite saved NBA datasets or expected-stat caches.
SCORING_LABELS = {
    'PTS': 'Points', 'FGM': 'Field Goals Made', 'FGA': 'Field Goals Attempted',
    'FTM': 'Free Throws Made', 'FTA': 'Free Throws Attempted',
    '3PM': 'Three-Pointers Made', 'REB': 'Rebounds', 'AST': 'Assists',
    'STL': 'Steals', 'BLK': 'Blocks', 'TOV': 'Turnovers',
}
SCORING_OPTIONS = dict(SCORING_LABELS)
SCORING_OPTIONS.update({'OREB': 'Offensive Rebounds', 'DREB': 'Defensive Rebounds',
                        '3PA': 'Three-Pointers Attempted', 'PF': 'Personal Fouls',
                        'DD2': 'Double-Doubles', 'TD3': 'Triple-Doubles'})
# Persist custom presets in the existing SQLite / PostgreSQL database.
# This is loaded once per Streamlit session, not on every widget interaction.

def _svg_from_html(chart_html):
    import re
    match = re.search(r"<svg\b[^>]*>.*?</svg>", chart_html, re.I | re.S)
    return match.group(0) if match else ""


def render_quick_chart_switch(wheel_html, radar_html, bar_html):
    """One responsive SVG for wheel/radar plus the shared Fppg bar."""
    wheel_svg = _svg_from_html(wheel_html)
    radar_svg = _svg_from_html(radar_html)
    bar_svg = _svg_from_html(bar_html)
    if not all((wheel_svg, radar_svg, bar_svg)):
        st.warning('The percentile visualization could not be rendered.')
        return

    # Scale only text containing numbers, keeping stat-name labels unchanged.
    def enlarge_numbers(svg):
        def change(match):
            opening, body, closing = match.groups()
            if not re.search(r'\d', re.sub(r'<[^>]*>', '', body)):
                return match.group(0)
            opening = re.sub(
                r'(font-size=["\'])([\d.]+)(["\'])',
                lambda m: m.group(1) + str(round(float(m.group(2)) * 1.1, 2)) + m.group(3),
                opening,
            )
            return opening + body + closing
        return re.sub(r'(<text\b[^>]*>)(.*?)(</text>)', change, svg, flags=re.S)

    wheel_svg = enlarge_numbers(wheel_svg)
    radar_svg = enlarge_numbers(radar_svg)
    bar_svg = enlarge_numbers(bar_svg)

    # Strip only the outer SVG tags; each source's geometry remains in its
    # original viewBox, so stat labels, legend, and gridlines scale together.
    def inner(svg):
        return re.sub(r'</svg>\s*$', '', re.sub(r'^<svg\b[^>]*>', '', svg, count=1), flags=re.I)

    # Prefix IDs in the bar to avoid conflicts with chart SVG definitions.
    bar_inner = inner(bar_svg).replace('id="expected-stripes"', 'id="fppg-expected-stripes"')
    bar_inner = bar_inner.replace('url(#expected-stripes)', 'url(#fppg-expected-stripes)')
    markup = """<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
html,body{margin:0;padding:0;min-height:0!important;height:auto!important;overflow:hidden;background:transparent;color:#f2f4f7;font-family:Arial,sans-serif}
.chart-heading{display:flex;justify-content:center;align-items:center;gap:10px;min-height:24px;margin:0 0 2px;font-size:16px;font-weight:700;white-space:nowrap}
.chart-switch{display:flex;align-items:center;gap:5px;font-size:9px;font-weight:400}
.chart-switch button{background:#181f2b;border:1px solid #485366;border-radius:5px;color:#cbd5e1;padding:4px 8px;cursor:pointer;font:inherit}
.chart-switch button[aria-pressed="true"]{background:#34435b;border-color:#8ca7cc;color:#fff;font-weight:700}
.visual{width:100%;max-width:605px;margin:0 auto}
.visual>svg{display:block;width:100%;height:auto;overflow:visible}
@media(max-width:640px){.chart-heading{margin-top:18px;margin-bottom:6px}.visual{margin-bottom:12px}}
[hidden]{display:none!important}
</style></head><body>
<div class="chart-heading"><span>Percentile Rankings</span>
<div class="chart-switch" role="group" aria-label="Percentile chart style">
<button id="wheel-btn" type="button" title="Wheel" aria-label="Wheel" aria-pressed="true">1</button>
<button id="radar-btn" type="button" title="Radar" aria-label="Radar" aria-pressed="false">2</button>
</div></div>
<div class="visual"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 485" role="img" aria-label="Player percentile wheel or radar and Fppg bar">
<svg id="wheel-chart" x="0" y="0" width="540" height="475" viewBox="0 0 500 450">__WHEEL__</svg>
<svg id="radar-chart" x="0" y="0" width="540" height="475" viewBox="0 0 500 450" display="none">__RADAR__</svg>
<svg x="510" y="0" width="140" height="450" viewBox="0 0 140 450">__BAR__</svg>
</svg></div>
<script>
const wheel=document.getElementById('wheel-chart'),radar=document.getElementById('radar-chart');
const wb=document.getElementById('wheel-btn'),rb=document.getElementById('radar-btn');
function show(mode){const w=mode==='wheel';wheel.setAttribute('display',w?'inline':'none');radar.setAttribute('display',w?'none':'inline');
wb.setAttribute('aria-pressed',String(w));rb.setAttribute('aria-pressed',String(!w));}
wb.addEventListener('click',()=>show('wheel'));rb.addEventListener('click',()=>show('radar'));
// Measure the rendered content, not the iframe viewport or its body height.
// On mobile the SVG scales with its width, so a fixed 485px iframe leaves a gap.
let lastHeight=0;
function resizeFrame(){
  const heading=document.querySelector('.chart-heading');
  const visual=document.querySelector('.visual');
  if(!heading||!visual)return;
  const bottom=Math.max(heading.getBoundingClientRect().bottom,
                        visual.getBoundingClientRect().bottom);
  const height=Math.ceil(bottom-document.documentElement.getBoundingClientRect().top)+3;
  if(Math.abs(height-lastHeight)>1){
    lastHeight=height;
    window.parent.postMessage({isStreamlitMessage:true,type:'streamlit:setFrameHeight',height},'*');
  }
}
window.addEventListener('resize',resizeFrame);
new ResizeObserver(resizeFrame).observe(document.querySelector('.visual'));
new ResizeObserver(resizeFrame).observe(document.querySelector('.chart-heading'));
requestAnimationFrame(()=>requestAnimationFrame(resizeFrame));
</script></body></html>"""
    markup = (markup.replace('__WHEEL__', inner(wheel_svg))
                    .replace('__RADAR__', inner(radar_svg))
                    .replace('__BAR__', bar_inner))
    components.html(markup, height=485, scrolling=False)


def load_scoring_presets():
    presets = {'My ESPN League': dict(SCORING)}
    try:
        with scoring_db_connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS nba_scoring_presets (name TEXT PRIMARY KEY, rules_json TEXT NOT NULL)')
            rows = conn.execute('SELECT name, rules_json FROM nba_scoring_presets').fetchall()
        for name, raw in rows:
            if name == 'My ESPN League':
                continue  # Always preserve the built-in ESPN scoring rules.
            data = json.loads(raw)
            if isinstance(data, dict) and all(isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool) for k, v in data.items()):
                presets[name] = {k: float(v) for k, v in data.items()}
    except Exception as exc:
        st.warning(f'Could not load saved scoring presets: {exc}')
    return presets


def save_scoring_preset(name, rules):
    with scoring_db_connect() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS nba_scoring_presets (name TEXT PRIMARY KEY, rules_json TEXT NOT NULL)')
        # DELETE + INSERT works consistently in both SQLite and PostgreSQL.
        conn.execute('DELETE FROM nba_scoring_presets WHERE name = ?', (name,))
        conn.execute('INSERT INTO nba_scoring_presets (name, rules_json) VALUES (?, ?)',
                     (name, json.dumps(rules, sort_keys=True)))
        conn.commit()


def delete_scoring_preset(name):
    with scoring_db_connect() as conn:
        conn.execute('DELETE FROM nba_scoring_presets WHERE name = ?', (name,))
        conn.commit()


if 'nba_scoring_presets' not in st.session_state:
    st.session_state.nba_scoring_presets = load_scoring_presets()
if 'nba_scoring_preset' not in st.session_state:
    st.session_state.nba_scoring_preset = 'My ESPN League'
if 'nba_scoring_rules' not in st.session_state:
    st.session_state.nba_scoring_rules = dict(SCORING)
if 'nba_scoring_draft' not in st.session_state:
    st.session_state.nba_scoring_draft = dict(st.session_state.nba_scoring_rules)


def active_scoring_signature():
    """Stable cache key: only an activated saved preset changes calculations."""
    return tuple(sorted((str(k), float(v)) for k, v in st.session_state.nba_scoring_rules.items()))



def scoring_changed():
    # Invalidate all calculated views, but leave the downloaded datasets and
    # persisted expected-stat cache untouched.
    for name in ('cached_combined', 'cached_main_fantasy_merge',
                 'cached_quick_view_pool', 'cached_quick_player_rows',
                 'cached_quick_all_ranks', 'cached_fantasy_analysis',
                 'cached_positive_regression_v094'):
        fn = globals().get(name)
        if fn is not None:
            fn.clear()


def apply_custom_scoring(frame):
    if frame.empty:
        return frame
    result = frame.copy()
    rules = st.session_state.get('nba_scoring_rules', SCORING)
    if 'GP' not in result:
        return result
    # Missing source columns contribute zero. The saved data is never modified.
    actual = pd.Series(0.0, index=result.index)
    for key, weight in rules.items():
        if key in result:
            actual = actual.add(pd.to_numeric(result[key], errors='coerce').fillna(0) * float(weight), fill_value=0)
    result['Fpts'] = actual.round(2)
    gp = pd.to_numeric(result['GP'], errors='coerce').replace(0, float('nan'))
    result['Fppg'] = (actual / gp).round(2)
    expected_map = {'PTS': 'xPTS', 'FGM': 'xFGM', 'FTM': 'xFTM',
                    '3PM': 'x3PM', 'REB': 'xREB', 'AST': 'xAST',
                    'STL': 'xSTL', 'BLK': 'xBLK', 'TOV': 'xTO'}
    if 'xFpts' in result:
        expected = pd.Series(0.0, index=result.index)
        available = pd.Series(True, index=result.index)
        for key, weight in rules.items():
            if not weight:
                continue
            source = expected_map.get(key, key)
            if source not in result:
                available &= False
                continue
            values = pd.to_numeric(result[source], errors='coerce')
            available &= values.notna()
            expected = expected.add(values.fillna(0) * float(weight), fill_value=0)
        # Respect original source availability / expected-data eligibility.
        original_expected = pd.to_numeric(result['xFpts'], errors='coerce')
        result['xFpts'] = expected.where(available & original_expected.notna()).round(2)
        result['xFppg'] = (result['xFpts'] / gp).round(2)
        result['+-Fppg'] = (result['xFppg'] - result['Fppg']).round(2)
    return result


def custom_scoring_panel():
    presets = st.session_state.nba_scoring_presets
    if st.session_state.get('nba_pending_preset'):
        st.session_state.nba_scoring_preset = st.session_state.pop('nba_pending_preset')
    selected = st.selectbox('Scoring preset', list(presets), key='nba_scoring_preset')
    if st.session_state.get('nba_last_scoring_preset') != selected:
        # Choosing an existing saved preset activates it. Editing never does.
        st.session_state.nba_scoring_rules = dict(presets[selected])
        st.session_state.nba_scoring_draft = dict(presets[selected])
        st.session_state.nba_scoring_editor_version = st.session_state.get('nba_scoring_editor_version', 0) + 1
        st.session_state.nba_last_scoring_preset = selected
        scoring_changed()
    draft = st.session_state.nba_scoring_draft
    version = st.session_state.get('nba_scoring_editor_version', 0)
    st.caption(f'Active scoring: **{selected}** · Edits are drafts until saved.')
    with st.expander('Manage presets'):
        preset_name = st.text_input('New preset name', key='nba_preset_name')
        a, b = st.columns(2)
        if a.button('Save current as preset', key='nba_save_preset'):
            name = preset_name.strip()
            if not name:
                st.warning('Enter a preset name first.')
            elif name == 'My ESPN League':
                st.warning('The built-in ESPN preset is protected. Choose another name.')
            else:
                try:
                    save_scoring_preset(name, dict(draft))
                except Exception as exc:
                    st.error(f'Could not save scoring preset: {exc}')
                else:
                    presets[name] = dict(draft)
                    st.session_state.nba_scoring_rules = dict(draft)
                    st.session_state.nba_pending_preset = name
                    scoring_changed()
                    st.rerun()
        if b.button('Discard edits', key='nba_discard_scoring'):
            st.session_state.nba_scoring_draft = dict(presets[selected])
            st.session_state.nba_scoring_editor_version = version + 1
            st.rerun()
        if selected != 'My ESPN League' and st.button('Delete selected preset', key='nba_delete_preset'):
            try:
                delete_scoring_preset(selected)
            except Exception as exc:
                st.error(f'Could not delete scoring preset: {exc}')
            else:
                del presets[selected]
                st.session_state.nba_pending_preset = 'My ESPN League'
                st.session_state.nba_last_scoring_preset = None
                st.rerun()
    st.caption(f'{len(draft)} draft rules · Select × to remove a rule')
    keys = list(draft)
    for offset in range(0, len(keys), 2):
        cols = st.columns(2)
        for col, key in zip(cols, keys[offset:offset + 2]):
            with col:
                left, value, remove = st.columns([5, 2, 1], vertical_alignment='center')
                left.markdown(f'**{SCORING_OPTIONS.get(key, key)}** `{key}`')
                updated = value.number_input(key, value=float(draft[key]), step=0.5,
                                             label_visibility='collapsed', key=f'nba_draft_{version}_{key}')
                if updated != draft[key]:
                    draft[key] = updated  # No cache invalidation, stats recalculation, or forced rerun.
                if remove.button('×', key=f'nba_remove_{version}_{key}', help=f'Remove {key}'):
                    del draft[key]
                    st.rerun()
    with st.expander('＋ Add or edit a scoring rule'):
        choices = [k for k in SCORING_OPTIONS if k not in draft]
        if choices:
            new_key = st.selectbox('Statistic', choices, format_func=lambda k: f'{SCORING_OPTIONS[k]} ({k})', key='nba_new_rule')
            new_weight = st.number_input('Points per statistic', value=1.0, step=0.5, key='nba_new_weight')
            if st.button('Add scoring rule', key='nba_add_rule'):
                draft[new_key] = new_weight
                st.rerun()
        else:
            st.caption('All available statistics already have scoring rules.')
    st.caption('Editing rules does not change player stats, regression projections, or trade values. '
               'Save as a named preset to activate your changes. Selecting a saved preset activates it immediately.')


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
def cached_combined(season_tuple, category_tuple, revision=None, scoring_signature=None):
    if revision is None:
        revision = dataset_revision()
    expected = cached_expected_statistics(revision)
    return apply_custom_scoring(combined_table(list(season_tuple), list(category_tuple), expected=expected))

@st.cache_data(ttl=600, max_entries=4, show_spinner=False)
def cached_main_fantasy_merge(season_tuple, category_tuple, fantasy_year, fantasy_updated, fantasy_snapshot, revision):
    """Avoid the expensive ESPN name-by-name merge on every Quick View click."""
    source = cached_combined(season_tuple, category_tuple, revision, active_scoring_signature())
    return merge_fantasy(source, fantasy_snapshot, fantasy_year)


@st.cache_data(ttl=1800, show_spinner=False)
def cached_player_news(espn_id):
    return fetch_player_news(espn_id)


@st.cache_data(ttl=3600, max_entries=1, show_spinner=False)
def cached_nba_injury_report():
    """Load ESPN's NBA injury feed once per hour, not once per player."""
    return fetch_nba_injury_report()


def season_end_year(values):
    """Return ending year for NBA season labels such as 2026-27."""
    labels = values.astype(str).str.strip()
    starts = pd.to_numeric(labels.str.extract(r'^(\d{4})[-–]\d{2,4}$', expand=False), errors='coerce')
    numeric = pd.to_numeric(labels, errors='coerce')
    return (starts + 1).fillna(numeric)


@st.cache_data(ttl=600, show_spinner=False)
def cached_quick_view_pool(seasons, revision):
    """Load the historical player pool once for Quick View interactions."""
    pool = cached_combined(seasons, tuple(CATEGORIES), revision, active_scoring_signature()).copy()
    if 'Player ID' in pool:
        pool['_quick_id'] = pool['Player ID'].astype(str)
    if 'Season' in pool:
        pool['_quick_year'] = season_end_year(pool['Season'])
    return pool



@st.cache_data(ttl=600, max_entries=5, show_spinner=False)
def cached_quick_season_percentiles(seasons, year, revision):
    """Calculate every player's actual/expected percentile once per season.

    Both actual and expected values are compared against the same qualified
    ACTUAL-stat distribution. Turnovers are inverted (lower is better).
    Returning maps keyed by Player ID makes switching players an O(1) lookup.
    """
    import math
    import numpy as np
    from nba_percentile_wheel import STATS, EXPECTED_KEYS
    if year is None:
        return {}, {}
    pool = cached_quick_view_pool(seasons, revision)
    if not {'_quick_year', '_quick_id', 'GP'}.issubset(pool.columns):
        return {}, {}
    peers = pool.loc[pool['_quick_year'].eq(int(year))].drop_duplicates('_quick_id')
    if peers.empty:
        return {}, {}
    ids = peers['_quick_id'].astype(str).to_numpy()
    gp = pd.to_numeric(peers['GP'], errors='coerce')
    minutes = pd.to_numeric(
        peers['MIN'] if 'MIN' in peers else pd.Series(float('nan'), index=peers.index),
        errors='coerce')
    max_gp = gp.max()
    if pd.isna(max_gp) or max_gp <= 0:
        return {}, {}
    minimum_games = max(1, math.ceil(0.20 * float(max_gp) - 1e-9))
    qualified = (gp.ge(minimum_games) & minutes.div(gp.where(gp.gt(0))).ge(15)).to_numpy()
    actual_result = {pid: {} for pid in ids}
    expected_result = {pid: {} for pid in ids}
    # Fppg is intentionally absent from the nine-wedge STATS list,
    # but its dedicated vertical bar still needs cached percentile values.
    for actual_key, label, per_game in STATS + (('Fppg', 'Fppg', False),):
        expected_key = EXPECTED_KEYS.get(actual_key)
        if actual_key not in peers or expected_key not in peers:
            continue
        actual = pd.to_numeric(peers[actual_key], errors='coerce')
        expected = pd.to_numeric(peers[expected_key], errors='coerce')
        if per_game:
            valid_gp = gp.where(gp.gt(0))
            actual = actual.div(valid_gp)
            expected = expected.div(valid_gp)
        sample = np.sort(actual.to_numpy(dtype=float)[qualified & actual.notna().to_numpy()])
        if sample.size < 5:
            continue
        for values, output in ((actual, actual_result), (expected, expected_result)):
            arr = values.to_numpy(dtype=float)
            valid = np.isfinite(arr)
            if not valid.any():
                continue
            left = np.searchsorted(sample, arr[valid], side='left')
            right = np.searchsorted(sample, arr[valid], side='right')
            pct = 100.0 * (left + 0.5 * (right - left)) / sample.size
            if actual_key == 'TOV':
                pct = 100.0 - pct
            for pid, value in zip(ids[valid], pct):
                output[pid][label] = round(float(np.clip(value, 0, 100)), 1)
    return actual_result, expected_result


@st.cache_data(ttl=600, show_spinner=False)
def cached_quick_player_rows(seasons, player_id, revision):
    """Cache one player's historical rows; avoid repeated full-table scans."""
    pool = cached_quick_view_pool(seasons, revision)
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
    pool = cached_quick_view_pool(seasons, revision)
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


@st.cache_data(ttl=600, max_entries=4, show_spinner=False)
def cached_quick_position_pairs(fantasy_snapshot):
    """Build ESPN position lookup once per fantasy roster update, not per player click."""
    if fantasy_snapshot.empty or 'ESPN Player' not in fantasy_snapshot:
        return ()
    from espn_fantasy import _normalized_name
    names = fantasy_snapshot['ESPN Player'].map(_normalized_name)
    primary = (fantasy_snapshot['Default Position'].fillna('').astype(str).str.upper().str.strip()
               if 'Default Position' in fantasy_snapshot else pd.Series('', index=fantasy_snapshot.index))
    fallback = (fantasy_snapshot['Positions'].fillna('').astype(str).str.split(',').str[0].str.strip()
                if 'Positions' in fantasy_snapshot else pd.Series('', index=fantasy_snapshot.index))
    positions = primary.where(primary.isin(('PG', 'SG', 'SF', 'PF', 'C')), fallback)
    lookup = pd.DataFrame({'name': names.to_numpy(), 'position': positions.to_numpy()})
    lookup = lookup.loc[lookup['position'].isin(('PG', 'SG', 'SF', 'PF', 'C'))]
    lookup = lookup.drop_duplicates('name', keep=False)
    return tuple(sorted(zip(lookup['name'], lookup['position'])))


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
    'xFpts': 'Expected fantasy points using the selected Custom Fantasy Scoring weights and available expected statistics.',
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
    return f'https://cdn.nba.com/logos/nba/{team_id}/global/L/logo.svg' if team_id else None

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
    ('FG%', 'xFG%'), ('3PT%', 'x3P%'), ('FT%', 'xFT%'),
    ('PTS', 'xPTS'), ('FGM', 'xFGM'), ('FTM', 'xFTM'),
    ('3PM', 'x3PM'), ('REB', 'xREB'), ('AST', 'xAST'),
    ('STL', 'xSTL'), ('BLK', 'xBLK'), ('TOV', 'xTO'),
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
    'FG%', 'xFG%', '3PT%', 'x3P%', 'FT%', 'xFT%',
    'PTS', 'xPTS', 'FGM', 'xFGM', 'FTM', 'xFTM', '3PM', 'x3PM',
    'REB', 'xREB', 'AST', 'xAST', 'STL', 'xSTL', 'BLK', 'xBLK',
    'TOV', 'xTO',
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
    if True:  # Visibility controlled by the dashboard toggle, not a nested expander.
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


top_left, top_right = st.columns(2)
with top_left:
  with st.expander('🏆 ESPN Fantasy League', expanded=False):
      st.caption('ESPN credentials are used only for the request. They are never stored in SQLite or CSV exports.')
      st.caption(f'League {fantasy_league} • Season {fantasy_year} (set ESPN_LEAGUE_ID in secrets or environment)')
      with st.form('espn_refresh'):
          # Keep private ESPN credentials out of the rendered page entirely.
          cookie = secret_or_env('ESPN_S2')
          swid = secret_or_env('ESPN_SWID')
          refresh_espn = st.form_submit_button('Refresh ESPN Fantasy Data')
      if refresh_espn:
          _espn_progress = st.progress(0, text='Connecting to ESPN…')
          _espn_status = st.empty()
          def _espn_progress_update(step, label):
              _espn_progress.progress(min(step / 7, 1.0), text=f'ESPN refresh: {label} ({step}/7)')
              _espn_status.caption(label)
          try:
                  fantasy_frame, info = fetch_fantasy(
                      fantasy_year, fantasy_league, cookie, swid,
                      progress_callback=_espn_progress_update)
                  _espn_progress_update(6, 'Saving ESPN snapshot')
                  save_fantasy(fantasy_year, fantasy_league, fantasy_frame)
                  cached_fantasy.clear()
                  cached_player_news.clear()
                  _espn_progress_update(7, 'ESPN refresh complete')
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
              _espn_progress.empty()
              _espn_status.empty()
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

with top_right:
    with st.expander('⚙️ Custom Fantasy Scoring', expanded=False):
        custom_scoring_panel()

bottom_left, bottom_right = st.columns(2)
with bottom_left:
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

with bottom_right:
    with st.expander('📋 Dashboard', expanded=False):
        show_completeness = st.checkbox('Load completeness dashboard', key='nba_show_completeness')
        if show_completeness:
            completeness_dashboard([f'{y}-{str(y + 1)[-2:]}' for y in range(2016, 2027)])

# Match the Fantasy Team selector width to the Download / Refresh Data panel.
fantasy_team_col, _fantasy_team_spacer = st.columns(2)
with fantasy_team_col:
    focus_fantasy_team = st.selectbox('🏆 Fantasy Team', team_options, key='focus_fantasy_team')

@st.fragment
def render_main_stats_section():
    st.subheader('🏀 NBA Statistics')
    if not selected_seasons or not selected_categories:
        st.info('Select at least one season and category in the sidebar.')
    else:
        try:
            df = cached_combined(tuple(selected_seasons), tuple(selected_categories), scoring_signature=active_scoring_signature())
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
            stat_mode = st.segmented_control('Stat display', ['Per Game', 'Totals'],
                default='Per Game', key='stat_display_mode_v39') or 'Per Game'
            _search_col, _f1, _f2, _f3, _f4, _f5 = st.columns([1.5, 1, 1, 1, 1, 1])
            with _search_col:
                search = st.text_input('Search player')
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
            # Retain injury codes for the combined Player cell before removing the
            # source column from the visible statistics. Keep row/index alignment
            # after the season, fantasy, position and search filters.
            _compact_injury_codes = (table_data['Injury'].fillna('').astype(str).str.strip()
                                     if 'Injury' in table_data.columns
                                     else pd.Series('', index=table_data.index))
            _compact_injury_codes = _compact_injury_codes.mask(
                _compact_injury_codes.str.lower().isin(('nan', 'none', '<na>')), '')
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
            # NBA fantasy season year (e.g. 2026-27 -> 2027), before AGE.
            if 'Season' in table_data:
                _season_labels = table_data['Season'].fillna('').astype(str)
                _season_year = _season_labels.str.extract(r'(\d{4})\s*[-/]\s*(\d{2,4})')
                _start_year = pd.to_numeric(_season_year[0], errors='coerce')
                _end_year = pd.to_numeric(_season_year[1], errors='coerce')
                _yr = _end_year.where(_end_year >= 1000, (_start_year // 100) * 100 + _end_year)
                _yr = _yr.where(_yr >= _start_year, _yr + 100)
                _yr = _yr.fillna(pd.to_numeric(_season_labels.str.extract(r'(\d{4})')[0], errors='coerce'))
                visible['YR'] = _yr.astype('Int64').to_numpy()
            _front = ['Rk', 'Headshot', 'Player', 'YR', 'AGE', 'Season']
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
            # MLB v223 architecture: custom HTML/CSS virtualized grid, JS sorting,
            # and a bidirectional compact_click component for Quick View selection.
            # All existing filters, ranks, calculations, and CSV exports stay in Python.
            compact_view = percent_view.drop(columns=['Headshot'], errors='ignore').copy()
            compact_view['Player ID'] = table_data['Player ID'].to_numpy() if 'Player ID' in table_data else None
            compact_view['Team ID'] = table_data['Team ID'].to_numpy() if 'Team ID' in table_data else None
            compact_view['Season'] = table_data['Season'].to_numpy() if 'Season' in table_data else ''
            compact_view['Team'] = table_data['Team'].fillna('').to_numpy() if 'Team' in table_data else ''
            compact_view['Positions'] = table_data['Positions'].fillna('').to_numpy() if 'Positions' in table_data else ''
            compact_view['Injury'] = _compact_injury_codes.to_numpy()
            compact_view['Fantasy'] = abbrev.to_numpy()
            compact_view['Headshot'] = (table_data['Player ID'].map(player_headshot).to_numpy()
                                        if 'Player ID' in table_data else '')
            compact_view['_focus'] = _focused_players.fillna(False).to_numpy()
            compact_view['_name'] = (table_data['Player'].astype(str).str.replace(
                r'\s+\((?:O|IR|DTD|D|Q|SUSP|OUT)\)$', '', regex=True).to_numpy())
            # The combined Player cell replaces separate Team/Pos/Fantasy columns.
            compact_view = compact_view.drop(columns=['Fantasy Team'], errors='ignore')
            freeze = st.session_state.get('nba_compact_freeze', False)
            html_doc = render_nba_html(
                compact_view, selected_focus=focus_fantasy_team, freeze_player=freeze,
                sort_stat='Rk', theme_bg=st.get_option('theme.backgroundColor') or '#0e1117',
                theme_fg=st.get_option('theme.textColor') or '#fafafa',
                theme_accent=st.get_option('theme.primaryColor') or '#ff4b4b',
                stat_mode=stat_mode, logo_url=nba_team_logo, show_player_id=True)
            click = _nba_compact_click(
                html=html_doc, height=min(585, 34 + 41 * len(compact_view)),
                key='nba_main_compact_click')
            selected_player_id = None
            if isinstance(click, dict) and click.get('player_id'):
                try:
                    token = (int(click['player_id']), click.get('nonce'))
                    if token != st.session_state.get('_nba_compact_last_click'):
                        st.session_state['_nba_compact_last_click'] = token
                        selected_player_id = str(token[0])
                        st.session_state['_pending_main_table_player_id'] = selected_player_id
                        st.session_state['_last_main_table_player_id'] = selected_player_id
                        selected_year = int(click.get('year') or 0)
                        if selected_year:
                            st.session_state[f'quick_view_year_{selected_player_id}'] = selected_year
                except (TypeError, ValueError):
                    pass
            # Compact footer: freeze toggle left, player-season metadata right.
            # Place the CSS on the footer itself so the rule applies to the preceding table.
            st.html('''<style>
            /* The compact table iframe is followed immediately by this footer. */
            div[data-testid="stElementContainer"]:has(.st-key-nba_main_compact_click) {
                margin-bottom: -0.9rem !important;
            }
            /* Pull the entire footer up toward the table without altering
               the margins inside the table's scrolling viewport. */
            .st-key-nba_table_footer {
                margin-top: -0.65rem !important;
            }
            .st-key-nba_table_footer div[data-testid="stHorizontalBlock"] {
                align-items: center;
            }
            </style>''')
            with st.container(key='nba_table_footer'):
                _freeze_left, _meta_right = st.columns([1.6, 5], vertical_alignment='center', gap='small')
                with _freeze_left:
                    st.toggle('Freeze player column', key='nba_compact_freeze',
                              help='Keep player names and headshots visible while scrolling horizontally.')
                with _meta_right:
                    _summary = (f'{len(df):,} player-season rows · {len(df.columns)} columns · '
                                f'{database_label()}')
                    if not fantasy_frame.empty:
                        _summary += (f' · ESPN {fantasy_matches:,} matched / '
                                     f'{fantasy_unmatched:,} unmatched')
                    st.markdown(f'<div style="text-align:right;color:#929ba6;font-size:0.875rem">{_summary}</div>', unsafe_allow_html=True)
            export_view = percent_view.drop(columns=['Headshot'], errors='ignore').copy()
            for c in percent_columns:
                export_view[c] = export_view[c].map(
                    lambda v: '' if pd.isna(v) else f'{v:.2f}'.rstrip('0').rstrip('.') + '%')
            st.download_button('Export current view to CSV', export_view.to_csv(index=False).encode('utf-8-sig'),
                               file_name=f'nba_combined_{stat_mode.lower().replace(" ", "_")}.csv', mime='text/csv')

            def render_player_quick_view():
                if 'Player' in df.columns and 'Player ID' in df.columns:
                    with st.expander('🏀 Player Quick View', expanded=True):
                        options = cached_player_options(df[['Player', 'Player ID']])
                        if options:
                            # Regression table can request a player from below this fragment.
                            # Consume once so ordinary Quick View navigation remains independent.
                            requested_id = st.session_state.pop('regression_quick_view_request', None)
                            table_request = st.session_state.pop('_pending_main_table_player_id', None)
                            # A request is consumed once; the search selectbox then
                            # owns the active player until another table click.
                            target_id = requested_id or table_request
                            current_index = next((i for i, option in enumerate(options)
                                                  if option[1] == target_id), None) if target_id else None
                            if current_index is not None:
                                st.session_state['quick_view_player'] = options[current_index]
                            elif st.session_state.get('quick_view_player') not in options:
                                # On a fresh page load, default to the player with the
                                # most total fantasy points in the latest loaded season.
                                # Preserve manual choices and table-click navigation.
                                default_option = options[0]
                                if {'Season', 'Player ID', 'Fpts'}.issubset(df.columns):
                                    season_years = pd.to_numeric(season_end_year(df['Season']), errors='coerce')
                                    if season_years.notna().any():
                                        latest_year = season_years.max()
                                        candidates = df.loc[season_years.eq(latest_year), ['Player ID', 'Fpts']].copy()
                                        candidates['Fpts'] = pd.to_numeric(candidates['Fpts'], errors='coerce')
                                        candidates = candidates.dropna(subset=['Fpts'])
                                        if not candidates.empty:
                                            top_player_id = str(candidates.loc[candidates['Fpts'].idxmax(), 'Player ID'])
                                            default_option = next((opt for opt in options if opt[1] == top_player_id), options[0])
                                st.session_state['quick_view_player'] = default_option
                            _player_select_col, _year_select_col, _filter_spacer = st.columns([1.5, 1, 6.6], vertical_alignment='bottom')
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
                            # Keep every saved season for the historical stats table; only
                            # the overview and identity card follow the Year dropdown.
                            overview_rows = (player_rows.loc[
                                season_end_year(player_rows['Season']).eq(selected_year)
                            ].copy() if selected_year is not None else player_rows.iloc[0:0].copy())
                            photo, _wheel_area, _overview_metrics, _right_margin = st.columns(
                                [1.55, 3.30, 1.35, 0.18], gap="small", vertical_alignment="top"
                            )
                            with photo:
                                # Align the bio title with Percentile Rankings and
                                # the season Overview heading in adjacent columns.
                                st.markdown(
                                    '<div style="width:100%;text-align:center;font-weight:800;'
                                    'font-size:1rem;line-height:1.5;margin:0 0 2px">Player Bio</div>',
                                    unsafe_allow_html=True,
                                )
                                url = player_headshot(choice[1])
                                if url:
                                    st.image(url, width=280)
                                else:
                                    st.caption('No headshot ID available')

                                # Compact identity block, matching the MLB Quick View layout.
                                # Reuse the already-loaded overview and ESPN lookup; no new API calls.
                                from html import escape as _html_escape
                                from espn_fantasy import _normalized_name, player_news_fallback_url
                                _player_name = str(choice[0])
                                _identity_row = overview_rows.iloc[0] if not overview_rows.empty else None
                                _nba_team = str(_identity_row.get('Team', '')).strip() if _identity_row is not None else ''
                                if _nba_team.lower() in ('nan', 'none'):
                                    _nba_team = ''
                                _logo = nba_team_logo(_nba_team) if _nba_team else None
                                _matches = (cached_espn_player_lookup(fantasy_frame).get(_normalized_name(_player_name), [])
                                            if not fantasy_frame.empty and 'ESPN Player' in fantasy_frame.columns else [])
                                _espn_row = _matches[0] if len(_matches) == 1 else {}
                                _espn_id = str(_espn_row.get('ESPN ID', '') or '')
                                _position = str(_identity_row.get('Positions', '')).strip() if _identity_row is not None else ''
                                if _position.lower() in ('nan', 'none') or not _position:
                                    _position = str(_espn_row.get('Positions', '') or '').strip()
                                if _position.lower() in ('nan', 'none'):
                                    _position = ''
                                _age = pd.to_numeric(_identity_row.get('AGE'), errors='coerce') if _identity_row is not None else float('nan')
                                _injury = str(_espn_row.get('Injury', '') or '').strip()
                                if _injury.lower() in ('nan', 'none', 'healthy', 'active'):
                                    _injury = ''
                                _fantasy_team = str(_espn_row.get('Fantasy Team', '') or '').strip()
                                if _fantasy_team.lower() in ('nan', 'none'):
                                    _fantasy_team = ''
                                _pos_team = f"<strong>{_html_escape(_position or '—')}</strong>"
                                if _nba_team:
                                    _pos_team += ' &nbsp;|&nbsp; '
                                    if _logo:
                                        _pos_team += (f'<img src="{_html_escape(_logo, quote=True)}" '
                                                      'style="width:24px;height:24px;vertical-align:middle;margin-right:4px">')
                                    _pos_team += f'<strong>{_html_escape(_nba_team)}</strong>'
                                _age_text = f'Age: {int(_age)}' if pd.notna(_age) else ''
                                _rnd, _pick = _espn_row.get('Draft Round'), _espn_row.get('Draft Pick')
                                if pd.notna(_rnd) and pd.notna(_pick):
                                    _draft_text = f'Round {int(_rnd)}, Pick {int(_pick)}'
                                    if pd.notna(_espn_row.get('Draft Overall')):
                                        _draft_text += f" ({int(_espn_row['Draft Overall'])})"
                                else:
                                    _draft_text = '—'
                                _team_text = _html_escape(_fantasy_team or '—')
                                _injury_detail, _return_date, _latest_update = '', '', ''
                                # Match ESPN's public injury report by BOTH player name and team.
                                # Never pass a fantasy player ID to the ESPN Sports injury endpoint.
                                if _injury and _nba_team:
                                    _injury_record = cached_nba_injury_report().get(
                                        (_normalized_name(_player_name), _nba_team.upper()), {})
                                    _injury_detail = _injury_record.get('description', '')
                                    _return_date = _injury_record.get('expected_return', '')
                                    _latest_update = _injury_record.get('latest_update', '')
                                _injury_text = _html_escape(
                                    f'{_injury} — {_injury_detail}' if _injury_detail and _injury else (_injury_detail or _injury or '—')
                                )
                                # Status-specific injury note color: serious/unavailable red,
                                # day-to-day/questionable yellow, healthy/unknown white.
                                _injury_upper = _injury.upper().replace('-', '').replace(' ', '')
                                if _injury_upper in ('O', 'OUT', 'IR', 'IL', 'SUSP', 'SUSPENDED', 'INJUREDRESERVE'):
                                    _injury_color = '#ff6575'
                                elif _injury_upper in ('DTD', 'DAYTODAY', 'Q', 'QUESTIONABLE', 'DOUBTFUL', 'PROBABLE', 'P'):
                                    _injury_color = '#f1c75b'
                                else:
                                    _injury_color = '#f2f4f7'
                                _return_text = _html_escape(_return_date or '—')
                                _update_html = (f'<div style="margin-top:3px;"><u>Latest injury update:</u> {_html_escape(_latest_update)}</div>'
                                                if _latest_update else '')
                                # Assemble the HTML without indented multiline Markdown: Streamlit's
                                # Markdown parser can render the closing </div> as literal text.
                                _identity_html = (
                                    '<div style="line-height:1.45;font-size:0.91rem;">'
                                    f'<div style="font-size:1.25rem;font-weight:700;margin-bottom:8px;">{_html_escape(_player_name)}</div>'
                                    f'<div style="margin-bottom:6px;">{_pos_team}</div>'
                                    f'<div style="color:#a7a9b2;font-size:0.83rem;margin-bottom:10px;">{_age_text}</div>'
                                    f'<div style="color:#a7a9b2;font-size:0.83rem;"><u>Draft:</u> {_html_escape(_draft_text)}</div>'
                                    f'<div style="color:#a7a9b2;font-size:0.83rem;"><u>Fantasy team:</u> {_team_text}</div>'
                                    f'<div style="margin-top:10px;color:{_injury_color};"><u>Injury:</u> {_injury_text}</div>'
                                    f'<div><u>Expected return:</u> {_return_text}</div>'
                                    f'{_update_html}'
                                    '</div>'
                                )
                                st.markdown(_identity_html, unsafe_allow_html=True)

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
                                        lambda v: '—' if pd.isna(v) else f'{v * 100:.1f}'.rstrip('0').rstrip('.') + '%')
                                elif col not in ('Season', 'Team', 'Positions'):
                                    view[col] = pd.to_numeric(view[col], errors='coerce').map(
                                        lambda v: '—' if pd.isna(v) else f'{v:,.1f}'.rstrip('0').rstrip('.'))
                            # Display-only substitution: leave the source frame
                            # and numeric data unchanged for calculations and charts.
                            return view.fillna('—').replace({'': '—'})

                        if not overview_rows.empty:
                            summary = overview_rows.iloc[0]
                            season = str(summary.get('Season', '')).strip()
                            # Rank controls and the Overview cards live in one small
                            # fragment. Changing rank selection reruns only this
                            # fragment, not the headshot, wheel, Fppg bar, or table.
                            from nba_percentile_wheel import render_percentile_wheel, player_percentiles

                            @st.fragment
                            def _quick_rank_overview(_summary, _season, _player_id,
                                                     _seasons, _year, _position_pairs,
                                                     _revision):
                                # Four compact rank buttons in a 2x2 grid.
                                # Buttons rerun only this fragment, not the full app.
                                st.session_state.setdefault('quick_view_rank_choice', 'Fppg')
                                st.session_state.setdefault('quick_view_scope_choice', 'All')
                                st.markdown('''<style>
.st-key-quick_rank_compact [data-testid="stButton"] button {
    min-height: 1.35rem !important; height: 1.35rem !important;
    padding: 0 .2rem !important; font-size: .65rem !important;
    border-radius: 5px !important;
}
.st-key-quick_rank_compact [data-testid="stVerticalBlock"] {
    gap: .48rem !important;
}
.st-key-quick_rank_compact [data-testid="stHorizontalBlock"] {
    gap: .5rem !important;
}
.st-key-quick_rank_compact .quick-rank-heading {
    font-size: .82rem; font-weight: 700; line-height: 1.25;
    display:block; min-height: 27px; padding: 0 0 9px; margin: 0; white-space:nowrap;
}
</style>''', unsafe_allow_html=True)
                                with st.container(key='quick_rank_compact'):
                                    _rank_buttons_col, _rank_divider_col, _scope_buttons_col = st.columns([1.4, 0.08, 1.1], gap='small')
                                    with _rank_divider_col:
                                        st.markdown('<div style="border-left:1px solid #4a5261;height:55px;margin:10px auto 0;width:1px"></div>', unsafe_allow_html=True)
                                    with _rank_buttons_col:
                                        st.markdown('<div class="quick-rank-heading">Rank by</div>', unsafe_allow_html=True)
                                        for _pair in (('Fppg', 'Fpts'), ('xFppg', 'xFpts')):
                                            _left, _right = st.columns(2, gap='small')
                                            for _col, _option in zip((_left, _right), _pair):
                                                with _col:
                                                    if st.button(
                                                        _option,
                                                        key=f'quick_rank_btn_{_option}',
                                                        use_container_width=True,
                                                        type=('primary' if st.session_state['quick_view_rank_choice'] == _option else 'secondary'),
                                                    ):
                                                        st.session_state['quick_view_rank_choice'] = _option
                                                        st.rerun(scope='fragment')
                                    with _scope_buttons_col:
                                        st.markdown('<div class="quick-rank-heading">Rank scope</div>', unsafe_allow_html=True)
                                        for _option in ('All', 'Pos'):
                                            if st.button(
                                                _option,
                                                key=f'quick_scope_btn_{_option}',
                                                use_container_width=True,
                                                type=('primary' if st.session_state['quick_view_scope_choice'] == _option else 'secondary'),
                                            ):
                                                st.session_state['quick_view_scope_choice'] = _option
                                                st.rerun(scope='fragment')
                                    _rank_stat = st.session_state['quick_view_rank_choice']
                                    _rank_scope = st.session_state['quick_view_scope_choice']
                                    _rank_maps = cached_quick_all_ranks(
                                        _seasons, _year, _position_pairs, _revision
                                    )
                                    _rank_label = f'{_rank_stat} Rank'
                                    _rank_value = _rank_maps.get(_rank_stat, {}).get(_rank_scope, {}).get(
                                        _player_id, float('nan')
                                    )
                                    _summary_stats = [
                                        ('GP', 'GP'), ('MIN', 'Min'), ('USG%', 'Usg'),
                                        (_rank_label, _rank_label), ('Fpts', 'Fpts'),
                                        ('xFpts', 'xFpts'), ('Fppg', 'Fppg'), ('xFppg', 'xFppg'),
                                    ]
                                    _metric_html = []
                                    from html import escape as _esc_metric
                                    for key, label in _summary_stats:
                                        if key != _rank_label and key not in _summary.index:
                                            continue
                                        value = (_rank_value if key == _rank_label else
                                                 pd.to_numeric(_summary.get(key), errors='coerce'))
                                        if key == 'MIN':
                                            gp = pd.to_numeric(_summary.get('GP'), errors='coerce')
                                            value = value / gp if pd.notna(gp) and gp > 0 else float('nan')
                                        elif key == 'USG%' and pd.notna(value):
                                            value *= 100
                                        if pd.isna(value):
                                            shown = '—'
                                        elif key == 'USG%':
                                            shown = f'{value:.1f}%'
                                        elif key in ('Fppg', 'xFppg'):
                                            shown = f'{value:,.1f}'
                                        elif key in ('GP', 'Fpts', 'xFpts', _rank_label):
                                            shown = f'{value:,.0f}'
                                        else:
                                            shown = f'{value:,.1f}'
                                        _metric_html.append(
                                            '<div style="padding:8px 7px;border:1px solid #303640;'
                                            'border-radius:7px;background:#11151c;min-width:0">'
                                            f'<div style="color:#c2cbd8;font-size:10px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{_esc_metric(label)}</div>'
                                            f'<div style="color:#f7f8fa;font-size:16px;font-weight:700;line-height:1.2">{_esc_metric(shown)}</div></div>'
                                        )
                                    st.markdown('<div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:5px">'
                                                + ''.join(_metric_html) + '</div>', unsafe_allow_html=True)

                            with _overview_metrics:
                                if season and season.lower() not in ('nan', 'none'):
                                    st.markdown(f'<div style="font-size:1rem;font-weight:800;line-height:1.5">{season} - Overview</div>', unsafe_allow_html=True)
                                _quick_rank_overview(
                                    summary, season, choice[1], quick_seasons, selected_year,
                                    cached_quick_position_pairs(fantasy_frame), dataset_revision()
                                )
                            with _wheel_area:
                                from nba_percentile_radar import render_percentile_radar
                                # Reuse precomputed season-wide distributions rather than
                                # scanning/ranking every peer on each player selection.
                                _actual_map, _expected_map = cached_quick_season_percentiles(
                                    quick_seasons, selected_year, dataset_revision()
                                )
                                _wheel_percentiles = _actual_map.get(str(choice[1]), {})
                                _expected_wheel_percentiles = _expected_map.get(str(choice[1]), {})
                                # Both SVGs are built once from cached data. The
                                # chart switch is client-side only: no Streamlit rerun,
                                # API call, database query, or recalculation on toggle.
                                _wheel_html = render_percentile_wheel(
                                    _wheel_percentiles, summary,
                                    expected_percentiles=_expected_wheel_percentiles,
                                    return_html=True,
                                )
                                _radar_html = render_percentile_radar(
                                    _wheel_percentiles, summary,
                                    expected_percentiles=_expected_wheel_percentiles,
                                    return_html=True,
                                )
                                from nba_fppg_bar import render_fppg_bar
                                _bar_html = render_fppg_bar(
                                    _wheel_percentiles, _expected_wheel_percentiles,
                                    summary, return_html=True,
                                )
                                render_quick_chart_switch(_wheel_html, _radar_html, _bar_html)

                            st.markdown('**Season - Stats**')
                            pg_columns = [c for c in ['Season', 'Team', 'AGE', 'GP', 'MIN', 'USG%', 'Fpts', 'Fppg'] +
                                          [stat for pair in [('FG%', 'xFG%'), ('3PT%', 'x3P%'),
                                                            ('FT%', 'xFT%'), ('PTS', 'xPTS'),
                                                            ('REB', 'xREB'), ('AST', 'xAST'),
                                                            ('STL', 'xSTL'), ('BLK', 'xBLK'),
                                                            ('TOV', 'xTO')] for stat in pair]
                                          if c in quick_pg]
                            if pg_columns:
                                st.dataframe(quick_display(quick_pg, pg_columns), height=min(420, 35 * (len(quick_pg) + 1) + 3),
                                             row_height=35, hide_index=True, use_container_width=True)

            render_player_quick_view()


render_main_stats_section()

# Isolate fantasy filters from the expensive main table rerun. The source data
# and full player pool are cached; only the small display is rebuilt on changes.
from fantasy_analysis import fantasy_analysis_table

@st.cache_data(ttl=600, show_spinner=False)
def cached_fantasy_analysis_shooting_v2(season_tuple, category_tuple, fantasy_year, refresh_stamp, dataset_stamp, fantasy_snapshot, scoring_signature):
    source = cached_combined(season_tuple, category_tuple, scoring_signature=scoring_signature)
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
            _table = cached_fantasy_analysis_shooting_v2(tuple(sorted(_cached)), tuple(CATEGORIES),
                                             fantasy_year, str(fantasy_updated), _dataset_stamp, fantasy_frame, active_scoring_signature())
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
            'Fpts', 'xFpts', 'Fppg', 'xFppg',
            'FG%', 'xFG%', '3PT%', 'x3P%', 'FT%', 'xFT%',
            'PTS', 'xPTS', 'AST', 'xAST',
            'REB', 'xREB', 'STL', 'xSTL', 'BLK', 'xBLK', 'TOV', 'xTO',
            ] if c in _display]
        _percent_cols = [c for c in _show if '%' in c]
        for _c in _percent_cols:
            _display[_c] = pd.to_numeric(_display[_c], errors='coerce') * 100
        # Reuse the same virtualized HTML/JS renderer as the main statistics table.
        # Preserve the viewer's existing _show headers, their order, ranks and
        # percentage formatting; only Headshot moves inside the Player cell.
        _compact = _display[[c for c in _show if c != 'Headshot']].copy()
        _compact['Player ID'] = _view['Player ID'].to_numpy() if 'Player ID' in _view else None
        _compact['Season'] = (_view['Season'].to_numpy() if 'Season' in _view
                              else str(fantasy_year))
        _compact['Team'] = (_view['Team'].fillna('').to_numpy() if 'Team' in _view
                            else '')
        _compact['Positions'] = (_view['Positions'].fillna('').to_numpy()
                                 if 'Positions' in _view else '')
        _compact['Injury'] = (_view['Injury'].fillna('').to_numpy()
                              if 'Injury' in _view else '')
        _compact['Fantasy'] = (_view['Fantasy Team'].fillna('').to_numpy()
                               if 'Fantasy Team' in _view else '')
        _compact['Headshot'] = (_view['Player ID'].map(player_headshot).to_numpy()
                                if 'Player ID' in _view else '')
        _compact['_name'] = _view['Player'].fillna('').astype(str).to_numpy()
        # Every player is already in the selected fantasy team context;
        # do not highlight the Player cell or display redundant fantasy badges.
        _compact['_focus'] = False
        _compact['Fantasy'] = ''
        _viewer_html = render_nba_html(
            _compact, selected_focus=focus_fantasy_team, freeze_player=False,
            sort_stat=_rank_stat if _rank_stat in _compact else 'Rk',
            theme_bg=st.get_option('theme.backgroundColor') or '#0e1117',
            theme_fg=st.get_option('theme.textColor') or '#fafafa',
            theme_accent=st.get_option('theme.primaryColor') or '#ff4b4b',
            stat_mode=_mode, logo_url=nba_team_logo, compact_player=True)
        _viewer_click = _nba_compact_click(
            html=_viewer_html, height=min(585, 34 + 41 * len(_compact)),
            key='nba_fantasy_viewer_compact_click')
        if isinstance(_viewer_click, dict) and _viewer_click.get('player_id'):
            try:
                _pid = str(int(_viewer_click['player_id']))
                _token = (_pid, _viewer_click.get('nonce'))
                if _token != st.session_state.get('_nba_fantasy_viewer_last_click'):
                    st.session_state['_nba_fantasy_viewer_last_click'] = _token
                    st.session_state['regression_quick_view_request'] = _pid
                    _yr = int(_viewer_click.get('year') or 0)
                    if _yr:
                        st.session_state[f'quick_view_year_{_pid}'] = _yr
                    # The existing Quick View is in the earlier main fragment.
                    # A full rerun is needed for this cross-fragment selection only;
                    # viewer filter changes stay inside this fragment.
                    st.rerun(scope='app')
            except (TypeError, ValueError):
                pass

fantasy_team_analysis()


@st.cache_data(ttl=600, max_entries=4, show_spinner=False)
def cached_positive_regression_v094(seasons, categories, fantasy_year, refresh_stamp, dataset_stamp, fantasy_snapshot, scoring_signature):
    from fantasy_analysis import positive_regression_table
    source = cached_combined(seasons, categories, scoring_signature=scoring_signature)
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
                                              str(fantasy_updated), _stamp, fantasy_frame, active_scoring_signature())
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
            # Reuse the main table's browser-side sorter and player-click bridge.
            required = {'Projected Fppg', 'Projected Rank', 'Fppg', 'xFppg', 'MPG', metric}
            missing = required.difference(data.columns)
            if missing:
                st.warning('Regression data is missing: ' + ', '.join(sorted(missing)))
                return
            results = shown.loc[pd.to_numeric(shown[metric], errors='coerce').gt(0)].copy()
            results = results.sort_values(metric, ascending=False)
            if results.empty:
                st.info('No players meet the selected criteria.')
                return
            results['Current Rank'] = pd.to_numeric(
                results['Player ID'].astype('string').map(current_rank_lookup), errors='coerce')
            columns = ['Player', 'Current Rank', 'Projected Rank', 'GP',
                       'Fppg', 'xFppg', 'Historical Fppg', 'Projected Fppg',
                       'MPG', 'Projected MPG', 'USG%', 'Usage Change', 'Confidence', metric]
            display = results[[c for c in columns if c in results.columns]].copy()
            display.rename(columns={
                'Current Rank': 'Rk',
                'Projected Rank': 'Proj. Rk',
                'Historical Fppg': 'hFppg',
                'Projected Fppg': 'pFppg',
                'Projected MPG': 'Proj. MPG',
                'Usage Change': 'ΔUSG',
            }, inplace=True)
            for col in ('Rk', 'Proj. Rk', 'GP', 'Fppg', 'xFppg',
                        'hFppg', 'pFppg', 'Proj. MPG',
                        'MPG', 'USG%', 'ΔUSG', metric):
                if col in display:
                    display[col] = pd.to_numeric(display[col], errors='coerce')
            for col in ('USG%', 'ΔUSG'):
                if col in display:
                    display[col] = display[col] * 100
            display['Player ID'] = results['Player ID'].to_numpy()
            display['Season'] = '2026-27' if str(fantasy_year) == '2027' else str(fantasy_year)
            display['Team'] = (results['Team'].fillna('').astype(str).to_numpy()
                               if 'Team' in results else '')
            display['Positions'] = (results['Positions'].fillna('').astype(str).to_numpy()
                                    if 'Positions' in results else '')
            display['Headshot'] = results['Player ID'].map(player_headshot).to_numpy()
            display['Injury'] = ''
            # Reuse ESPN's saved fantasy-team abbreviation. Do not infer one
            # from a team's name; leave unmatched/unrostered players unbadged.
            if 'Fantasy Abbrev' in results.columns:
                _badges = results['Fantasy Abbrev']
            elif {'Fantasy Team', 'Fantasy Abbrev'}.issubset(fantasy_frame.columns):
                _owners = fantasy_frame[['Fantasy Team', 'Fantasy Abbrev']].dropna(
                    subset=['Fantasy Team', 'Fantasy Abbrev']).copy()
                _owners['Fantasy Team'] = _owners['Fantasy Team'].astype(str).str.strip()
                _owners['Fantasy Abbrev'] = _owners['Fantasy Abbrev'].astype(str).str.strip()
                _owners = _owners.loc[_owners['Fantasy Abbrev'].ne('')].drop_duplicates('Fantasy Team')
                _badges = results['Fantasy Team'].map(
                    _owners.set_index('Fantasy Team')['Fantasy Abbrev'])
            else:
                _badges = pd.Series('', index=results.index)
            display['Fantasy'] = _badges.fillna('').astype(str).str.strip().str.upper().to_numpy()
            display['_focus'] = False
            display['_name'] = results['Player'].fillna('').astype(str).to_numpy()
            html_doc = render_nba_html(
                display, selected_focus=None, freeze_player=False, sort_stat=metric,
                theme_bg=st.get_option('theme.backgroundColor') or '#0e1117',
                theme_fg=st.get_option('theme.textColor') or '#fafafa',
                theme_accent=st.get_option('theme.primaryColor') or '#ff4b4b',
                stat_mode='Per Game', logo_url=nba_team_logo, compact_player=True, show_rank=False)
            clicked = _nba_compact_click(
                html=html_doc, height=min(585, 34 + 41 * len(display)),
                key=f'nba_regression_{tab_name}_compact_click')
            if isinstance(clicked, dict) and clicked.get('player_id'):
                try:
                    pid = str(int(clicked['player_id']))
                    token = (pid, clicked.get('nonce'))
                    state_key = f'_nba_regression_last_click_{tab_name}'
                    if token != st.session_state.get(state_key):
                        st.session_state[state_key] = token
                        st.session_state['regression_quick_view_request'] = pid
                        st.rerun(scope='app')
                except (TypeError, ValueError):
                    pass
            csv_columns = [c for c in display.columns if c not in (
                'Player ID', 'Season', 'Team', 'Positions', 'Headshot', 'Injury',
                'Fantasy', '_focus', '_name')]
            st.download_button('Export ' + tab_name + ' rankings to CSV',
                               display[csv_columns].to_csv(index=False).encode('utf-8-sig'),
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
                seasons, tuple(CATEGORIES), fantasy_year, str(fantasy_updated), stamp, fantasy_frame, active_scoring_signature())
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
        historical_stats = cached_combined(seasons, ('Standard',), stamp, active_scoring_signature())
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
