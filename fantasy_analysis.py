"""Fantasy analysis built from saved ESPN metadata and NBA xFppg; no credentials stored."""
from datetime import date, timedelta
import pandas as pd
import requests


def fantasy_analysis_table(nba, fantasy, season):
    """Join with existing conservative matching and preserve only current-season rows."""
    from espn_fantasy import merge_fantasy
    if nba.empty or fantasy.empty:
        return pd.DataFrame()
    current = nba.loc[pd.to_numeric(nba['Season'], errors='coerce') == int(season)].copy()
    if current.empty:
        return pd.DataFrame()
    joined, _ = merge_fantasy(current, fantasy, season)
    joined = joined.loc[joined['ESPN ID'].notna()].copy()
    if joined.empty:
        return pd.DataFrame()
    joined['Fantasy Team'] = joined['Fantasy Team'].fillna('FA').replace('', 'FA')
    stat_cols = ('Fpts', 'xFpts', 'Fppg', 'xFppg', '+-Fppg', 'GP', 'MIN',
                 'PTS', 'xPTS', 'AST', 'xAST', 'REB', 'xREB', 'STL', 'xSTL',
                 'BLK', 'xBLK', 'TOV', 'xTO')
    for col in stat_cols:
        if col in joined:
            joined[col] = pd.to_numeric(joined[col], errors='coerce')
    cols = ['Player', 'Team', 'Positions', 'Default Position', 'Injury', 'Fantasy Team', 'GP', 'MIN',
            *stat_cols, 'Player ID', 'ESPN ID']
    return joined[[c for c in dict.fromkeys(cols) if c in joined]].sort_values(
        'xFppg', ascending=False, na_position='last')


def replacements(table, own_team, available_only=True):
    """For each rostered player, find highest-xFppg position-compatible available player."""
    if table.empty or 'xFppg' not in table or 'Positions' not in table:
        return pd.DataFrame()
    mine = table.loc[table['Fantasy Team'] == own_team].copy()
    others = table.loc[table['Fantasy Team'].eq('FA') if available_only else table['Fantasy Team'].ne(own_team)].copy()
    others = others.dropna(subset=['xFppg'])
    results = []
    def positions(s):
        return {x.strip() for x in str(s or '').split(',') if x.strip() in ('PG','SG','SF','PF','C')}
    for _, player in mine.iterrows():
        if pd.isna(player.get('xFppg')):
            continue
        eligible = positions(player.get('Positions'))
        if not eligible:
            continue
        possible = others.loc[others['Positions'].apply(lambda x: bool(eligible & positions(x)))]
        if possible.empty:
            continue
        best = possible.sort_values('xFppg', ascending=False).iloc[0]
        results.append({'Your Player': player['Player'], 'Positions': player['Positions'],
                        'Your xFppg': player['xFppg'], 'Replacement': best['Player'],
                        'Replacement Injury': best.get('Injury',''),
                        'Replacement xFppg': best['xFppg'],
                        'Potential Gain': best['xFppg'] - player['xFppg']})
    return pd.DataFrame(results).sort_values('Potential Gain', ascending=False) if results else pd.DataFrame()


ESPN_SCOREBOARD = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard'


def week_bounds(today=None):
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    return monday, monday + timedelta(days=6)


