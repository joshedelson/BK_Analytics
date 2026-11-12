"""Dynasty trade matching with elite scarcity and roster-spot replacement cost.

All valuations are heuristics, not inferred manager preferences. Uses only existing
ESPN rosters and expected-first fantasy projections; optional daily crowd-ranking refresh.
"""
from functools import lru_cache
from itertools import combinations
import math
import re
import json
import time
from pathlib import Path
from urllib.request import Request, urlopen
import pandas as pd


_CROWD_URL = 'https://joshlloydfantasy.com/api/player-vote/rankings?format=points'
_CROWD_CACHE = Path(__file__).resolve().with_name('crowd_points_rankings_cache.json')
_CROWD_SEED = Path(__file__).resolve().with_name('crowd_points_rankings_seed.json')
_CROWD_REFRESH_SECONDS = 24 * 60 * 60


def _crowd_rankings():
    """Use fresh community rankings; on errors use the last valid local snapshot."""
    cache = None
    try:
        if _CROWD_CACHE.exists():
            cache = json.loads(_CROWD_CACHE.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        pass
    # A stale snapshot is useful as a fallback, not as a reason to stop refreshing.
    age = time.time() - _CROWD_CACHE.stat().st_mtime if _CROWD_CACHE.exists() else float('inf')
    if age >= _CROWD_REFRESH_SECONDS:
        try:
            request = Request(_CROWD_URL, headers={'User-Agent': 'Mozilla/5.0 NBA-Analytics/1.08', 'Accept': 'application/json'})
            with urlopen(request, timeout=4) as response:
                fresh = json.load(response)
            if (isinstance(fresh, dict) and fresh.get('format') == 'points'
                    and isinstance(fresh.get('rows'), list) and len(fresh['rows']) >= 20):
                cache = fresh
                try:
                    _CROWD_CACHE.write_text(json.dumps(fresh), encoding='utf-8')
                except OSError:
                    pass
        except Exception:
            pass  # Never break the trade calculator because a third-party API failed.
    if cache is None:
        try:
            cache = json.loads(_CROWD_SEED.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            return pd.DataFrame(columns=['Player ID', '_Crowd Rank', '_Crowd Confidence'])
    rows = cache.get('rows', []) if isinstance(cache, dict) else []
    if not rows:
        return pd.DataFrame(columns=['Player ID', '_Crowd Rank', '_Crowd Confidence'])
    frame = pd.DataFrame(rows)
    required = {'nba_player_id', 'rank'}
    if not required.issubset(frame.columns):
        return pd.DataFrame(columns=['Player ID', '_Crowd Rank', '_Crowd Confidence'])
    frame['Player ID'] = pd.to_numeric(frame['nba_player_id'], errors='coerce').astype('Int64').astype('string')
    frame['_Crowd Rank'] = pd.to_numeric(frame['rank'], errors='coerce')
    coverage = pd.to_numeric(frame.get('recent_vote_coverage', pd.Series(0, index=frame.index)), errors='coerce').fillna(0)
    frame['_Crowd Confidence'] = (coverage / 100.).clip(0., 1.)
    for field in ('insufficient_evidence', 'stale'):
        if field in frame:
            frame.loc[frame[field].fillna(False).astype(bool), '_Crowd Confidence'] *= .25
    if cache.get('calculation_stale'):
        frame['_Crowd Confidence'] *= .5
    return frame[['Player ID', '_Crowd Rank', '_Crowd Confidence']].dropna(subset=['Player ID', '_Crowd Rank']).drop_duplicates('Player ID')


def _crowd_reputation(row):
    """Modest market signal, separate from projected fantasy/dynasty value."""
    rank = pd.to_numeric(row.get('_Crowd Rank'), errors='coerce')
    own_rank = pd.to_numeric(row.get('_Value Rank'), errors='coerce')
    pool = pd.to_numeric(row.get('_Value Pool'), errors='coerce')
    confidence = pd.to_numeric(row.get('_Crowd Confidence'), errors='coerce')
    if pd.isna(rank) or pd.isna(own_rank) or pd.isna(pool) or pool < 2 or pd.isna(confidence):
        return 1.0
    # Compare ranks on a common 0..1 percentile scale. Crowd list may have
    # a different size than the fantasy league's rostered-player pool.
    crowd_pool = pd.to_numeric(row.get('_Crowd Pool'), errors='coerce')
    if pd.isna(crowd_pool) or crowd_pool < 2:
        return 1.0
    crowd_percentile = 1. - (float(rank) - 1.) / (float(crowd_pool) - 1.)
    own_percentile = 1. - (float(own_rank) - 1.) / (float(pool) - 1.)
    return max(.85, min(1.15, 1. + .30 * (crowd_percentile - own_percentile) * float(confidence)))


def _dynasty_multiplier(age):
    if pd.isna(age):
        return 1.0
    age = float(age)
    # Small, transparent age premium; these are adjustable assumptions.
    if age <= 22: return 1.12
    if age <= 25: return 1.09
    if age <= 28: return 1.04
    if age <= 31: return 1.00
    if age <= 34: return .93
    return .87


def _star_multiplier(row):
    """Smooth top-heavy scarcity premium for dynasty rostered assets.

    A single elite player occupies one roster slot and cannot be replicated by
    summing two mid-tier players. The curve strongly rewards top-10 assets but
    fades toward 1.0 for ordinary rostered players. It affects dynasty and
    perceived values consistently, not projected Fppg.
    """
    rank = pd.to_numeric(row.get('_Value Rank'), errors='coerce')
    if pd.isna(rank) or rank <= 0:
        return 1.0
    distance = float(rank) - 1.0
    return 1.0 + 1.35 * math.exp(-distance / 18.0) + .25 * math.exp(-distance / 65.0)


def _seller_value(row, league_teams=None):
    """Perceived asking price, including a small franchise-cornerstone premium."""
    value = _perceived(row, league_teams)
    team_rank = pd.to_numeric(row.get('_Team Rank'), errors='coerce')
    if pd.notna(team_rank):
        if team_rank <= 1: return value * 1.12
        if team_rank <= 2: return value * 1.07
    return value


def _player_value(row):
    return float(row['Projected Fppg']) * _dynasty_multiplier(row.get('AGE')) * _star_multiplier(row)


def _historical_rank_reputation(row):
    """Modest perceived-market adjustment from decayed historical Fppg ranks.

    Historical percentile is mapped to the current league's dynasty-value scale;
    only the perceived asking price changes, never the projection or true value.
    """
    current = pd.to_numeric(row.get('_Value Rank'), errors='coerce')
    pool = pd.to_numeric(row.get('_Value Pool'), errors='coerce')
    history = pd.to_numeric(row.get('_Historical Rank Percentile'), errors='coerce')
    confidence = pd.to_numeric(row.get('_Historical Rank Confidence'), errors='coerce')
    if pd.isna(current) or pd.isna(pool) or pool < 2 or pd.isna(history):
        return 1.0
    if pd.isna(confidence):
        confidence = 0.0
    current_percentile = 1.0 - (float(current) - 1) / (float(pool) - 1)
    # An older elite track record provides some reputation cushion, but it
    # cannot override a major deterioration in current projected production.
    delta = float(history) - current_percentile
    # GP reliability attenuates reputation, never projected fantasy points.
    return max(.88, min(1.20, 1.0 + .35 * delta * max(0., min(1., float(confidence)))))


def _perceived(row, league_teams=None):
    # Draft anchoring fades for players with more games; never replaces production.
    actual = _player_value(row)
    pick = pd.to_numeric(row.get('Draft Overall'), errors='coerce')
    draft_round = pd.to_numeric(row.get('Draft Round'), errors='coerce')
    if (pd.isna(pick) or pick <= 0) and pd.isna(draft_round):
        return actual * _historical_rank_reputation(row) * _crowd_reputation(row)
    if pd.isna(pick) or pick <= 0:
        # Draft round is available but not overall selection; retain a
        # round-based difficulty premium without fabricating a draft pick.
        pick = float('nan')
    # A bounded draft reputation proxy: not a historical valuation model.
    draft_proxy = max(10., min(55., 52. - 0.18 * (float(pick) - 1))) if pd.notna(pick) else actual
    gp = float(pd.to_numeric(row.get('GP'), errors='coerce') or 0)
    anchor = 0.20 * (15 / (15 + max(gp, 0)))
    perceived = actual * (1 - anchor) + draft_proxy * anchor
    perceived *= _historical_rank_reputation(row) * _crowd_reputation(row)
    # Dynasty league: first- and second-round fantasy draft selections carry
    # additional acquisition difficulty beyond their present projected output.
    # Prefer ESPN's explicit fantasy draft round. Only infer from overall pick
    # when the number of fantasy teams is known.
    draft_round = pd.to_numeric(row.get('Draft Round'), errors='coerce')
    if pd.isna(draft_round) and pd.notna(pick) and league_teams and league_teams > 1:
        draft_round = math.ceil(float(pick) / league_teams)
    if pd.notna(draft_round):
        if int(draft_round) == 1:
            premium = .15
        elif int(draft_round) == 2:
            premium = .09
        else:
            premium = 0.
        # Reputation gradually softens with new evidence, but early draft
        # capital remains a meaningful negotiating anchor in dynasty leagues.
        evidence_factor = .75 + .25 * (15. / (15. + max(gp, 0.)))
        perceived *= 1. + premium * evidence_factor
    return perceived


def _historical_rank_table(history, current_season, max_years=3):
    """Return recent Fppg rank percentiles by NBA player ID.

    Season-level ranks use players with >=15 games and positive Fppg.
    Recency weights are 55%/30%/15%; each season's contribution is also
    scaled by min(GP / 65, 1). Low-GP or missing history weakens the overall
    reputation effect. Current-season data is never used.
    """
    required = {'Season', 'Player ID', 'Fppg', 'GP'}
    if history is None or history.empty or not required.issubset(history.columns):
        return pd.DataFrame(columns=['Player ID', '_Historical Rank Percentile', '_Historical Rank Confidence'])
    h = history[list(required)].copy()
    h['Season'] = pd.to_numeric(h['Season'].astype(str).str.extract(r'(\d{4})')[0], errors='coerce')
    h['Player ID'] = h['Player ID'].astype('string').str.replace(r'\.0$', '', regex=True)
    h['Fppg'] = pd.to_numeric(h['Fppg'], errors='coerce')
    h['GP'] = pd.to_numeric(h['GP'], errors='coerce')
    season = int(str(current_season)[:4])
    h = h[h['Season'].between(season - max_years, season - 1) & h['GP'].ge(15) & h['Fppg'].gt(0)]
    if h.empty:
        return pd.DataFrame(columns=['Player ID', '_Historical Rank Percentile', '_Historical Rank Confidence'])
    h = h.sort_values('GP').drop_duplicates(['Player ID', 'Season'], keep='last')
    h['_Rank Percentile'] = h.groupby('Season')['Fppg'].rank(pct=True, ascending=True)
    # Recent seasons matter more; missing seasons are not treated as poor ranks.
    weights = {season - 1: .55, season - 2: .30, season - 3: .15}
    h['_Recency Weight'] = h['Season'].map(weights)
    h['_GP Reliability'] = (h['GP'] / 65.).clip(lower=0., upper=1.)
    h['_Weight'] = h['_Recency Weight'] * h['_GP Reliability']
    h['_Weighted Rank'] = h['_Rank Percentile'] * h['_Weight']
    agg = h.groupby('Player ID', as_index=False).agg(
        weighted=('_Weighted Rank', 'sum'),
        weight=('_Weight', 'sum'),
        available_recency=('_Recency Weight', 'sum'))
    agg['_Historical Rank Percentile'] = agg['weighted'] / agg['weight'].where(agg['weight'].gt(0))
    # Preserve established older seasons when the latest year was missed,
    # but reduce the influence of sparse historical evidence.
    agg['_Historical Rank Confidence'] = (
        agg['weight'] / agg['available_recency'].where(agg['available_recency'].gt(0))
    ).clip(0., 1.)
    return agg[['Player ID', '_Historical Rank Percentile', '_Historical Rank Confidence']]


def _normalize(projections, fantasy, historical_stats=None, current_season=None):
    required = {'Player ID', 'Player', 'Fantasy Team', 'Fppg', 'Projected Fppg'}
    if projections is None or projections.empty or not required.issubset(projections.columns):
        return pd.DataFrame()
    df = projections.copy()
    df['Player ID'] = df['Player ID'].astype('string').str.replace(r'\.0$', '', regex=True)
    df['Fantasy Team'] = df['Fantasy Team'].fillna('FA').astype(str).str.strip()
    for c in ('Fppg', 'Projected Fppg', 'AGE', 'GP', 'Draft Overall', 'Draft Round'):
        if c in df: df[c] = pd.to_numeric(df[c], errors='coerce')
    if 'Positions' not in df: df['Positions'] = ''
    if fantasy is not None and not fantasy.empty and 'ESPN Player' in fantasy:
        meta = fantasy.copy()
        meta['_name'] = meta['ESPN Player'].fillna('').astype(str).str.casefold().str.replace(r'[^a-z0-9]', '', regex=True)
        meta = meta.drop_duplicates('_name')
        df['_name'] = df['Player'].fillna('').astype(str).str.casefold().str.replace(r'[^a-z0-9]', '', regex=True)
        fields = [c for c in ('Positions', 'Draft Overall', 'Draft Round', 'Injury') if c in meta]
        if fields:
            df = df.merge(meta[['_name', *fields]], on='_name', how='left', suffixes=('', '_espn'))
            for c in fields:
                ec = c+'_espn'
                if ec in df:
                    if c == 'Positions':
                        df[c] = df[ec].where(df[ec].fillna('').astype(str).str.strip().ne(''), df[c])
                    else:
                        df[c] = df[ec].combine_first(df[c]) if c in df else df[ec]
                    df = df.drop(columns=ec)
        df = df.drop(columns='_name')
    for c in ('AGE', 'GP', 'Draft Overall', 'Draft Round', 'Injury'):
        if c not in df: df[c] = float('nan') if c != 'Injury' else ''
    df = df.dropna(subset=['Player ID', 'Fppg', 'Projected Fppg'])
    df = df[df['Fppg'].ge(0) & df['Projected Fppg'].ge(0)]
    df = df.drop_duplicates('Player ID').copy()
    # Value players against assets actually rostered in this fantasy league.
    # Free agents should not dilute elite rank or make an average player elite.
    ranking = df['Projected Fppg'] * df['AGE'].apply(_dynasty_multiplier)
    rostered = ~df['Fantasy Team'].isin(['FA', ''])
    rostered_ranks = ranking.loc[rostered].rank(method='min', ascending=False)
    df['_Value Rank'] = float('nan')
    df.loc[rostered, '_Value Rank'] = rostered_ranks
    df['_Value Pool'] = int(rostered.sum())
    if historical_stats is not None and current_season is not None:
        historical_ranks = _historical_rank_table(historical_stats, current_season)
        if not historical_ranks.empty:
            df = df.merge(historical_ranks, on='Player ID', how='left', validate='one_to_one')
    # Crowd data is a live, rest-of-season *redraft* sentiment signal, not a
    # dynasty projection. It only changes perceived trade value.
    crowd = _crowd_rankings()
    if not crowd.empty:
        df = df.merge(crowd, on='Player ID', how='left', validate='one_to_one')
        df['_Crowd Pool'] = int(crowd['_Crowd Rank'].max())
    # The historical-rank and crowd merges can rebuild the DataFrame index.
    # Recompute the roster mask against the final DataFrame to avoid
    # pandas.errors.IndexingError from unalignable boolean Series.
    rostered = ~df['Fantasy Team'].isin(['FA', ''])
    df['_Team Rank'] = float('nan')
    df.loc[rostered, '_Team Rank'] = (
        df.loc[rostered].groupby('Fantasy Team')['_Value Rank'].rank(method='min')
    )
    return df


def _replacement_value(df):
    """Waiver opportunity in the same dynasty-value units as traded assets.

    Use the upper available pool rather than a single outlier; don't fabricate
    waiver value when no free agents are present.
    """
    available = df[df['Fantasy Team'].isin(['FA', ''])]
    if available.empty:
        return 0.0
    values = available.apply(_player_value, axis=1)
    values = values[values.notna() & (values > 0)]
    if values.empty:
        return 0.0
    return float(values.nlargest(min(5, len(values))).median())


def _trade_roster_adjustments(roster, outgoing, incoming, waiver_value):
    """Return trade-caused dynasty adjustment and optional drop, ignoring IR.

    All teams are assumed to have 18 occupied regular slots, regardless of
    actual ESPN roster counts. Equal-sized swaps never force drops. An extra
    incoming player displaces the weakest non-traded asset; the *net* penalty
    is its value above waiver replacement, not its full value. When sending
    more than receiving, the open slot can be filled from waivers.
    """
    difference = len(incoming) - len(outgoing)
    if difference == 0:
        return 0.0, []
    if difference < 0:
        return -difference * waiver_value, []
    outgoing_ids = {str(p['Player ID']) for p in outgoing}
    eligible = [p for p in roster if str(p['Player ID']) not in outgoing_ids]
    eligible.sort(key=_player_value)
    dropped = eligible[:difference]
    # Never invent a forced cut when there is no known eligible player.
    cost = sum(max(0., _player_value(p) - waiver_value) for p in dropped)
    return -cost, dropped


def _two_for_one_consolidation_discount(two_players, single_player, value_fn):
    """Non-additive bundle discount for the side receiving two for one.

    Two roster players are not fully interchangeable with a single elite asset.
    Discount only the LESS valuable piece, increasingly when the single player
    is a scarce top-ranked talent. This is a trade-comparison adjustment, not
    a change to player projections or standalone dynasty values.
    """
    if len(two_players) != 2 or single_player is None:
        return 0.0
    values = sorted((max(0.0, float(value_fn(p))) for p in two_players))
    rank = pd.to_numeric(single_player.get('_Value Rank'), errors='coerce')
    if pd.isna(rank) or rank <= 0:
        rank = 150.0
    # Top-5: ~70% discount on the second piece; rank 30: ~45%;
    # rank 100+: ~20%. Do not double-count the full weaker player.
    fraction = .18 + .55 * math.exp(-(float(rank) - 1.0) / 34.0)
    return values[0] * fraction


def suggest_trades(projections, focus_team, outgoing_ids, fantasy=None, fairness=.15, max_results=10, historical_stats=None, current_season=None):
    """Suggest 1-for-1, 2-for-1 and 2-for-2 trades without lineup optimization.

    Return up to ten league-wide offers, ranked by your dynasty gain, considering roster spots, elite scarcity,
    and a non-additive two-for-one consolidation discount. Perceived fairness
    uses the same structure with market-facing player values.
    """
    columns = ['Fantasy Team', 'You Give', 'You Get', 'Give IDs', 'Get IDs',
               'Your Dynasty Gain', 'Their Dynasty Gain', 'Perceived Difference',
               'Roster Note', 'Score', 'Trade Fit']
    df = _normalize(projections, fantasy, historical_stats=historical_stats, current_season=current_season)
    if df.empty or not outgoing_ids:
        return pd.DataFrame(columns=columns)
    ids = set(map(str, outgoing_ids))
    yours = df[df['Fantasy Team'].eq(focus_team)].to_dict('records')
    given = [p for p in yours if str(p['Player ID']) in ids]
    if len(given) != len(ids) or len(given) not in (1, 2):
        return pd.DataFrame(columns=columns)
    give_ids = {str(p['Player ID']) for p in given}
    league_teams = df.loc[~df['Fantasy Team'].isin(['FA', '']), 'Fantasy Team'].nunique()
    give_perceived = sum(_seller_value(p, league_teams) for p in given)
    replacement = _replacement_value(df)
    qualifying_all = []
    fallback_all = []
    opponents = df[~df['Fantasy Team'].isin(['FA', '', focus_team])]
    for team, group in opponents.groupby('Fantasy Team', sort=True):
        theirs = group.to_dict('records')
        for count in (1, 2):
            for received in combinations(theirs, count):
                recv_ids = {str(p['Player ID']) for p in received}
                receive_perceived = sum(_seller_value(p, league_teams) for p in received)
                # Trade deltas depend only on the exchanged players, not on
                # ESPN's reported roster size (which can include IR slots).
                your_roster_effect, your_drops = _trade_roster_adjustments(
                    yours, given, received, replacement)
                their_roster_effect, their_drops = _trade_roster_adjustments(
                    theirs, received, given, replacement)
                exchange_delta = sum(_player_value(p) for p in received) - sum(_player_value(p) for p in given)
                # A two-player package is worth less than the simple sum when
                # acquiring a scarce single asset. Apply the discount to the
                # side receiving TWO, regardless of which team that is.
                your_bundle_cost = (_two_for_one_consolidation_discount(received, given[0], _player_value)
                                    if len(received) == 2 and len(given) == 1 else 0.0)
                their_bundle_cost = (_two_for_one_consolidation_discount(given, received[0], _player_value)
                                     if len(given) == 2 and len(received) == 1 else 0.0)
                your_gain = exchange_delta + your_roster_effect - your_bundle_cost
                their_gain = -exchange_delta + their_roster_effect - their_bundle_cost
                # Perceived fairness is a separate market-facing calculation.
                # Use the same consolidation logic with perceived asset values,
                # rather than assuming two lesser players equal one star.
                your_market_cost = (_two_for_one_consolidation_discount(
                    received, given[0], lambda p: _seller_value(p, league_teams))
                    if len(received) == 2 and len(given) == 1 else 0.0)
                their_market_cost = (_two_for_one_consolidation_discount(
                    given, received[0], lambda p: _seller_value(p, league_teams))
                    if len(given) == 2 and len(received) == 1 else 0.0)
                perceived_diff = ((receive_perceived - give_perceived)
                                  + your_roster_effect - your_market_cost + their_market_cost)

                # A fair-looking offer must be near the seller's asking value.
                # Never use a fallback to advertise wildly uneven superstar deals.
                tolerance = max(4.0, give_perceived * min(fairness, .12))
                fallback_tolerance = max(6.0, give_perceived * .20)
                # A manager should not surrender a cornerstone for a substantial
                # dynasty-value loss, regardless of apparent draft reputation.
                max_their_loss = max(5.0, give_perceived * .08)
                # Keep the closest offers as a transparent fallback. A strict
                # fairness gate can otherwise hide every player in a league.
                score = your_gain - .15 * abs(perceived_diff) + .10 * their_gain
                note = (f"You drop: {', '.join(p['Player'] for p in your_drops)}. " if your_drops else '') + (f"They drop: {', '.join(p['Player'] for p in their_drops)}." if their_drops else '')
                item = {'Fantasy Team': team, 'You Give': ' + '.join(p['Player'] for p in given),
                    'You Get': ' + '.join(p['Player'] for p in received),
                    'Give IDs': tuple(str(p['Player ID']) for p in given),
                    'Get IDs': tuple(str(p['Player ID']) for p in received),
                    'Your Dynasty Gain': your_gain, 'Their Dynasty Gain': their_gain,
                    'Perceived Difference': perceived_diff, 'Roster Note': note.strip(),
                    'Score': score}
                if abs(perceived_diff) <= tolerance and your_gain > 0 and their_gain >= -max_their_loss:
                    qualifying_all.append({**item, 'Trade Fit': 'Qualifying'})
                elif your_gain > 0 and abs(perceived_diff) <= fallback_tolerance and their_gain >= -max_their_loss:
                    # Ranking by fairness gap first avoids advertising wildly
                    # lopsided offers merely because their projected gain is high.
                    item['Trade Fit'] = 'Closest alternative — may be difficult to accept'
                    fallback_all.append(item)
    # Rank the entire league's acceptable trades by YOUR dynasty value gain.
    # Never force one result per opponent or display negative-value exploratory offers.
    # A slightly wider market-fairness fallback is used only if fewer than ten
    # strict offers exist, and it still must help you without badly hurting them.
    ordered = sorted(qualifying_all, key=lambda p: (
        -p['Your Dynasty Gain'], abs(p['Perceived Difference']), -p['Their Dynasty Gain']))
    if len(ordered) < max_results:
        fallback_all.sort(key=lambda p: (
            -p['Your Dynasty Gain'], abs(p['Perceived Difference']), -p['Their Dynasty Gain']))
        ordered.extend(fallback_all[:max(0, max_results - len(ordered))])
    return (pd.DataFrame(ordered[:max_results], columns=columns).reset_index(drop=True)
            if ordered else pd.DataFrame(columns=columns))
