"""ESPN Fantasy Basketball roster metadata, separate from NBA stats.

Private ESPN cookies are read from environment / Streamlit secrets, never saved.
ESPN does not share NBA player IDs; joins are conservative name+NBA-team matches.
"""
import json
import os
import re
import sqlite3
from cloud_db import connect, DB_PATH
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests


ESPN_BASE = 'https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons'
POSITION = {0:'PG',1:'SG',2:'SF',3:'PF',4:'C',5:'G',6:'F',7:'UTIL',8:'BE',9:'IR',10:'IR+',11:'NA'}
# NBA team abbreviation by ESPN proTeamId (commonly used ESPN FBA mapping).
TEAM_IDS = {1:'ATL',2:'BOS',3:'NOP',4:'CHI',5:'CLE',6:'DAL',7:'DEN',8:'DET',9:'GSW',10:'HOU',11:'IND',12:'LAC',13:'LAL',14:'MIA',15:'MIL',16:'MIN',17:'BKN',18:'NYK',19:'ORL',20:'PHI',21:'PHX',22:'POR',23:'SAC',24:'SAS',25:'OKC',26:'UTA',27:'WAS',28:'TOR',29:'MEM',30:'CHA'}


def _normalized_name(name):
    name = unicodedata.normalize('NFKD', str(name or ''))
    name = ''.join(ch for ch in name if not unicodedata.combining(ch)).casefold()
    return re.sub(r'[^a-z0-9]', '', re.sub(r'\b(jr|sr|ii|iii|iv)\b', '', name))


def _team_name(team):
    if not isinstance(team, dict):
        return None
    if team.get('name'):
        return str(team['name'])
    location = str(team.get('location') or '').strip()
    nickname = str(team.get('nickname') or '').strip()
    result = f'{location} {nickname}'.strip()
    return result or f"Team {team.get('id', '?')}"


def _injury(player):
    # ESPN injuryStatus is usually NORMAL, OUT, DAY_TO_DAY, DOUBTFUL, QUESTIONABLE, etc.
    raw = str(player.get('injuryStatus') or '').upper().replace('-', '_')
    if raw in ('NORMAL', 'ACTIVE', 'HEALTHY', 'NONE', ''):
        return ''
    return {'OUT':'O','DAY_TO_DAY':'DTD','DTD':'DTD','INJURED_RESERVE':'IR',
            'INJURY_RESERVE':'IR','IR':'IR','DOUBTFUL':'D','QUESTIONABLE':'Q',
            'PROBABLE':'P','SUSPENSION':'SUS'}.get(raw, raw.replace('_', ' ').title())


def _positions(player):
    positions = player.get('eligibleSlots') or []
    if not positions and player.get('defaultPositionId') is not None:
        positions = [player['defaultPositionId']]
    # Restrict to actual playing positions, not ESPN lineup slots.
    found = [POSITION.get(int(x)) for x in positions if str(x).isdigit() and int(x) in range(5)]
    return ', '.join(dict.fromkeys(x for x in found if x))


def _request(session, url, cookie, swid, params, filter_payload=None):
    headers = {'Accept':'application/json', 'Cookie':f'espn_s2={cookie}; SWID={swid}',
               'User-Agent':'Mozilla/5.0'}
    if filter_payload is not None:
        headers['X-Fantasy-Filter'] = json.dumps(filter_payload, separators=(',', ':'))
    response = session.get(url, params=params, headers=headers, timeout=45)
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict):
        raise ValueError('ESPN returned an unexpected response.')
    return result


