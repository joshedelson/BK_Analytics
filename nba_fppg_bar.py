"""Compact, single-bar actual/expected Fppg percentile comparison."""
import math
import pandas as pd
import streamlit.components.v1 as components
from nba_percentile_wheel import _color, _ordinal_suffix


def render_fppg_bar(percentiles, expected_percentiles, player_row, return_html=False):
    p = percentiles.get('Fppg')
    xp = (expected_percentiles or {}).get('Fppg')
    actual = pd.to_numeric(player_row.get('Fppg'), errors='coerce')
    expected = pd.to_numeric(player_row.get('xFppg'), errors='coerce')
    p = float(p) if p is not None and pd.notna(p) else None
    xp = float(xp) if xp is not None and pd.notna(xp) else None
    color = _color(p) if p is not None else '#4d5b6b'
    # Height matches the 320px wheel diameter. Both start at the same y.
    x, y, w, h = 69.64, 65, 30.72, 320
    bottom = y + h
    pieces = [
        '<defs><pattern id="expected-stripes" width="8" height="8" patternUnits="userSpaceOnUse" '
        'patternTransform="rotate(45)"><path d="M0 0V8" stroke="#ffffff" '
        'stroke-opacity=".28" stroke-width="2"/></pattern></defs>',
        '<text x="85" y="21" fill="#fff" text-anchor="middle" font-weight="700" font-size="16">Fppg</text>',
    ]
    for t in (0, 25, 50, 75, 100):
        ty = bottom - h * t / 100
        pieces.append(f'<line x1="48" x2="126" y1="{ty:.2f}" y2="{ty:.2f}" stroke="#8793a4" stroke-opacity=".23"/>')
        if t in (0, 50, 100):
            pieces.append(f'<text x="30" y="{ty+3:.2f}" fill="#aab6c7" font-size="11" text-anchor="end">{t}</text>')
    pieces.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" fill="#252d39" stroke="#586476"/>')
    if p is not None:
        a_top = bottom - h * max(0, min(100, p)) / 100
        pieces.append(f'<rect x="{x}" y="{a_top:.2f}" width="{w}" height="{bottom-a_top:.2f}" fill="{color}"/>')
        pieces.append(f'<line x1="{x-3}" x2="{x+w+3}" y1="{a_top:.2f}" y2="{a_top:.2f}" stroke="#fff" stroke-width="2"/>')
        if bottom - a_top > 34:
            pieces.append(f'<text x="{x+w/2}" y="{min(bottom-14,a_top+24):.2f}" fill="#fff" '
                          f'font-size="14.3" font-weight="700" text-anchor="middle">{round(p)}'
                          f'<tspan font-size="8.8" baseline-shift="super">{_ordinal_suffix(p)}</tspan></text>')
    if xp is not None:
        e_top = bottom - h * max(0, min(100, xp)) / 100
        # Same-color translucent sheet on top of the actual bar; hatched to
        # remain visible when expected and actual heights nearly coincide.
        pieces.append(f'<rect x="{x}" y="{e_top:.2f}" width="{w}" height="{bottom-e_top:.2f}" '
                      f'fill="{color}" fill-opacity=".22"/>')
        pieces.append(f'<rect x="{x}" y="{e_top:.2f}" width="{w}" height="{bottom-e_top:.2f}" '
                      'fill="url(#expected-stripes)"/>')
        pieces.append(f'<line x1="{x-5}" x2="{x+w+5}" y1="{e_top:.2f}" y2="{e_top:.2f}" '
                      'stroke="#f7fbff" stroke-width="2.5" stroke-dasharray="5 3"/>')
        pieces.append(f'<text x="{x+w+11}" y="{max(y+10,min(bottom-5,e_top+3)):.2f}" '
                      'fill="#e3ebf5" font-size="10" font-style="italic">x</text>')
    # Put raw values side-by-side beneath the single bar.
    pieces.append('<text x="85" y="410" fill="#d0d9e5" font-size="10" text-anchor="middle">Actual / Expected</text>')
    if pd.notna(actual):
        pieces.append(f'<text x="64" y="431" fill="#fff" font-size="14.3" font-weight="700" '
                      f'text-anchor="middle">{actual:.1f}</text>')
    if pd.notna(expected):
        pieces.append(f'<text x="107" y="431" fill="#d6deea" font-size="13.2" font-style="italic" '
                      f'text-anchor="middle">x{expected:.1f}</text>')
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 140 450" '
           'role="img" aria-label="Fppg actual and expected percentile comparison">'
           + ''.join(pieces) + '</svg>')
    markup = ('<html><head><style>html,body{margin:0;background:transparent}'
              'svg{display:block;width:105px;height:337.5px;max-width:none;margin:0 auto;position:relative;left:-22.5px;font-family:Arial,Helvetica,sans-serif}</style>'
              '</head><body>' + svg + '</body></html>')
    if return_html:
        return markup
    components.html(markup, height=345, scrolling=False)
