"""Compact NBA player percentile wheel for the Quick View overview."""
from html import escape
import math
import pandas as pd
import streamlit.components.v1 as components

STATS = (('PTS', 'PTS', True), ('AST', 'AST', True), ('REB', 'REB', True),
         ('STL', 'STL', True), ('BLK', 'BLK', True), ('TOV', 'TO', True),
         ('3PT%', '3PT%', False), ('FT%', 'FT%', False),
         ('FG%', 'FG%', False), ('Fppg', 'Fppg', False))

EXPECTED_KEYS = {'PTS': 'xPTS', 'AST': 'xAST', 'REB': 'xREB', 'STL': 'xSTL',
                 'BLK': 'xBLK', 'TOV': 'xTO', '3PT%': 'x3P%', 'FT%': 'xFT%',
                 'FG%': 'xFG%', 'Fppg': 'xFppg'}


def player_percentiles(pool, year, player_id, expected=False):
    """Percentiles against the same actual-stat peer distribution for both values.

    Counting stats are compared per game, while shooting percentages remain rates.
    Qualify players with at least 20% of games elapsed and 15 MPG.
    Games elapsed is approximated by the largest GP in the season dataset,
    avoiding a new schedule/API dependency. No new API calls.
    """
    if year is None or pool.empty or not {'_quick_year', '_quick_id', 'GP'}.issubset(pool.columns):
        return {}
    peers = pool.loc[pool['_quick_year'].eq(int(year))].drop_duplicates('_quick_id').copy()
    if peers.empty:
        return {}
    ids = peers['_quick_id'].astype(str)
    selected = ids.eq(str(player_id))
    if not selected.any():
        return {}
    gp = pd.to_numeric(peers['GP'], errors='coerce')
    mins = pd.to_numeric(peers.get('MIN', pd.Series(float('nan'), index=peers.index)), errors='coerce')
    # Use the most games played by any player as the season-progress proxy.
    # This scales the sample requirement throughout the season (e.g., 15 -> 3,
    # 40 -> 8, 82 -> 17) and does not exclude the selected player from viewing.
    games_elapsed = gp.max()
    if pd.isna(games_elapsed) or games_elapsed <= 0:
        return {}
    min_games = max(1, math.ceil(0.20 * float(games_elapsed) - 1e-9))
    qualified = gp.ge(min_games) & mins.div(gp.where(gp.gt(0))).ge(15)
    result = {}
    for actual_key, label, per_game in STATS:
        # Both actual and expected values MUST use the same ACTUAL-stat
        # peer distribution. Comparing x3P% to other players' x3P%
        # would produce an incompatible percentile scale.
        comparison_key = actual_key
        player_key = EXPECTED_KEYS[actual_key] if expected else actual_key
        if comparison_key not in peers.columns or player_key not in peers.columns:
            continue
        peer_values = pd.to_numeric(peers[comparison_key], errors='coerce')
        player_values = pd.to_numeric(peers[player_key], errors='coerce')
        if per_game:
            valid_gp = gp.where(gp.gt(0))
            peer_values = peer_values.div(valid_gp)
            player_values = player_values.div(valid_gp)
        sample = peer_values.loc[qualified & peer_values.notna()]
        player_value = player_values.loc[selected].iloc[0]
        if pd.isna(player_value) or len(sample) < 5:
            continue
        # Midrank percentile on a shared distribution, with ties handled fairly.
        pct = 100 * (sample.lt(player_value).sum() + 0.5 * sample.eq(player_value).sum()) / len(sample)
        # Every stat rewards higher values except turnovers, which rewards lower.
        # This inversion applies to both actual and expected turnover percentiles.
        if actual_key == 'TOV':
            pct = 100 - pct
        result[label] = round(max(0., min(100., float(pct))), 1)
    return result


