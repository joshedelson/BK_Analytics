"""NBA adaptation of the MLB v223 virtualized compact table. No network requests."""
import html
import pandas as pd


def render_nba_html(display, *, selected_focus, freeze_player, sort_stat,
                    theme_bg, theme_fg, theme_accent, stat_mode, logo_url,
                    compact_player=False, show_rank=True, show_player_id=False, footer_note=""):
    def esc(value):
        return html.escape(str(value), quote=True)

    def fmt(column, value):
        n = pd.to_numeric(value, errors='coerce')
        if pd.isna(n):
            if column in ('Fantasy team', 'Confidence'):
                return esc(value) if pd.notna(value) and str(value).strip() else '—'
            return '—'
        if column in ('YR', 'Yr', 'Year', 'Team ID', 'Team Id', 'TEAM_ID', 'Player ID'):
            return f'{n:.0f}'
        if column in ('Rk', 'Proj. Rk', 'AGE', 'GP', 'Fpts', 'xFpts', 'Current Rank', 'Projected Rank'):
            return f'{n:,.0f}'
        if column in ('Fppg', 'xFppg', 'pFppg', 'hFppg', 'Projected Fppg', 'Historical Fppg'):
            return f'{n:,.2f}'
        if '%' in column or column == 'ΔUSG':
            return f'{n:.1f}%'
        if stat_mode == 'Totals':
            return f'{n:,.0f}'
        return f'{n:,.1f}'.rstrip('0').rstrip('.')

    visible = [c for c in display.columns if c not in
               ('Player ID', 'Season', 'Team', 'Positions', 'Injury',
                'Fantasy', 'Headshot', '_focus', '_name')]
    if show_player_id and 'Player ID' in display.columns:
        # Player ID is normally hidden because it is used internally for click
        # navigation. Display it only when explicitly requested by main table.
        anchor = visible.index('Team ID') + 1 if 'Team ID' in visible else len(visible)
        visible.insert(anchor, 'Player ID')
    if show_rank:
        visible = ['Rk', 'Headshot', 'Player'] + [c for c in visible if c not in ('Rk', 'Player')]
    else:
        visible = ['Player'] + [c for c in visible if c != 'Player']
    # Display-only abbreviations for NBA Hustle statistics. Preserve source
    # column names for sorting, calculations, filters, and exports.
    hustle_headers = {
        'Contested Shots': 'Cont. Shots',
        'Contested 2PT': 'Cont. 2PT',
        'Contested 3PT': 'Cont. 3PT',
        'Deflections': 'Defl.',
        'Charges Drawn': 'Chg. Drawn',
        'Contested OREB': 'Cont. OREB',
        'Uncontested OREB': 'Uncont. OREB',
        'OREB Chances': 'OREB Ch.',
        'OREB Chances Deferred': 'OREB Ch. Def.',
        'Contested DREB': 'Cont. DREB',
        'Uncontested DREB': 'Uncont. DREB',
        'DREB Chances': 'DREB Ch.',
        'DREB Chances Deferred': 'DREB Ch. Def.',
        'Contested REB': 'Cont. REB',
        'Uncontested REB': 'Uncont. REB',
        'REB Chances': 'REB Ch.',
        'REB Chances Deferred': 'REB Ch. Def.',
        'Passes Made': 'Pass Made',
        'Passes Received': 'Pass Rec.',
        'Potential AST': 'Pot. AST',
        'Touches': 'Touches',
    }
    header = ''.join(
        '<th class="photo-head" aria-label="Headshot"></th>' if c == "Headshot" else f'<th class="{("player-header " if c == "Player" else "") + ("sorted-stat" if c == sort_stat else "")}"'
        f' title="{esc(c)}">{esc(hustle_headers.get(c, c))} <small>↕</small></th>'
        for c in visible
    )
    trs = []
    for _, r in display.iterrows():
        name = str(r.get('_name', r['Player']))
        team = str(r.get('Team', '') or '')
        logo = logo_url(team)
        logo_html = f'<img class="club-logo" src="{esc(logo)}" alt="" loading="lazy" decoding="async" width="16" height="16">' if logo else ''
        photo = str(r.get('Headshot', '') or '')
        img = f'<img class="portrait" src="{esc(photo)}" alt="" loading="lazy" decoding="async" width="{56 if show_rank else 52}" height="{40 if show_rank else 38}">' if photo.startswith('https://') else ''
        injury = str(r.get('Injury', '') or '').upper()
        injury = injury if injury not in ('NAN', 'NONE', '<NA>') else ''
        badge = f'<span class="injury injury-{esc(injury.lower())}">{esc(injury)}</span>' if injury else ''
        fantasy = str(r.get('Fantasy', '') or '').strip().upper()
        fantasy_badge = f'<span class="fantasy-badge">{esc(fantasy)}</span>' if fantasy not in ('', 'FA', 'NAN', 'NONE') else ''
        focused = ' focused' if bool(r.get('_focus', False)) else ''
        detail = f'{logo_html}{esc(team)} · {esc(str(r.get("Positions", "") or ""))}'
        if compact_player and not show_rank:
            name_html = (f'<span class="player-name"><span class="player-text">{esc(name)}</span>'
                         f'{badge}{fantasy_badge}</span>')
        else:
            name_html = (f'<span class="player-name"><span style="font-weight:700">{esc(name)}</span>{badge}{fantasy_badge}</span>' if show_rank else f'<span class="player-name">{esc(name)}{badge}{fantasy_badge}</span>')
        person = (f'<div class="person">{img if not show_rank else ""}<div class="identity">{name_html}'
                  f'<span class="detail">{detail}</span></div></div>')
        cells = []
        for c in visible:
            if c == 'Headshot':
                cells.append(f'<td class="photo-cell">{img}</td>')
            elif c == 'Player':
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
    sticky_player_css = """th.photo-head,td.photo-cell{width:62px;min-width:62px;max-width:62px;text-align:center;padding:0 3px;overflow:hidden}
    td.photo-cell{cursor:pointer} th.photo-head{cursor:default}
    .freeze-photo td.photo-cell{position:sticky;left:0;z-index:4}
    .freeze-photo th.photo-head{left:0;z-index:9}
    td.photo-cell .portrait{display:block;margin:auto}
    """
    footer = ('<div class="table-footer grid-footer"><label class="freeze-label"><input id="freeze-photo" type="checkbox" role="switch"><span>Freeze Headshot</span></label><span class="footer-note">'+esc(footer_note)+'</span></div>') if show_rank else ''
    player_width_css = ('''
    th:first-child,td.player{position:sticky;left:0;min-width:220px;
        text-align:left;overflow:hidden}
    td.player{z-index:5;background:var(--bg)}
    thead th:first-child{z-index:10;text-align:left;background:var(--bg)}
    tr:hover td.player{background:{hover_bg}}
    td.player .person{width:100%;min-width:0;overflow:hidden}
    td.player .portrait{flex:0 0 46px;width:46px;height:36px}
    td.player .identity{flex:1 1 0;min-width:0;max-width:none;overflow:hidden}
    td.player .player-name{display:flex;align-items:center;gap:3px;
        min-width:0;max-width:100%;overflow:hidden;white-space:nowrap}
    td.player .player-text{flex:0 1 auto;min-width:0;overflow:hidden;
        text-overflow:ellipsis;white-space:nowrap}
    td.player .detail{min-width:0;max-width:100%;overflow:hidden;
        text-overflow:ellipsis;white-space:nowrap}
    '''.replace('{hover_bg}', hover_bg) if compact_player and not show_rank else
    'th.player-header,td.player{min-width:190px;width:190px;max-width:190px}' if compact_player else '')
    # Compact widths apply only to the regression grids, not the main table.
    regression_widths = {
        'Player': 220, 'Rk': 44, 'Proj. Rk': 66, 'GP': 40,
        'Fppg': 63, 'xFppg': 66, 'hFppg': 66, 'pFppg': 66,
        'MPG': 52, 'Proj. MPG': 78, 'USG%': 62, 'ΔUSG': 59,
        'Confidence': 79, 'ROS Upside': 88, 'Sell-High Gap': 100,
    }
    regression_mode = compact_player and not show_rank
    # Percentages let the regression grid fill its container while the
    # minimum width keeps every heading and player identity readable.
    regression_width_css = ''
    explicit_colgroup = ''
    regression_table_css = ''
    if regression_mode:
        total_width = sum(regression_widths.get(col, 70) for col in visible)
        explicit_colgroup = '<colgroup>' + ''.join(
            f'<col style="width:{regression_widths.get(col, 70) / total_width * 100:.5f}%">'
            for col in visible
        ) + '</colgroup>'
        regression_width_css = '\n'.join(
            f'th:nth-child({i}),td:nth-child({i})' +
            '{min-width:' + str(regression_widths.get(col, 70)) + 'px}'
            for i, col in enumerate(visible, 1)
        )
        regression_table_css = (
            f'table{{table-layout:fixed!important;width:100%!important;'
            f'min-width:{total_width}px!important}}'
            'td.player,th:first-child{overflow:hidden!important}'
            'td.player{max-width:none!important;width:auto!important}'
            'th:first-child{max-width:none!important;width:auto!important}'
        )
    doc=f"""<!doctype html><html><head><meta charset="utf-8"><style>
    :root{{--bg:{theme_bg};--fg:{theme_fg};--accent:{theme_accent}}}
    *{{box-sizing:border-box}}html,body{{background:var(--bg);overflow:hidden}}
    body{{margin:0;color:var(--fg);background:var(--bg);font:14px var(--app-font,"Source Sans","Source Sans Pro",-apple-system,"Segoe UI",Roboto,sans-serif)}}
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
    th.player-header{{text-align:left!important}}
    td{{padding:1px 5px;text-align:right;white-space:nowrap;border-bottom:1px solid #27333d;background:var(--bg)}}
    tr:hover td,.freeze-photo tr:hover td.photo-cell{{background:{hover_bg}}}
    /* Color only the active sort header text; leave backgrounds and data cells untouched. */
    thead th.sorted-stat{{color:var(--accent)!important}}
    {sticky_player_css if show_rank else ''}
    td.player{{text-align:left;min-width:210px}}
    {player_width_css}
    {regression_width_css}
    {regression_table_css}
    td.player .person{{cursor:pointer}}
    td.player .identity .player-name:hover{{color:var(--accent);text-decoration:underline}}
    .person{{display:flex;align-items:center;gap:5px;height:{41 if show_rank else 39}px}}
    tbody tr.data-row{{height:{44 if show_rank else 41}px}}
    tbody tr.virtual-spacer td{{height:0;padding:0;border:0;line-height:0;background:var(--bg)!important;pointer-events:none}}
    .portrait{{width:{56 if show_rank else 52}px;height:{40 if show_rank else 38}px;object-fit:contain}}
    .identity{{display:flex;flex-direction:column;gap:1px}}
    .identity .player-name{{display:flex;align-items:center;gap:4px;font-size:14px;font-weight:400}}
    .detail{{display:flex;align-items:center;gap:3px;opacity:.75;font-size:12px}}
    .club-logo{{width:16px;height:16px;object-fit:contain}}
    .fantasy-badge{{flex:0 0 auto;font-size:9px;padding:1px 4px;border-radius:3px;color:var(--accent);border:1px solid var(--accent)}}
    .injury{{display:inline-block;margin-left:4px;padding:1px 3px;border-radius:3px;font-size:10px;font-weight:800;line-height:1.2;vertical-align:1px}}
    .injury-o,.injury-ir{{color:#ff5964;background:rgba(255,89,100,.13)}}
    .injury-dtd{{color:#f4c453;background:rgba(244,196,83,.13)}}
    td.player.focused{{border-left:4px solid var(--accent);padding-left:1px;
      background:{focus_bg}}}
    tr:hover td.player.focused{{background:{focus_hover_bg}}}
    .table-footer,.grid-footer{{background:var(--bg);display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;padding:8px 0 6px;min-height:30px}}
    .freeze-label{{display:flex;align-items:center;gap:7px;cursor:pointer;white-space:nowrap}}
    .freeze-label input{{appearance:none;width:30px;height:17px;border-radius:12px;background:#485563;position:relative;margin:0;cursor:pointer}}
    .freeze-label input:checked{{background:var(--accent)}}
    .freeze-label input:before{{content:"";position:absolute;width:13px;height:13px;left:2px;top:2px;border-radius:50%;background:white}}
    .freeze-label input:checked:before{{left:15px}}
    .footer-note{{color:#929ba6;font-size:12px;text-align:right}}
    </style></head><body><div class="viewport"><table>{explicit_colgroup}<thead><tr>{header}</tr></thead>
    <tbody>{''.join(trs)}</tbody></table></div>{footer}<script>
    const freezeToggle=document.getElementById('freeze-photo');
    if(freezeToggle){{
      function applyFreeze(value){{freezeToggle.checked=!!value;document.querySelector('.viewport').classList.toggle('freeze-photo',!!value);}}
      window.addEventListener('message',e=>{{if(e.source===window.parent && e.data?.type==='nba-freeze-state')applyFreeze(e.data.value);}});
      freezeToggle.addEventListener('change',()=>{{applyFreeze(freezeToggle.checked);window.parent.postMessage({{type:'nba-freeze-change',value:freezeToggle.checked}},'*');}});
      window.parent.postMessage({{type:'nba-freeze-ready'}},'*');
      const reportHeight=()=>window.parent.postMessage({{type:'nba-table-height',height:Math.ceil(document.querySelector('.table-footer').getBoundingClientRect().bottom)}},'*');
      new ResizeObserver(reportHeight).observe(document.querySelector('.table-footer'));
      new ResizeObserver(reportHeight).observe(document.querySelector('.viewport'));
      reportHeight();
    }}
    // Virtualize rows inside the existing scrolling viewport. Sorting remains
    // client-side and works across the COMPLETE dataset, not just visible rows.
    document.fonts.ready.then(()=>{{
    const viewport=document.querySelector('.viewport');
    const table=document.querySelector('table');
    const heads=[...document.querySelectorAll('th')];
    const body=document.querySelector('tbody');
    const allRows=[...body.rows];
    const ROW_HEIGHT={44 if show_rank else 41}, OVERSCAN=16, WINDOW_STEP=12;
    let orderedRows=allRows.slice(), last=-1, asc=true;
    let firstRendered=-1, lastRendered=-1, frame=0;
    // Preserve measured widths before removing offscreen cells. This prevents
    // the table from changing width as different players come into view.
    const measured=heads.map(h=>Math.ceil(h.getBoundingClientRect().width));
    const measuredTable=Math.ceil(table.getBoundingClientRect().width);
    if(!table.querySelector('colgroup')){{
      const colgroup=document.createElement('colgroup');
      measured.forEach(w=>{{const col=document.createElement('col');col.style.width=w+'px';colgroup.appendChild(col);}});
      table.insertBefore(colgroup,table.firstChild);
      table.style.width=measuredTable+'px';
      table.style.minWidth='100%';
    }}
    table.style.tableLayout='fixed';
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
      if(!event.target.closest('td.player .person,td.photo-cell'))return;
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
      if(h.classList.contains('photo-head'))return;
      highlightColumn(i);
      asc=last===i?!asc:i===0;last=i;
      orderedRows.sort((a,b)=>{{
        const x=a.cells[i].dataset.sort||'',y=b.cells[i].dataset.sort||'';
        const nx=Number(x),ny=Number(y);
        const v=x&&y&&!isNaN(nx)&&!isNaN(ny)?nx-ny:
          x.localeCompare(y,undefined,{{numeric:true}});
        return v*(asc?1:-1);
      }});
      heads.forEach((head,j)=>{{const indicator=head.querySelector('small');if(indicator)indicator.textContent=j===i?(asc?'▲':'▼'):'↕';}});
      viewport.scrollTop=0;
      paint(true);
    }}));
    paint(true);
    }});
    </script></body></html>"""
    return doc
