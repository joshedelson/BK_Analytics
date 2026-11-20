"""Client-side wheel/radar selector for NBA Player Quick View.

Both SVGs are pre-rendered into the same iframe. Clicking the switch changes
visibility in the browser only, without triggering a Streamlit fragment rerun.
"""
import re
from html import escape
import streamlit.components.v1 as components


def _svg_from_html(chart_html):
    match = re.search(r"<svg\\b[^>]*>.*?</svg>", chart_html, re.I | re.S)
    return match.group(0) if match else ""


def render_quick_chart_switch(wheel_html, radar_html):
    wheel_svg = _svg_from_html(wheel_html)
    radar_svg = _svg_from_html(radar_html)
    markup = """<!doctype html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
html,body{margin:0;background:transparent;color:#f2f4f7;font-family:Arial,sans-serif}
.chart-switch{display:flex;align-items:center;justify-content:center;gap:10px;margin:0 0 3px;font-size:12px;color:#cbd5e1}
.chart-switch button{background:#181f2b;border:1px solid #485366;border-radius:7px;color:#cbd5e1;padding:5px 11px;cursor:pointer;font:inherit}
.chart-switch button[aria-pressed="true"]{background:#34435b;border-color:#8ca7cc;color:#fff;font-weight:700}
.chart{max-width:540px;margin:auto}
.chart svg{width:100%;height:auto;display:block}
.chart[hidden]{display:none!important}
</style></head><body>
<div class="chart-switch" role="group" aria-label="Percentile chart style">
  <button id="wheel-btn" type="button" aria-pressed="true">Wheel</button>
  <button id="radar-btn" type="button" aria-pressed="false">Radar</button>
</div>
<div id="wheel-chart" class="chart">__WHEEL__</div>
<div id="radar-chart" class="chart" hidden>__RADAR__</div>
<script>
const wheel=document.getElementById('wheel-chart');
const radar=document.getElementById('radar-chart');
const wb=document.getElementById('wheel-btn');
const rb=document.getElementById('radar-btn');
function show(mode){
  const isWheel=mode==='wheel';
  wheel.hidden=!isWheel;radar.hidden=isWheel;
  wb.setAttribute('aria-pressed',String(isWheel));
  rb.setAttribute('aria-pressed',String(!isWheel));
}
wb.addEventListener('click',()=>show('wheel'));
rb.addEventListener('click',()=>show('radar'));
</script></body></html>"""
    markup = markup.replace("__WHEEL__", wheel_svg).replace("__RADAR__", radar_svg)
    components.html(markup, height=510, scrolling=False)