def _color(p):
    """Muted blue -> soft rose -> red, based on percentile."""
    stops = [(0, (35, 92, 215)), (25, (58, 117, 224)), (50, (142, 119, 184)), (75, (211, 89, 122)), (100, (239, 54, 70))]
    a, b = next((lo, hi) for lo, hi in zip(stops, stops[1:]) if p <= hi[0])
    t = (p - a[0]) / (b[0] - a[0])
    rgb = tuple(round(a[1][i] + t * (b[1][i] - a[1][i])) for i in range(3))
    return '#%02x%02x%02x' % rgb


def _ordinal_suffix(value):
    """Return the correct English suffix for a rounded percentile."""
    n = int(round(value))
    return 'th' if n % 100 in (11, 12, 13) else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')


def _xy(cx, cy, radius, angle):
    angle = math.radians(angle - 90)
    return (cx + radius * math.cos(angle), cy + radius * math.sin(angle))


def _wedge(cx, cy, r0, r1, start, end):
    points = [_xy(cx,cy,r0,start), _xy(cx,cy,r1,start), _xy(cx,cy,r1,end), _xy(cx,cy,r0,end)]
    a,b,c,d = points
    return (f'M {a[0]:.2f} {a[1]:.2f} L {b[0]:.2f} {b[1]:.2f} '
            f'A {r1:.2f} {r1:.2f} 0 0 1 {c[0]:.2f} {c[1]:.2f} '
            f'L {d[0]:.2f} {d[1]:.2f} A {r0:.2f} {r0:.2f} 0 0 0 {a[0]:.2f} {a[1]:.2f} Z')