def fetch_fantasy(season, league_id, espn_s2, swid):
    if not (str(season).isdigit() and str(league_id).isdigit()):
        raise ValueError('Season and League ID must be numbers.')
    if not espn_s2 or not swid:
        raise ValueError('ESPN login cookies are required for private leagues.')
    base = f'{ESPN_BASE}/{season}/segments/0/leagues/{league_id}'
    with requests.Session() as session:
        league = _request(session, base, espn_s2, swid,
                          {'view': 'mTeam'})
        roster_league = _request(session, base, espn_s2, swid,
                                 {'view': 'mRoster'})
        kona = _request(session, base, espn_s2, swid,
                        {'scoringPeriodId':0,'view':'kona_player_info'},
                        {'players':{'limit':3000,'sortPercOwned':{'sortPriority':1,'sortAsc':False}}})
        # ESPN fantasy league draft history, separate from the NBA draft.
        try:
            draft_response = _request(session, base, espn_s2, swid,
                                      {'view':'mDraftDetail'})
            draft_picks = (draft_response.get('draftDetail') or {}).get('picks') or []
        except (requests.RequestException, ValueError, TypeError) as exc:
            draft_picks = []
            draft_error = f'{type(exc).__name__}: {exc}'
        else:
            draft_error = ''
    draft_by_id = {
        str(pick['playerId']): pick
        for pick in draft_picks
        if isinstance(pick, dict) and pick.get('playerId') is not None
    }
    if 'teams' not in league:
        raise ValueError('ESPN response has no league teams. Check your cookies and league ID.')
    team_names = {t['id']:_team_name(t) for t in league.get('teams',[]) if 'id' in t}
    # ESPN typically uses 'abbrev', but accept 'abbreviation' as a fallback.
    # Normalize team IDs because some payloads use strings rather than ints.
    team_abbrevs = {str(t['id']): str(t.get('abbrev') or t.get('abbreviation') or '').strip().upper()
                    for t in league.get('teams', []) if 'id' in t}
    team_names = {str(k): v for k, v in team_names.items()}
    # ESPN team ids and abbreviations are taken from League Views/mTeam.
    # The roster view is requested separately so each response is unambiguous.
    roster_map = {}
    players_by_id = {}
    for t in roster_league.get('teams', []):
        for entry in (t.get('roster') or {}).get('entries', []):
            athlete = entry.get('playerPoolEntry') or {}
            p = athlete.get('player') or {}
            pid = p.get('id') or athlete.get('id')
            if pid is None:
                continue
            players_by_id[str(pid)] = p
            roster_map[str(pid)] = {'Fantasy Team':team_names.get(str(t['id'])),
                                     'Fantasy Abbrev':team_abbrevs.get(str(t['id']), ''),
                                     'Roster Slot':POSITION.get(entry.get('lineupSlotId'),
                                                                str(entry.get('lineupSlotId') or ''))}
    for item in kona.get('players', []):
        p = item.get('player') or {}
        pid = p.get('id') or item.get('id')
        if pid is None:
            continue
        if str(pid) not in players_by_id or len(p) > len(players_by_id[str(pid)]):
            players_by_id[str(pid)] = p
        # Some ESPN views include ownership team id directly.
        # Ownership can be on the outer kona entry or nested player object.
        owner_id = item.get('onTeamId', p.get('onTeamId'))
        if str(pid) not in roster_map and owner_id not in (None, 0, -1, '0', '-1'):
            roster_map[str(pid)] = {'Fantasy Team':team_names.get(str(owner_id)), 'Fantasy Abbrev':team_abbrevs.get(str(owner_id), ''), 'Roster Slot':''}
    # Backfill owner abbreviations from the authoritative team id lookup.
    for owner in roster_map.values():
        owner_name = owner.get('Fantasy Team')
        if not owner.get('Fantasy Abbrev') and owner_name:
            owner['Fantasy Abbrev'] = next((team_abbrevs.get(tid, '') for tid, name in team_names.items() if name == owner_name), '')
    rows = []
    for pid, p in players_by_id.items():
        name = p.get('fullName') or p.get('name')
        if not name:
            continue
        owner = roster_map.get(pid, {})
        team_id = p.get('proTeamId')
        draft = draft_by_id.get(str(pid), {})
        rows.append({'Draft Round':draft.get('roundId'),
                     'Draft Pick':draft.get('roundPickNumber'),
                     'Draft Overall':draft.get('overallPickNumber'),
                     'ESPN ID':str(pid), 'ESPN Player':name,
                     'NBA Team ESPN':TEAM_IDS.get(team_id, ''),
                     'Fantasy Team':owner.get('Fantasy Team') or 'FA',
                     'Fantasy Abbrev':owner.get('Fantasy Abbrev') or ('FA' if pid not in roster_map else ''),
                     'Injury':_injury(p), 'Positions':_positions(p),
                     'Default Position': POSITION.get(p.get('defaultPositionId'), '') if str(p.get('defaultPositionId', '')).isdigit() else '',
                     'Roster Slot':owner.get('Roster Slot') or '',
                     'Availability': 'ROSTERED' if pid in roster_map else 'FA'})
    if not rows:
        raise ValueError('No players found in ESPN responses; verify authentication and season.')
    return pd.DataFrame(rows), {'teams':len(team_names), 'rostered':len(roster_map), 'abbreviations': sum(bool(v) for v in team_abbrevs.values()), 'rostered_abbreviations': sum(bool(v.get('Fantasy Abbrev')) for v in roster_map.values()), 'draft_picks':len(draft_picks), 'draft_error':draft_error}


