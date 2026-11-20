"""Alternative NBA percentile radar, displayed below the Quick View season table."""
from html import escape
import math
import pandas as pd
import streamlit.components.v1 as components

from nba_percentile_wheel import STATS, EXPECTED_KEYS


def _xy(cx, cy, radius, degrees):
    theta = math.radians(degrees - 90)
    return (cx + radius * math.cos(theta), cy + radius * math.sin(theta))


def _points(values, labels, cx, cy, inner, outer):
    points = []
    for i, label in enumerate(labels):
        p = values.get(label)
        # Missing values do not imply zero performance: suppress the series.
        if p is None or not math.isfinite(float(p)):
            return None
        r = inner + (outer - inner) * max(0, min(100, float(p))) / 100
        points.append(_xy(cx, cy, r, (i + .5) * 360 / len(labels)))
    return points


def _path(points):
    return 'M ' + ' L '.join(f'{x:.2f},{y:.2f}' for x, y in points) + ' Z'


def render_percentile_radar(percentiles, player_row=None, expected_percentiles=None, return_html=False):
    """Render two comparable percentile polygons over the existing wheel-style grid."""
    labels = [label for _, label, _ in STATS]
    n = len(labels)
    cx, cy, inner, outer = 240, 215, 58, 151
    parts = []
    # Preserve the dark annular background and narrow radial divisions of the wheel.
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{outer}" fill="#232a34" stroke="#454c58" stroke-width="1.3"/>')
    for i in range(n):
        a = i * 360 / n
        x, y = _xy(cx, cy, outer, a)
        parts.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.2f}" y2="{y:.2f}" stroke="#0e1219" stroke-width="2"/>')
    for tick in (25, 50, 75, 100):
        r = inner + (outer - inner) * tick / 100
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r:.2f}" fill="none" stroke="#a1aab9" stroke-opacity=".28" stroke-width="1"/>')
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{inner}" fill="#0e1219" stroke="#454c58" stroke-width="1.3"/>')

    actual_points = _points(percentiles or {}, labels, cx, cy, inner, outer)
    # An unavailable expected metric must not suppress the entire expected
    # polygon. For plotting only, use that player's actual percentile at the
    # same spoke; leave the displayed x-stat missing (no invented estimate).
    expected_for_plot = dict(expected_percentiles or {})
    for label in labels:
        value = expected_for_plot.get(label)
        try:
            available = value is not None and math.isfinite(float(value))
        except (TypeError, ValueError):
            available = False
        if not available:
            actual_value = (percentiles or {}).get(label)
            try:
                if actual_value is not None and math.isfinite(float(actual_value)):
                    expected_for_plot[label] = actual_value
            except (TypeError, ValueError):
                pass
    expected_points = _points(expected_for_plot, labels, cx, cy, inner, outer)
    # Show both series as translucent polygons, with distinctive solid/dashed outlines.
    if expected_points:
        parts.append(f'<path d="{_path(expected_points)}" fill="#72a9f3" fill-opacity=".22" stroke="#8dc5ff" stroke-width="2.5" stroke-dasharray="6 4" stroke-linejoin="round"/>')
    if actual_points:
        parts.append(f'<path d="{_path(actual_points)}" fill="#ed536d" fill-opacity=".24" stroke="#ff657d" stroke-width="2.8" stroke-linejoin="round"/>')
    for points, color in ((expected_points, '#8dc5ff'), (actual_points, '#ff657d')):
        if points:
            for x, y in points:
                parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3.6" fill="{color}"/>')

    gp = pd.to_numeric(player_row.get('GP'), errors='coerce') if player_row is not None else float('nan')
    for i, (key, label, per_game) in enumerate(STATS):
        a = (i + .5) * 360 / n
        x, y = _xy(cx, cy, outer + 19, a)
        anchor = 'middle'
        if 40 < a < 140:
            anchor, x = 'start', x + 1
        elif 220 < a < 320:
            anchor, x = 'end', x - 1
        if 140 <= a <= 220:
            y += 17
        elif a >= 320 or a <= 40:
            # Same top-label clearance as the wedge chart.
            y -= 13
        parts.append(f'<text x="{x:.1f}" y="{y-13:.1f}" text-anchor="{anchor}" font-size="13" font-weight="700" fill="#f1f3f8">{escape(label)}</text>')
        if player_row is not None:
            for is_expected, yoffset in ((False, 2), (True, 16)):
                stat_key = EXPECTED_KEYS.get(key) if is_expected else key
                val = pd.to_numeric(player_row.get(stat_key), errors='coerce') if stat_key else float('nan')
                if per_game and pd.notna(val):
                    val = val / gp if pd.notna(gp) and gp > 0 else float('nan')
                if pd.notna(val):
                    shown = (f'{val:.1f}' if per_game or key == 'Fppg' else f'{val * 100:.1f}%')
                    if is_expected:
                        shown = 'x' + shown
                    parts.append(f'<text x="{x:.1f}" y="{y+yoffset:.1f}" text-anchor="{anchor}" font-size="{"11" if is_expected else "12"}" font-weight="{"400" if is_expected else "700"}" font-style="{"italic" if is_expected else "normal"}" fill="{"#b9c4d4" if is_expected else "#ffffff"}">{escape(shown)}</text>')
    # Legend centered beneath the BLK label and value. Keep the original
    # circle coordinates/viewBox untouched so wheel and radar stay aligned.
    legend = ('<g font-family="Arial,sans-serif" font-size="12">'
              '<line x1="149" y1="445" x2="173" y2="445" stroke="#ff657d" stroke-width="3"/>'
              '<text x="179" y="449" fill="#e9edf3">Actual</text>'
              '<line x1="264" y1="445" x2="288" y2="445" stroke="#8dc5ff" stroke-width="3" stroke-dasharray="6 4"/>'
              '<text x="294" y="449" fill="#e9edf3">Expected</text></g>')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 500 500" role="img" aria-label="NBA actual and expected percentile radar chart">'
           + ''.join(parts) + legend + '</svg>')
    html = ('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<style>html,body{margin:0;background:transparent;font-family:Arial,sans-serif}'
            '.wrap{max-width:580px;margin:auto}svg{width:100%;height:auto;display:block}</style></head><body>'
            f'<div class="wrap">{svg}</div></body></html>')
    if return_html:
        return html
    components.html(html, height=510, scrolling=False)
