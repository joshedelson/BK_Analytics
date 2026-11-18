"""NBA adaptation of the MLB v223 virtualized compact table. No network requests."""
import html
import pandas as pd


def render_nba_html(display, *, selected_focus, freeze_player, sort_stat,
                    theme_bg, theme_fg, theme_accent, stat_mode, logo_url,
                    compact_player=False):
    def esc(value):
        return html.escape(str(value), quote=True)

    def fmt(column, value):
        n = pd.to_numeric(value, errors='coerce')
        if pd.isna(n):
            return '—'
        if column in ('Rk', 'AGE', 'GP', 'Fpts', 'xFpts'):
            return f'{n:,.0f}'
        if column in ('Fppg', 'xFppg'):
            return f'{n:,.2f}'
        if '%' in column:
            return f'{n:.1f}%'
        if stat_mode == 'Totals':
            return f'{n:,.0f}'
        return f'{n:,.1f}'.rstrip('0').rstrip('.')

    visible = [c for c in display.columns if c not in
               ('Player ID', 'Season', 'Team', 'Positions', 'Injury',
                'Fantasy', 'Headshot', '_focus', '_name')]
    visible = ['Rk', 'Player'] + [c for c in visible if c not in ('Rk', 'Player')]
    header = ''.join(f'<th{" class=sorted-stat" if c == sort_stat else ""}>{esc(c)} <small>↕</small></th>' for c in visible)
    trs = []
    for _, r in display.iterrows():
        name = str(r.get('_name', r['Player']))
        team = str(r.get('Team', '') or '')
        logo = logo_url(team)
        logo_html = f'<img class="club-logo" src="{esc(logo)}" alt="" loading="lazy" decoding="async" width="16" height="16">' if logo else ''
        photo = str(r.get('Headshot', '') or '')
        img = f'<img class="portrait" src="{esc(photo)}" alt="" loading="lazy" decoding="async" width="52" height="38">' if photo.startswith('https://') else ''
        injury = str(r.get('Injury', '') or '').upper()
        injury = injury if injury not in ('NAN', 'NONE', '<NA>') else ''
        badge = f'<span class="injury injury-{esc(injury.lower())}">{esc(injury)}</span>' if injury else ''
        fantasy = str(r.get('Fantasy', '') or '').strip().upper()
        fantasy_badge = f'<span class="fantasy-badge">{esc(fantasy)}</span>' if fantasy not in ('', 'FA', 'NAN', 'NONE') else ''
        focused = ' focused' if bool(r.get('_focus', False)) else ''
        detail = f'{logo_html}{esc(team)} · {esc(str(r.get("Positions", "") or ""))}'
        person = (f'<div class="person">{img}<div class="identity"><span class="player-name">{esc(name)}{badge}{fantasy_badge}</span>'
                  f'<span class="detail">{detail}</span></div></div>')
        cells = []
        for c in visible:
            if c == 'Player':
                cells.append(f'<td class="player{focused}" data-sort="{esc(name.casefold())}">{person}</td>')
            else:
                v = r.get(c)
                numeric = pd.to_numeric(v, errors='coerce')
                sort = '' if pd.isna(numeric) else str(numeric)
                cells.append(f'<td data-sort="{esc(sort)}">{fmt(c, v)}</td>')
        pid = pd.to_numeric(r.get('Player ID'), errors='coerce')
        if pd.isna(pid):
            continue
        yr = str(r.get('Season', ''))
        try:
            year = int(yr[:4]) + 1 if '-' in yr else int(yr)
        except ValueError:
            year = 0
        trs.append(f'<tr data-player-id="{int(pid)}" data-year="{year}" data-kind="nba">'+''.join(cells)+'</tr>')
    def _rgb(hex_value, fallback):
        v = str(hex_value or '').strip().lstrip('#')
        if len(v) == 3:
            v = ''.join(ch * 2 for ch in v)
        if len(v) != 6:
            return fallback
        try:
            return tuple(int(v[i:i+2], 16) for i in (0, 2, 4))
        except ValueError:
            return fallback
    def _blend(bg, fg, alpha):
        a, b = _rgb(bg, (14, 25, 35)), _rgb(fg, (233, 242, 245))
        return '#' + ''.join(f'{round(x*(1-alpha)+y*alpha):02x}' for x, y in zip(a, b))
    hover_bg = _blend(theme_bg, theme_accent, .12)
    focus_bg = _blend(theme_bg, theme_accent, .19)
    focus_hover_bg = _blend(theme_bg, theme_accent, .29)
    sticky_player_css = (''' 
    th:first-child,td:first-child{position:sticky;left:0;z-index:4;min-width:35px}
    th:nth-child(2),td:nth-child(2){position:sticky;left:35px;z-index:4}
    th:first-child,th:nth-child(2){z-index:8}
    ''' if freeze_player else '''
    th:first-child,td:first-child{min-width:44px;width:44px;max-width:44px;overflow:hidden}
    ''')
    player_width_css = ('th:nth-child(2),td.player{min-width:190px;width:190px;max-width:190px}'
                        if compact_player else '')
    doc=f"""<!doctype html><html><head><meta charset="utf-8"><style>
    :root{{--bg:{theme_bg};--fg:{theme_fg};--accent:{theme_accent}}}
    *{{box-sizing:border-box}}html{{color-scheme:dark}}
    body{{margin:0;color:var(--fg);background:transparent;font:14px var(--app-font,"Source Sans","Source Sans Pro",-apple-system,"Segoe UI",Roboto,sans-serif)}}
    .viewport{{overflow:auto;max-height:575px;border:1px solid #34414d;border-radius:7px;
    overscroll-behavior:contain;
    scrollbar-color:#485563 #111923;scrollbar-width:thin;color-scheme:dark}}
    .viewport::-webkit-scrollbar{{width:11px;height:11px;background:#111923}}
    .viewport::-webkit-scrollbar-track{{background:#111923}}
    .viewport::-webkit-scrollbar-thumb{{background:#485563;border:2px solid #111923;border-radius:8px}}
    .viewport::-webkit-scrollbar-corner{{background:#111923}}
    table{{border-collapse:separate;border-spacing:0;width:max-content;min-width:100%}}
    thead{{position:sticky;top:0;z-index:6}}
    th{{position:sticky;top:0;z-index:6;background:var(--bg);padding:7px 5px;text-align:right;
    white-space:nowrap;cursor:pointer;border-bottom:1px solid #34414d;font-weight:600}}
    th small{{opacity:.55}}th:hover{{color:var(--accent)}}
    td{{padding:1px 5px;text-align:right;white-space:nowrap;border-bottom:1px solid #27333d;background:var(--bg)}}
    tr:hover td{{background:{hover_bg}}}
    /* Color only the active sort header text; leave backgrounds and data cells untouched. */
    thead th.sorted-stat{{color:var(--accent)!important}}
    {sticky_player_css}
    th:nth-child(2),td.player{{text-align:left;min-width:210px}}
    {player_width_css}
    td.player .person{{cursor:pointer}}
    td.player .identity .player-name:hover{{color:var(--accent);text-decoration:underline}}
    .person{{display:flex;align-items:center;gap:5px;height:39px}}
    tbody tr.data-row{{height:41px}}
    tbody tr.virtual-spacer td{{height:0;padding:0;border:0;line-height:0;background:var(--bg)!important;pointer-events:none}}
    .portrait{{width:52px;height:38px;object-fit:contain}}
    .identity{{display:flex;flex-direction:column;gap:1px}}
    .identity .player-name{{display:flex;align-items:center;gap:4px;font-size:14px;font-weight:400}}
    .detail{{display:flex;align-items:center;gap:3px;opacity:.75;font-size:12px}}
    .club-logo{{width:16px;height:16px;object-fit:contain}}
    .fantasy-badge{{font-size:9px;padding:1px 4px;border-radius:3px;color:var(--accent);border:1px solid var(--accent)}}
    .injury{{display:inline-block;margin-left:4px;padding:1px 3px;border-radius:3px;font-size:10px;font-weight:800;line-height:1.2;vertical-align:1px}}
    .injury-o,.injury-ir{{color:#ff5964;background:rgba(255,89,100,.13)}}
    .injury-dtd{{color:#f4c453;background:rgba(244,196,83,.13)}}
    td.player.focused{{border-left:4px solid var(--accent);padding-left:1px;
      background:{focus_bg}}}
    tr:hover td.player.focused{{background:{focus_hover_bg}}}
    </style></head><body><div class="viewport"><table><thead><tr>{header}</tr></thead>
    <tbody>{''.join(trs)}</tbody></table></div><script>
    // Virtualize rows inside the existing scrolling viewport. Sorting remains
    // client-side and works across the COMPLETE dataset, not just visible rows.
    document.fonts.ready.then(()=>{{
    const viewport=document.querySelector('.viewport');
    const table=document.querySelector('table');
    const heads=[...document.querySelectorAll('th')];
    const body=document.querySelector('tbody');
    const allRows=[...body.rows];
    const ROW_HEIGHT=41, OVERSCAN=16, WINDOW_STEP=12;
    let orderedRows=allRows.slice(), last=-1, asc=true;
    let firstRendered=-1, lastRendered=-1, frame=0;
    // Preserve measured widths before removing offscreen cells. This prevents
    // the table from changing width as different players come into view.
    const measured=heads.map(h=>Math.ceil(h.getBoundingClientRect().width));
    const measuredTable=Math.ceil(table.getBoundingClientRect().width);
    const colgroup=document.createElement('colgroup');
    measured.forEach(w=>{{const col=document.createElement('col');col.style.width=w+'px';colgroup.appendChild(col);}});
    table.insertBefore(colgroup,table.firstChild);
    table.style.tableLayout='fixed';
    table.style.width=measuredTable+'px';
    table.style.minWidth='100%';
    allRows.forEach(r=>r.classList.add('data-row'));
    const spacer=(height)=>{{
      const tr=document.createElement('tr');tr.className='virtual-spacer';
      const td=document.createElement('td');td.colSpan=heads.length;
      td.style.height=Math.max(0,height)+'px';
      tr.appendChild(td);return tr;
    }};
    function paint(force=false){{
      const count=orderedRows.length;
      const firstVisible=Math.floor(viewport.scrollTop/ROW_HEIGHT);
      const visible=Math.ceil(viewport.clientHeight/ROW_HEIGHT);
      // Keep the same DOM nodes until the viewport nears the window edge.
      // This avoids replacing dozens of table rows for every small wheel movement.
      if(!force && firstRendered>=0 &&
         firstVisible>=firstRendered+OVERSCAN/2 &&
         firstVisible+visible<=lastRendered-OVERSCAN/2)return;
      const top=Math.max(0,Math.floor(Math.max(0,firstVisible-OVERSCAN)/WINDOW_STEP)*WINDOW_STEP);
      const bottom=Math.min(count,top+visible+2*OVERSCAN+WINDOW_STEP);
      if(!force && top===firstRendered && bottom===lastRendered)return;
      firstRendered=top;lastRendered=bottom;
      const fragment=document.createDocumentFragment();
      if(top)fragment.appendChild(spacer(top*ROW_HEIGHT));
      for(let i=top;i<bottom;i++)fragment.appendChild(orderedRows[i]);
      if(bottom<count)fragment.appendChild(spacer((count-bottom)*ROW_HEIGHT));
      body.replaceChildren(fragment);
    }}
    function schedulePaint(){{if(frame)return;frame=requestAnimationFrame(()=>{{frame=0;paint();}});}}
    viewport.addEventListener('scroll',schedulePaint,{{passive:true}});
    // Event delegation survives rows being moved into and out of the DOM.
    body.addEventListener('click',event=>{{
      if(!event.target.closest('td.player .person'))return;
      const row=event.target.closest('tr[data-player-id]');
      if(!row)return;
      window.parent.postMessage({{type:'mlb-player-click',
        player_id:Number(row.dataset.playerId),year:Number(row.dataset.year),
        kind:"nba",nonce:Date.now()}},'*');
    }});
    function highlightColumn(index){{
      // Header-only update: O(number of columns), independent of row count.
      heads.forEach((h,j)=>h.classList.toggle('sorted-stat',j===index));
    }}
    heads.forEach((h,i)=>h.addEventListener('click',()=>{{
      highlightColumn(i);
      asc=last===i?!asc:i===0;last=i;
      orderedRows.sort((a,b)=>{{
        const x=a.cells[i].dataset.sort||'',y=b.cells[i].dataset.sort||'';
        const nx=Number(x),ny=Number(y);
        const v=x&&y&&!isNaN(nx)&&!isNaN(ny)?nx-ny:
          x.localeCompare(y,undefined,{{numeric:true}});
        return v*(asc?1:-1);
      }});
      heads.forEach((head,j)=>head.querySelector('small').textContent=j===i?(asc?'▲':'▼'):'↕');
      viewport.scrollTop=0;
      paint(true);
    }}));
    paint(true);
    }});
    </script></body></html>"""
    return doc