def nba_week_games(monday=None, timeout=20):
    """Public ESPN NBA scoreboard, one day at a time to avoid scoreboard date-range limits.

    Returns abbreviation -> number of NBA games scheduled Monday through Sunday.
    Never claims this equals playable fantasy starts (lineup and injuries can differ).
    """
    monday = monday or week_bounds()[0]
    counts = {}
    with requests.Session() as session:
        for day in (monday + timedelta(days=i) for i in range(7)):
            response = session.get(ESPN_SCOREBOARD, params={'dates':day.strftime('%Y%m%d'), 'limit':100}, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            for event in data.get('events', []):
                for comp in event.get('competitions', []):
                    for entry in comp.get('competitors', []):
                        team = entry.get('team') or {}
                        abbr = team.get('abbreviation')
                        if abbr:
                            abbr = {'GS':'GSW','NY':'NYK','SA':'SAS','NO':'NOP','UTAH':'UTA'}.get(abbr, abbr)
                            counts[abbr] = counts.get(abbr,0)+1
    return counts


def weekly_projection(table, game_counts):
    out = table.copy()
    out['Games This Week'] = out['Team'].map(game_counts)
    out['Projected Weekly Points'] = pd.to_numeric(out['xFppg'], errors='coerce') * out['Games This Week']
    return out



# Season-level observational forecast. All operations are vectorized; no network calls.
# Historical cohort aging is estimated from adjacent player seasons using past
# seasons only. This is a heuristic until rolling game-log backtests are available.
def positive_regression_table(nba, fantasy, season):
    import numpy as np
    if nba is None or nba.empty or not {'Season','Player ID','Fppg','GP'}.issubset(nba):
        return pd.DataFrame()
    year = int(season)
    fields = ['Season','Player ID','Player','Team','AGE','GP','MIN','Fppg','xFppg',
              'USG%','Pot_AST','REB Chances','Touches','Deflections',
              'FG%','3PT%','FT%','xFG%','x3P%','xFT%','FGA','3PA','FTA','FGM','3PM','FTM']
    data = nba[[c for c in fields if c in nba]].copy()
    for col in fields:
        if col not in data:
            data[col] = pd.NA
    data['Season'] = pd.to_numeric(data['Season'],errors='coerce')
    data['Player ID'] = data['Player ID'].astype('string')
    data = data.loc[data['Season'].le(year) & data['Season'].ge(2016)].copy()
    for col in ['AGE','GP','MIN','Fppg','xFppg','USG%','Pot_AST','REB Chances','Touches','Deflections',
              'FG%','3PT%','FT%','xFG%','x3P%','xFT%','FGA','3PA','FTA','FGM','3PM','FTM']:
        data[col] = pd.to_numeric(data[col], errors='coerce')
    data = data.sort_values(['Player ID','Season','GP']).drop_duplicates(['Player ID','Season'],keep='last')
    # Observed NBA experience. Data begins in 2016-17, so first appearances in
    # that season are left-censored and must not be labeled as rookies.
    first = data.groupby('Player ID')['Season'].min()
    current_years = (year - first + 1).where(first.gt(2016))
    data['MPG'] = data['MIN'].div(data['GP'].where(data['GP'].gt(0)))
    data['FPM'] = data['Fppg'].div(data['MPG'].where(data['MPG'].gt(0)))
    data.loc[~data['FPM'].between(0,5), 'FPM'] = np.nan
    # Empirical age curve: robust median within each age, from past season pairs.
    past = data.loc[data['Season'].lt(year) & data['GP'].ge(15) & data['MPG'].ge(10)].copy()
    past = past.sort_values(['Player ID','Season'])
    prior = past.groupby('Player ID', sort=False)
    consecutive = past['Season'].sub(prior['Season'].shift()).eq(1)
    prior_fpm = prior['FPM'].shift()
    growth = np.log(past['FPM'].div(prior_fpm))
    valid_growth = consecutive & prior_fpm.gt(.15) & past['FPM'].gt(.15) & growth.between(-.35,.35)
    age_growth = growth.loc[valid_growth].groupby(past.loc[valid_growth,'AGE'].round()).agg(['median','count'])
    # Shrink sparse age cohorts toward no change, cap annual adjustment to 5%.
    curve = (age_growth['median'] * age_growth['count'].div(age_growth['count']+60)).clip(-.05,.05).to_dict()
    current = data.loc[data['Season'].eq(year)].copy()
    if current.empty:
        return pd.DataFrame()
    history = data.loc[data['Season'].lt(year) & data['GP'].ge(10) & data['FPM'].notna()].copy()
    history['Lag'] = year-history['Season']
    # Exponential recency, games played reliability; older years still inform trend.
    history['Weight'] = (.65 ** (history['Lag']-1)) * history['GP'].clip(upper=70).div(70)
    history['Age Step'] = history['AGE'] + history['Lag']
    # Experience cohorts use completed historical seasons only. Estimate the
    # year-1->2 and year-2->3 change in FPM, not total Fppg (minutes modeled
    # separately). Exclude left-censored 2016-17 debuts and weak samples.
    cohort = past.copy()
    cohort['First Season'] = cohort['Player ID'].map(first)
    cohort['Experience'] = cohort['Season'] - cohort['First Season'] + 1
    cohort_prior = cohort.groupby('Player ID', sort=False)
    cohort_growth = np.log(cohort['FPM'].div(cohort_prior['FPM'].shift()))
    cohort_valid = (cohort['First Season'].gt(2016) &
                    cohort['Season'].sub(cohort_prior['Season'].shift()).eq(1) &
                    cohort['Experience'].isin([2,3]) &
                    cohort_prior['FPM'].shift().gt(.15) & cohort['FPM'].gt(.15) &
                    cohort_growth.between(-.35,.35))
    cohort_stats = cohort_growth.loc[cohort_valid].groupby(cohort.loc[cohort_valid,'Experience']).agg(['median','count'])
    # Shrink noisy cohorts toward zero; cap any one annual effect to 8%.
    cohort_curve = (cohort_stats['median'] * cohort_stats['count'].div(cohort_stats['count']+100)).clip(-.08,.08).to_dict()
    # Shrunk empirical age effects across intervening ages, with a conservative cap.
    def age_factor(row):
        # For early-career seasons, use measured experience-cohort development
        # instead of also applying the age curve (avoids double counting).
        player_first = first.get(row['Player ID'], np.nan)
        if pd.notna(player_first) and player_first > 2016:
            start_exp = int(row['Season'] - player_first + 1)
            end_exp = int(year - player_first + 1)
            if end_exp in (2,3) and start_exp >= 1:
                return float(np.exp(sum(cohort_curve.get(e, 0) for e in range(start_exp+1,end_exp+1))))
        if pd.isna(row['AGE']): return 1.0
        return float(np.exp(sum(curve.get(float(round(row['AGE']+k)),0) for k in range(1,int(row['Lag'])+1))))
    history['Aging Factor'] = history[['Player ID','Season','AGE','Lag']].apply(age_factor,axis=1)
    history['Adjusted FPM'] = history['FPM'] * history['Aging Factor']
    history['Weighted FPM'] = history['Adjusted FPM']*history['Weight']
    sums = history.groupby('Player ID').agg(Weight=('Weight','sum'), Weighted=('Weighted FPM','sum'),
                                            Historical_Seasons=('Season','nunique'), Historical_GP=('GP','sum'))
    sums['Historical FPM'] = sums['Weighted']/sums['Weight'].where(sums['Weight'].gt(0))
    current = current.join(sums[['Historical FPM','Historical_Seasons','Historical_GP']],on='Player ID')
    current['Historical Seasons'] = current['Historical_Seasons'].fillna(0).astype(int)
    current['NBA Experience'] = current['Player ID'].map(current_years).astype('Int64')
    current['Experience Group'] = current['NBA Experience'].map(
        lambda v: ('Unknown' if pd.isna(v) else
                   'Rookie' if v == 1 else '2nd year' if v == 2 else
                   '3rd year' if v == 3 else '4th+ year'))
    # Historical shooting baselines: aggregate attempts/makes rather than average
    # percentages. Weight recent seasons and shrink low-volume historical samples.
    shooting_history = data.loc[data['Season'].lt(year)].copy()
    shooting_history['Lag'] = year - shooting_history['Season']
    shooting_history['Weight'] = (.65 ** (shooting_history['Lag'] - 1))
    shooting_history['Weight'] *= shooting_history['GP'].clip(lower=0, upper=70).div(70)
    # Current and historical shooting percentages may be stored as 0-1 or 0-100.
    def pct(values):
        values = pd.to_numeric(values, errors='coerce')
        return values.where(values.le(1.0), values.div(100)).where(lambda x: x.between(0,1))
    for name, makes, attempts, pct_name in [('FG','FGM','FGA','FG%'),
                                             ('3P','3PM','3PA','3PT%'),
                                             ('FT','FTM','FTA','FT%')]:
        # FGM/3PM/FTM and attempts are season totals in the source dataset.
        # Derive a rate from recorded makes/attempts, falling back to percentage.
        shot_attempts = shooting_history[attempts].where(shooting_history[attempts].gt(0))
        hist_rate = (shooting_history[makes] / shot_attempts).where(
            shooting_history[makes].notna() & shot_attempts.notna(),
            pct(shooting_history[pct_name]))
        weight_attempts = shot_attempts.fillna(0) * shooting_history['Weight']
        shooting_history[f'{name}_made_weight'] = hist_rate.fillna(0) * weight_attempts
        shooting_history[f'{name}_attempt_weight'] = weight_attempts.where(hist_rate.notna(),0)
    grouped_shooting = shooting_history.groupby('Player ID')[[
        f'{n}_{part}_weight' for n in ('FG','3P','FT') for part in ('made','attempt')]].sum()
    for name in ('FG','3P','FT'):
        made = grouped_shooting[f'{name}_made_weight']
        attempts = grouped_shooting[f'{name}_attempt_weight']
        grouped_shooting[f'Hist {name}%'] = made.div(attempts.where(attempts.gt(0)))
        grouped_shooting[f'Hist {name} Att'] = attempts
    current = current.join(grouped_shooting[[f'Hist {n}{suffix}' for n in ('FG','3P','FT')
                                            for suffix in ('%',' Att')]], on='Player ID')
    # Role-aware minutes: a short, injury-disrupted previous season should not
    # outweigh multiple well-sampled seasons, nor should a long-gone role be
    # automatically restored. Current MPG gets the majority of the weight.
    role_history = data.loc[data['Season'].between(year-3, year-1)].copy()
    role_history['Lag'] = year-role_history['Season']
    role_history['Role Weight'] = (.75 ** (role_history['Lag']-1)) * (
        role_history['GP'].clip(lower=0, upper=70).div(70) ** 1.5)
    role_history.loc[role_history['GP'].lt(8), 'Role Weight'] = 0
    def role_baseline(field):
        valid = role_history.loc[role_history[field].notna() & role_history['Role Weight'].gt(0)].copy()
        if valid.empty:
            return pd.Series(dtype=float)
        valid['Weighted'] = valid[field]*valid['Role Weight']
        totals = valid.groupby('Player ID')[['Weighted','Role Weight']].sum()
        return totals['Weighted'].div(totals['Role Weight'].where(totals['Role Weight'].gt(0)))
    current['Prior MPG'] = current['Player ID'].map(role_baseline('MPG'))
    current['Prior USG%'] = current['Player ID'].map(role_baseline('USG%'))
    # Data-calibrated next-season role forecasts. Fit on completed-season pairs
    # and checked on held-out 2024-25/2025-26 outcomes. Use only the immediately
    # preceding season; older seasons remain in the weighted fallback baseline.
    from role_calibration import predict_next_role
    last = role_history.loc[role_history['Lag'].eq(1)].set_index('Player ID')
    if not last.empty:
        prior_gp = current['Player ID'].map(last['GP'])
        prior_age = current['Player ID'].map(last['AGE'])
        last_mpg = current['Player ID'].map(last['MPG'])
        last_usage = current['Player ID'].map(last['USG%'])
        calibrated_mpg = predict_next_role(last_mpg, prior_gp, prior_age, 'MPG')
        calibrated_usage = predict_next_role(last_usage, prior_gp, prior_age, 'USG%')
        # A short previous season should not erase a more reliable multi-year
        # baseline. The learned model is a correction, not an automatic override.
        calibration_share = (.65 * prior_gp.div(prior_gp+20)).clip(0,.65)
        for col, predicted in [('Prior MPG', calibrated_mpg),
                               ('Prior USG%', calibrated_usage)]:
            baseline = current[col]
            valid = baseline.notna() & predicted.notna()
            current[col] = baseline.where(~valid,
                baseline*(1-calibration_share) + predicted*calibration_share)
            current[col] = current[col].combine_first(predicted)
        current['Calibrated Prior MPG'] = calibrated_mpg
        current['Calibrated Prior USG%'] = calibrated_usage
    else:
        current['Calibrated Prior MPG'] = np.nan
        current['Calibrated Prior USG%'] = np.nan
    gp = current['GP'].fillna(0).clip(lower=0)
    prior_mpg = current['Prior MPG']
    current_mpg = current['MPG']
    # After ten games, current role carries >=70% of the minutes forecast.
    # This guards against returning established veterans to outdated roles.
    minute_weight = (0.55 + 0.40*gp.div(gp+12)).clip(upper=.95)
    current['Projected MPG'] = (
        current_mpg*minute_weight + prior_mpg*(1-minute_weight)
    ).where(current_mpg.notna() & prior_mpg.notna(),
            current_mpg.combine_first(prior_mpg)).clip(0,40)
    current['Usage Change'] = current['USG%']-current['Prior USG%']
    current['Opportunity Adjustment %'] = 0.0
    current['Opportunity Trend %'] = np.nan
    historical_fppg = current['Historical FPM']*current['Projected MPG']
    current['Historical Fppg'] = historical_fppg
    current['Expected Gap'] = current['xFppg']-current['Fppg']
    current['Historical Gap'] = current['Historical Fppg']-current['Fppg']
    actual_rate = current['FPM']
    expected_rate = current['xFppg'].div(current_mpg.where(current_mpg.gt(0)))
    baseline_rate = current['Historical FPM']
    # Shrink the observed usage change toward the previous role; only a small
    # capped rate adjustment, and only where both usage readings exist.
    # This avoids adding overlapping passing/touches/rebounding bonuses.
    usage_delta = current['Usage Change']
    usage_ratio = (usage_delta / current['Prior USG%'].where(current['Prior USG%'].gt(5))).clip(-.5,.5)
    usage_effect = (usage_ratio * .12 * gp.div(gp+25)).clip(-.04,.04).fillna(0)
    # Actual adjustment is applied only to the historical portion below.
    current['Opportunity Adjustment %'] = 100*usage_effect
    current['Opportunity Trend %'] = current['Usage Change']
    # xFppg already includes xFG%, x3P%, and xFT%. Do not add their
    # expected-vs-actual gaps again. Instead, adjust xFppg *slightly* toward
    # historically sustainable shooting rates, with attempt-based shrinkage.
    # Coefficients follow league fantasy scoring: FGM contributes 4 points
    # (2 for make + 2 from PTS), 3PM adds 2 more, FTM contributes 2.
    # xFT% is itself history-shrunk upstream, so its extra adjustment is small.
    shooting_correction = pd.Series(0.0, index=current.index)
    shooting_signals = []
    for name, xcol, actual_col, att_col, points, prior_strength in (
        ('FG','xFG%','FG%','FGA',4.0,150.0),
        ('3P','x3P%','3PT%','3PA',2.0,100.0),
        ('FT','xFT%','FT%','FTA',2.0,250.0),
    ):
        expected_pct = pct(current[xcol])
        actual_pct = pct(current[actual_col])
        history_pct = current[f'Hist {name}%']
        history_attempts = current[f'Hist {name} Att'].fillna(0)
        # A historical prior only has influence if we have attempts in that
        # category; the influence is capped and decreases with current attempts.
        attempts_pg = current[att_col].div(gp.where(gp.gt(0)))
        attempts_now = current[att_col].fillna(0).clip(lower=0)
        hist_reliability = history_attempts.div(history_attempts+prior_strength)
        current_reliability = attempts_now.div(attempts_now+prior_strength)
        blend = (0.30 * hist_reliability * (1-current_reliability)).clip(0,.30)
        # FT expectations already use historical Bayesian smoothing.
        if name == 'FT':
            blend *= .35
        correction = ((history_pct-expected_pct) * blend * attempts_pg * points)
        correction = correction.where(expected_pct.notna() & history_pct.notna() &
                                      attempts_pg.notna(),0).fillna(0)
        shooting_correction += correction
        current[f'Hist {name}%'] = history_pct
        current[f'{name} Expected Gap'] = (expected_pct-actual_pct)*100
        current[f'{name} Historical Gap'] = (history_pct-actual_pct)*100
        shooting_signals.append(expected_pct.notna())
    # Prevent the historical shooting calibration from dominating xFppg.
    shooting_correction = shooting_correction.clip(-2.0,2.0)
    current['Shooting Calibration'] = shooting_correction
    current['Shooting Coverage'] = sum(s.astype(int) for s in shooting_signals)
    calibrated_xfppg = current['xFppg'] + shooting_correction
    expected_rate = calibrated_xfppg.div(current_mpg.where(current_mpg.gt(0)))
    # Expected production dominates actual production (85%/15%). The career
    # baseline is a minority prior and fades as current games accumulate.
    current_rate = expected_rate.fillna(actual_rate)*.85 + actual_rate*.15
    current_rate = current_rate.where(expected_rate.notna(),actual_rate)
    hist_strength = current['Historical_GP'].fillna(0).div(current['Historical_GP'].fillna(0)+70)
    history_share = ((1-gp.div(gp+18))*hist_strength).where(baseline_rate.notna(),0).clip(0,.35)
    # When current minutes/usage are materially below an established historical
    # role, downweight old points-per-minute rather than assuming a role rebound.
    minute_role_ratio = current_mpg.div(prior_mpg.where(prior_mpg.gt(5)))
    usage_role_ratio = current['USG%'].div(current['Prior USG%'].where(current['Prior USG%'].gt(0)))
    minute_role_factor = minute_role_ratio.clip(lower=.35,upper=1).fillna(1)
    usage_role_factor = usage_role_ratio.clip(lower=.35,upper=1).fillna(1)
    role_evidence = gp.div(gp+10)
    role_discount = (1-role_evidence*(1-minute_role_factor*usage_role_factor)).clip(.35,1)
    history_share *= role_discount
    current['Historical Weight %'] = 100*history_share
    # Missing xFppg means we use observed rate + history, with lower confidence.
    projected_rate = (baseline_rate.fillna(0)*history_share +
                      current_rate.fillna(0)*(1-history_share))
    # Expected Fppg already reflects this season's shot volume and opportunity;
    # applying the full usage adjustment to it would double-count role changes.
    # Only the residual historical influence receives a modest usage correction.
    current['Opportunity Adjustment %'] = 100*usage_effect*history_share
    projected_rate *= (1+usage_effect*history_share)
    current['Projected Fppg'] = projected_rate*current['Projected MPG']
    current.loc[actual_rate.isna() | current['Projected MPG'].isna(), 'Projected Fppg'] = np.nan
    current['Baseline Fppg'] = current['Historical Fppg']
    current['Minutes Impact'] = (current['Projected MPG']-current['MPG'])*actual_rate
    current['Projection vs History %'] = (100*(current['Projected Fppg']/historical_fppg.where(historical_fppg.gt(0))-1))
    current['Model Warning'] = np.where(
        (current['Historical Seasons'].ge(2) & gp.lt(20) &
         current['Projection vs History %'].lt(-15)).fillna(False).to_numpy(dtype=bool),
        'Early-season projection substantially below historical baseline', '')
    observed = current['Fppg']
    # Rank fantasy relevance by projected per-game value, not raw regression gap.
    # Unprojectable players are unranked; rank is independent of UI filters.
    current['ROS Upside'] = current['Projected Fppg']-observed
    current['ROS Upside %'] = 100*current['ROS Upside'].div(observed.where(observed.gt(0)))
    current['Sell-High Gap'] = (observed-current['Projected Fppg']).clip(lower=0)
    # Distinct bounce-back score requires established history AND positive
    # current expected gap, not merely a historical career peak.
    current['Bounce-Back Score'] = current[['Expected Gap','Historical Gap']].clip(lower=0).min(axis=1)
    qualifies = current['Historical Seasons'].ge(2) & current['Expected Gap'].gt(0) & current['Historical Gap'].gt(0)
    current.loc[~qualifies,'Bounce-Back Score'] = np.nan
    # Mirror the bounce-back signal: sell-high candidates outperform both
    # their expected production and their adjusted historical baseline.
    current['Historical Sell-High Score'] = (-current[['Expected Gap','Historical Gap']]).clip(lower=0).min(axis=1)
    sell_qualifies = (current['Historical Seasons'].ge(2) &
                      current['Expected Gap'].lt(0) & current['Historical Gap'].lt(0))
    current.loc[~sell_qualifies, 'Historical Sell-High Score'] = np.nan
    x = current['xFppg']
    h = current['Historical Fppg']
    # Confidence is data sufficiency, not probability or calibrated accuracy.
    advanced_coverage = current[['USG%','Pot_AST','REB Chances','Touches']].notna().sum(axis=1)
    current['Data Coverage'] = advanced_coverage.astype(str)+'/4'
    current['Confidence'] = 'Low'
    current.loc[gp.ge(10) & (x.notna() | h.notna()),'Confidence'] = 'Medium'
    current.loc[gp.ge(25) & x.notna() & current['Historical Seasons'].ge(2) & advanced_coverage.ge(2),'Confidence'] = 'High'
    current.loc[gp.lt(3), ['Projected Fppg','ROS Upside','ROS Upside %','Bounce-Back Score','Sell-High Gap','Historical Sell-High Score']] = np.nan
    current['Projected Rank'] = current['Projected Fppg'].rank(method='min', ascending=False)
    if fantasy is not None and not fantasy.empty:
        from espn_fantasy import merge_fantasy
        current,_ = merge_fantasy(current,fantasy,year)
    else:
        for col in ('Fantasy Team','Positions','Injury'): current[col] = pd.NA
    current['Fantasy Team'] = current['Fantasy Team'].fillna('FA').replace('','FA')
    return current.sort_values('ROS Upside',ascending=False,na_position='last').reset_index(drop=True)