def render_percentile_wheel(percentiles, player_row=None, expected_percentiles=None):
    """Render actual wedges and optional same-color translucent expected wedges."""
    labels = [label for _,label,_ in STATS]
    values = {}
    if player_row is not None:
        gp = pd.to_numeric(player_row.get('GP'), errors='coerce')
        for key, label, per_game in STATS:
            val = pd.to_numeric(player_row.get(key), errors='coerce')
            if per_game:
                val = val / gp if pd.notna(gp) and gp > 0 else float('nan')
            if pd.notna(val):
                values[label] = (f'{val:.1f}' if per_game or key == 'Fppg' else f'{val * 100:.1f}%')

    expected_values = {}
    if player_row is not None:
        gp = pd.to_numeric(player_row.get('GP'), errors='coerce')
        for key, label, per_game in STATS:
            xkey = EXPECTED_KEYS.get(key)
            if not xkey:
                continue
            val = pd.to_numeric(player_row.get(xkey), errors='coerce')
            if per_game:
                val = val / gp if pd.notna(gp) and gp > 0 else float('nan')
            if pd.notna(val):
                expected_values[label] = (f'x{val:.1f}' if per_game or key == 'Fppg' else f'x{val * 100:.1f}%')

    # Ten closely packed wedges. Percentiles live inside; actual and expected
    # numbers are outside the wheel with their category labels.
    cx, cy, inner, outer = 240, 215, 61, 151
    n = len(labels)
    sector = 360 / n
    parts = [f'<circle cx="{cx}" cy="{cy}" r="{outer}" fill="none" stroke="#454c58" stroke-width="1.2"/>']
    for tick in (25, 50, 75):
        radius = inner + (outer-inner)*tick/100
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="{radius:.1f}" fill="none" stroke="#77808c" stroke-opacity=".28" stroke-width=".8"/>')
    for i, label in enumerate(labels):
        start, end = i*sector+0.65, (i+1)*sector-0.65
        angle = (start+end)/2
        pct = percentiles.get(label)
        parts.append(f'<path d="{_wedge(cx,cy,inner,outer,start,end)}" fill="#232a34"/>')
        if pct is not None and pct > 0:
            r = inner+(outer-inner)*pct/100
            parts.append(f'<path d="{_wedge(cx,cy,inner,r,start,end)}" fill="{_color(pct)}"><title>{escape(label)}: {pct:.1f}th percentile</title></path>')
        expected_pct = (expected_percentiles or {}).get(label)
        if pct is not None and expected_pct is not None:
            expected_pct = max(0., min(100., float(expected_pct)))
            expected_radius = inner+(outer-inner)*expected_pct/100
            overlay_color = _color(pct)
            pattern_id = f'expected-hatch-{i}'
            parts.append(f'<defs><pattern id="{pattern_id}" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><path d="M 0 0 V 7" stroke="#ffffff" stroke-opacity=".18" stroke-width="1"/></pattern></defs>')
            if expected_pct > 0:
                overlay_path = _wedge(cx,cy,inner,expected_radius,start,end)
                parts.append(f'<path d="{overlay_path}" fill="{overlay_color}" fill-opacity=".24"/>')
                parts.append(f'<path d="{overlay_path}" fill="url(#{pattern_id})"/>')
            a = _xy(cx,cy,expected_radius,start)
            b = _xy(cx,cy,expected_radius,end)
            arc = f'M {a[0]:.2f} {a[1]:.2f} A {expected_radius:.2f} {expected_radius:.2f} 0 0 1 {b[0]:.2f} {b[1]:.2f}'
            parts.append(f'<path d="{arc}" fill="none" stroke="#0e1219" stroke-opacity=".8" stroke-width="3"/>')
            parts.append(f'<path d="{arc}" fill="none" stroke="{overlay_color}" stroke-width="2.3" stroke-dasharray="4 2.5"/>')
            parts.append(f'<path d="{arc}" fill="none" stroke="#fff" stroke-opacity=".6" stroke-width=".7" stroke-dasharray="4 2.5"/>')

        # Percentile inside its own sector, including low-percentile wedges.
        if pct is not None:
            available = (outer-inner)*max(0.,pct)/100
            text_r = inner + min(max(available*.60, 24), 61)
            tx,ty = _xy(cx,cy,text_r,angle)
            if available < 30:
                parts.append(f'<circle cx="{tx:.1f}" cy="{ty:.1f}" r="13" fill="#121722" fill-opacity=".85"/>')
            parts.append(f'<text x="{tx:.1f}" y="{ty+4:.1f}" text-anchor="middle" fill="#fff" font-size="12.5" font-weight="700" paint-order="stroke" stroke="#11151c" stroke-width="2.5">{round(pct)}<tspan font-size="8.5" font-weight="400" baseline-shift="super">{_ordinal_suffix(pct)}</tspan></text>')

        # Category + actual + expected labels outside the wheel.
        # Alternating horizontal label alignment prevents crowding at the top/bottom.
        x,y = _xy(cx,cy,outer+19,angle)
        anchor = 'middle'
        if 40 < angle < 140:
            anchor = 'start'
            x += 1
        elif 220 < angle < 320:
            anchor = 'end'
            x -= 1
        # Bottom sectors (BLK and TO) need their labels below the outer arc,
        # rather than centered vertically across it.
        if 140 <= angle <= 220:
            y += 17
        parts.append(f'<text x="{x:.1f}" y="{y-13:.1f}" text-anchor="{anchor}" fill="#e8ebf1" font-size="13" font-weight="700">{escape(label)}</text>')
        if label in values:
            parts.append(f'<text x="{x:.1f}" y="{y+2:.1f}" text-anchor="{anchor}" fill="#fff" font-size="12" font-weight="700">{escape(values[label])}</text>')
        if label in expected_values:
            parts.append(f'<text x="{x:.1f}" y="{y+16:.1f}" text-anchor="{anchor}" fill="#b9c4d4" font-size="11" font-style="italic" font-weight="400">{escape(expected_values[label])}</text>')
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{inner-1}" fill="#0e1219" stroke="#404853" stroke-width="1.2"/>')
    html = ("""<html><head><meta name="viewport" content="width=device-width,initial-scale=1"/>
    <style>html,body{margin:0;background:transparent;color:#f2f4f7;font-family:Arial,sans-serif}
    .wrap{width:100%;max-width:540px;margin:0 auto}
    svg{width:100%;height:auto;display:block}</style></head><body><div class="wrap">
    <svg viewBox="0 0 500 450" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="NBA player percentile wheel">"""
    + ''.join(parts) + '</svg></div></body></html>')
    components.html(html, height=465, scrolling=False)