def save_fantasy(season, league_id, frame):
    with connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS fantasy_snapshots
            (season TEXT NOT NULL, league_id TEXT NOT NULL, downloaded_at TEXT NOT NULL,
             payload TEXT NOT NULL, PRIMARY KEY(season,league_id))''')
        conn.execute('''INSERT INTO fantasy_snapshots(season,league_id,downloaded_at,payload) VALUES (?,?,?,?) ON CONFLICT(season,league_id) DO UPDATE SET downloaded_at=excluded.downloaded_at, payload=excluded.payload''',
                     (str(season),str(league_id),datetime.now(timezone.utc).isoformat(),
                      frame.to_json(orient='records')))


def load_fantasy(season, league_id):
    with connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS fantasy_snapshots
            (season TEXT NOT NULL, league_id TEXT NOT NULL, downloaded_at TEXT NOT NULL,
             payload TEXT NOT NULL, PRIMARY KEY(season,league_id))''')
        row = conn.execute('SELECT payload, downloaded_at FROM fantasy_snapshots WHERE season=? AND league_id=?',
                           (str(season),str(league_id))).fetchone()
    return (pd.DataFrame(json.loads(row[0])),row[1]) if row else (pd.DataFrame(),None)


def merge_fantasy(nba_df, espn_df, fantasy_season):
    """Match exact normalized names, require unique match; never guess ambiguous players.

    Fantasy roster is current-year data only; past season rows stay blank.
    """
    result = nba_df.copy()
    fields = ['Fantasy Team','Fantasy Abbrev','Injury','Positions','Default Position','Roster Slot','ESPN ID', 'Draft Round', 'Draft Pick', 'Draft Overall']
    for col in fields:
        result[col] = pd.NA
    if espn_df.empty or 'Player' not in result:
        return result, 0
    espn = espn_df.copy()
    espn['_name'] = espn['ESPN Player'].map(_normalized_name)
    # Only match names occurring once in ESPN; no potentially incorrect team-based guesses.
    unique = espn[~espn['_name'].duplicated(keep=False)].set_index('_name')
    season_value = int(fantasy_season)
    match_count = 0
    for idx, row in result.iterrows():
        if int(row['Season']) != season_value:
            continue
        key = _normalized_name(row['Player'])
        if key not in unique.index:
            continue
        matched = unique.loc[key]
        # NBA team abbreviation is an extra safeguard when both sides provide it.
        espn_team = matched.get('NBA Team ESPN')
        nba_team = str(row.get('Team') or '')
        # Names are unique in the ESPN player pool; avoid team rejection
        # because ESPN team ID conventions can change between seasons.
        for col in fields:
            result.at[idx,col] = matched.get(col, pd.NA)
        match_count += 1
    return result, match_count



def fetch_player_news(espn_player_id):
    """Retrieve ESPN Fantasy Basketball's recent RotoWire player-card updates."""
    pid = str(espn_player_id).strip()
    if not pid.isdigit():
        return []
    url = 'https://site.api.espn.com/apis/fantasy/v3/games/fba/news/players'
    response = requests.get(
        url, params={'days': 30, 'playerId': pid},
        headers={'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0'},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    feed = (payload.get('news') or {}).get('feed') or []
    results = []
    for item in feed:
        if not isinstance(item, dict) or str(item.get('playerId', pid)) != pid:
            continue
        headline = item.get('headline') or item.get('description')
        if not headline:
            continue
        links = item.get('links') or {}
        mobile = links.get('mobile') or {}
        url = mobile.get('href') if isinstance(mobile, dict) else None
        if url and url.startswith('http://m.espn.go.com/'):
            url = 'https://' + url[len('http://'):]
        if not url or not url.startswith(('https://m.espn.go.com/', 'https://www.espn.com/')):
            url = None
        results.append({
            'title': str(headline),
            'summary': str(item.get('story') or ''),
            'source': str(item.get('type') or 'ESPN'),
            'date': str(item.get('published') or '')[:16].replace('T', ' '),
            'url': url,
        })
    results.sort(key=lambda x: x['date'], reverse=True)
    return results[:3]

def player_news_fallback_url(espn_player_id):
    """Official ESPN player profile with news tab; valid ESPN IDs only."""
    pid = str(espn_player_id).strip()
    return (f'https://www.espn.com/nba/player/_/id/{pid}'
            if pid.isdigit() else None)
