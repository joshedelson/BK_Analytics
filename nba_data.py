"""NBA Fantasy Analytics: downloads, transformations and local persistence."""

import io
import os
import json
import sqlite3
import time
import inspect

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from cloud_db import connect, DB_PATH

from nba_api.stats.endpoints import (
    leaguedashplayerstats,
    leaguehustlestatsplayer,
    leaguedashptstats,
)
from nba_api.stats.library.http import NBAStatsHTTP


# ============================================================
# CONSTANTS
# ============================================================

CATEGORIES = [
    "Standard",
    "Shot Difficulty",
    "Hustle",
    "Rebounding",
    "Passing",
    "Touches",
    "Usage",
]

KEYS = ["Season", "Player ID"]

SCORING = {
    "PTS": 1,
    "FGM": 2,
    "FGA": -1,
    "FTM": 1,
    "FTA": -1,
    "3PM": 1,
    "REB": 1,
    "AST": 2,
    "STL": 4,
    "BLK": 4,
    "TOV": -2,
}

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nba.com",
    "Referer": "https://www.nba.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
}

COMMON_FILTERS = dict(
    College="",
    Conference="",
    Country="",
    DateFrom="",
    DateTo="",
    Division="",
    DraftPick="",
    DraftYear="",
    GameScope="",
    Height="",
    ISTRound="",
    LastNGames="0",
    LeagueID="00",
    Location="",
    Month="0",
    OpponentTeamID="0",
    Outcome="",
    PORound="0",
    PlayerExperience="",
    PlayerPosition="",
    SeasonSegment="",
    SeasonType="Regular Season",
    TeamID="0",
    VsConference="",
    VsDivision="",
    Weight="",
)

RENAME = {
    "PLAYER_ID": "Player ID",
    "PLAYER_NAME": "Player",
    "TEAM_ABBREVIATION": "Team",
    "TEAM_ID": "Team ID",
    "FT_PCT": "FT%",
    "PLUS_MINUS": "+-",
    "FG_PCT": "FG%",
    "FG3M": "3PM",
    "FG3A": "3PA",
    "FG3_PCT": "3PT%",
}

SHOT_RENAME = {
    "PLAYERNAME": "Player",
    "PLAYERID": "Player ID",
    "TEAMABBREVIATION": "Team",
    "AVGSHOTQUALITY": "xFG%",
    "AVGMADESHOTQUALITY": "Made xFG%",
    "AVGMISSEDSHOTQUALITY": "Miss xFG%",
    "MINSHOTQUALITYONMAKE": "MIN xFG% Made",
    "AVGSHOTQUALITY2PT": "x2P%",
    "AVGMADESHOTQUALITY2PT": "2PT Made xFG%",
    "AVGMISSEDSHOTQUALITY2PT": "2PT Miss xFG%",
    "AVGSHOTQUALITY3PT": "x3P%",
    "AVGMADESHOTQUALITY3PT": "3PT Made xFG%",
    "AVGMISSEDSHOTQUALITY3PT": "3PT Miss xFG%",
    "FGPCTABOVEEXPECTED": "FG%+",
    "FG3PCTABOVEEXPECTED": "3P%+",
}

HUSTLE_RENAME = {
    "DEFLECTIONS": "Deflections",
    "CONTESTED_SHOTS": "Contested Shots",
    "CONTESTED_SHOTS_2PT": "Contested 2PT",
    "CONTESTED_SHOTS_3PT": "Contested 3PT",
    "CHARGES_DRAWN": "Charges Drawn",
}

PASSING_RENAME = {
    "POTENTIAL_AST": "Pot_AST",
    "PASSES_MADE": "Passes Made",
    "PASSES_RECEIVED": "Passes Received",
}

REBOUND_RENAME = {
    "OREB_CONTEST": "Contested OREB",
    "OREB_UNCONTEST": "Uncontested OREB",
    "OREB_CONTEST_PCT": "Contested OREB%",
    "OREB_CHANCES": "OREB Chances",
    "OREB_CHANCE_PCT": "OREB Chance%",
    "OREB_CHANCE_DEFER": "OREB Chances Deferred",
    "OREB_CHANCE_PCT_ADJ": "Adjusted OREB Chance%",
    "AVG_OREB_DIST": "Avg OREB Distance",
    "DREB_CONTEST": "Contested DREB",
    "DREB_UNCONTEST": "Uncontested DREB",
    "DREB_CONTEST_PCT": "Contested DREB%",
    "DREB_CHANCES": "DREB Chances",
    "DREB_CHANCE_PCT": "DREB Chance%",
    "DREB_CHANCE_DEFER": "DREB Chances Deferred",
    "DREB_CHANCE_PCT_ADJ": "Adjusted DREB Chance%",
    "AVG_DREB_DIST": "Avg DREB Distance",
    "REB_CONTEST": "Contested REB",
    "REB_UNCONTEST": "Uncontested REB",
    "REB_CONTEST_PCT": "Contested REB%",
    "REB_CHANCES": "REB Chances",
    "REB_CHANCE_PCT": "REB Chance%",
    "REB_CHANCE_DEFER": "REB Chances Deferred",
    "REB_CHANCE_PCT_ADJ": "Adjusted REB Chance%",
    "AVG_REB_DIST": "Avg REB Distance",
}

TOUCH_RENAME = {
    "POINTS": "Points",
    "TOUCHES": "Touches",
    "FRONT_CT_TOUCHES": "Front Court Touches",
    "TIME_OF_POSS": "Time of Possession",
    "AVG_SEC_PER_TOUCH": "Avg Seconds per Touch",
    "AVG_DRIB_PER_TOUCH": "Avg Dribbles per Touch",
    "PTS_PER_TOUCH": "Points per Touch",
    "ELBOW_TOUCHES": "Elbow Touches",
    "POST_TOUCHES": "Post Touches",
    "PAINT_TOUCHES": "Paint Touches",
    "PTS_PER_ELBOW_TOUCH": "Points per Elbow Touch",
    "PTS_PER_POST_TOUCH": "Points per Post Touch",
    "PTS_PER_PAINT_TOUCH": "Points per Paint Touch",
}

SHOT_DROP = {
    "FIRSTNAME",
    "LASTNAME",
    "Team",
    "TEAMID",
    "TEAMCITY",
    "TEAMNAME",
    "FGPCT",
    "FG3PCT",
    "FG2M",
    "FG2A",
    "FG3M",
    "FG3A",
    "FGM",
    "FGA",
    "AVGDEFENSIVEHANDHEIGHT",
    "AVGDEFENDERBALLDISTANCE",
    "AVGSHOOTERSPEED",
    "AVGROTATIONDEGREES",
    "AVGDEFENDERPRESSURESCORE",
    "JERSEYNUM",
    "POSITION",
}

TOUCH_DROP = {
    "GP",
    "W",
    "L",
    "Team",
    "Team ID",
    "MIN",
    "Points per Paint Touch",
    "Front Court Touches",
    "Avg Seconds per Touch",
    "Avg Dribbles per Touch",
    "Points",
    "Points per Touch",
    "Points per Elbow Touch",
    "Points per Post Touch",
}


# ============================================================
# DATABASE
# ============================================================

def connection():
    con = connect()

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS datasets (
            season TEXT NOT NULL,
            category TEXT NOT NULL,
            downloaded_at TEXT NOT NULL,
            payload TEXT NOT NULL,
            PRIMARY KEY (season, category)
        )
        """
    )

    return con


def save_dataset(season, category, df):
    if df.empty:
        raise ValueError("Cannot save an empty dataset.")

    payload = df.to_json(
        orient="split",
        date_format="iso",
    )

    with connection() as con:
        con.execute(
            """
            INSERT INTO datasets (
                season,
                category,
                downloaded_at,
                payload
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT (season, category)
            DO UPDATE SET
                downloaded_at = excluded.downloaded_at,
                payload = excluded.payload
            """,
            (
                season,
                category,
                datetime.now(timezone.utc).isoformat(),
                payload,
            ),
        )

        con.commit()


def load_dataset(season, category):
    with connection() as con:
        row = con.execute(
            """
            SELECT payload
            FROM datasets
            WHERE season = ? AND category = ?
            """,
            (season, category),
        ).fetchone()

    if row is None:
        return None

    df = pd.read_json(
        io.StringIO(row[0]),
        orient="split",
        dtype={"Player ID": str},
    )

    df["Player ID"] = df["Player ID"].astype("string")

    df["Season"] = pd.to_numeric(
        df["Season"],
        errors="coerce",
    ).astype("Int64")

    return df


def status_table():
    with connection() as con:
        rows = con.execute(
            """
            SELECT
                season,
                category,
                downloaded_at
            FROM datasets
            ORDER BY season DESC, category
            """
        ).fetchall()

    return pd.DataFrame(
        rows,
        columns=["Season", "Category", "Updated"],
    )


# ============================================================
# NBA API REQUESTS
# ============================================================

def request_json(endpoint, params, attempts=2):
    """Use the NBA API HTTP transport."""

    last_error = None

    for attempt in range(attempts):
        try:
            response = NBAStatsHTTP().send_api_request(
                endpoint=endpoint,
                parameters=params,
                timeout=90,
            )

            payload = response.get_dict()

            if not isinstance(payload, dict):
                raise ValueError(
                    f"Unexpected response type for {endpoint}."
                )

            return payload

        except Exception as exc:
            last_error = exc

            if attempt < attempts - 1:
                time.sleep(3 * (attempt + 1))

    raise RuntimeError(
        f"NBA endpoint {endpoint} failed: {last_error}"
    )


def fetch_with_nba_api(endpoint_class, season, measure=None):
    """Use official endpoint defaults across nba_api versions."""

    signature = inspect.signature(endpoint_class)

    options = {
        "season": season,
        "season_type_all_star": "Regular Season",
        "per_mode_time": "Totals",
        "per_mode_simple": "Totals",
        "per_mode_detailed": "Totals",
        "per_mode": "Totals",
        "player_or_team": "Player",
        "pt_measure_type": measure,
        "timeout": 90,
    }

    kwargs = {
        name: value
        for name, value in options.items()
        if name in signature.parameters and value is not None
    }

    result = endpoint_class(**kwargs)

    frames = result.get_data_frames()

    if not frames:
        raise ValueError(
            f"{endpoint_class.__name__} returned no data frames."
        )

    return frames[0]


def result_table(payload, result_name):
    sets = payload.get(
        "resultSets",
        payload.get("resultSet"),
    )

    if isinstance(sets, dict):
        sets = [sets]

    if not isinstance(sets, list):
        raise ValueError("NBA response has no result sets.")

    result = next(
        (
            r
            for r in sets
            if r.get("name") == result_name
        ),
        None,
    )

    if result is None:
        raise ValueError(
            f"Expected result set {result_name!r} missing."
        )

    return pd.DataFrame(
        result.get("rowSet", []),
        columns=result["headers"],
    )


# ============================================================
# NORMALIZATION
# ============================================================

def normalize(df, season):
    df = df.copy()

    if "Player ID" not in df:
        raise ValueError(
            "Missing Player ID; cannot merge safely."
        )

    ids = pd.to_numeric(
        df["Player ID"],
        errors="coerce",
    ).astype("Int64")

    df = df.loc[ids.notna()].copy()

    df["Player ID"] = (
        ids.loc[ids.notna()].astype(str)
    )

    df["Season"] = int(season[:4]) + 1

    if df.empty:
        raise ValueError(
            "No valid player rows returned; existing data left untouched."
        )

    if df.duplicated(KEYS).any():
        if "Team" in df:
            df["_total"] = (
                df["Team"].astype(str).eq("TOT")
            )

            df = (
                df.sort_values(
                    "_total",
                    ascending=False,
                )
                .drop(columns="_total")
            )

        if df.duplicated(KEYS).any():
            raise ValueError(
                "Duplicate player-season rows detected; "
                "not saving ambiguous data."
            )

    return df.reset_index(drop=True)


# ============================================================
# DOWNLOAD NBA STATISTICS
# ============================================================

def fetch_category(season, category):
    if category not in CATEGORIES:
        raise ValueError(
            f"Unknown category: {category}"
        )

    if category == "Shot Difficulty":
        payload = request_json(
            "shotqualityleaders",
            {
                "LeagueID": "00",
                "Season": season,
                "SeasonType": "Regular Season",
                "TeamID": "0",
            },
        )

        shots = payload.get("shots")

        if not isinstance(shots, list):
            raise ValueError(
                "Shot Difficulty response did not contain a shots array."
            )

        df = pd.DataFrame.from_records(shots)

        df = df.rename(columns=SHOT_RENAME)

        df = df.drop(
            columns=[
                c
                for c in df
                if c in SHOT_DROP or c.endswith("_RANK")
            ],
            errors="ignore",
        )

    elif category == "Standard":
        response = leaguedashplayerstats.LeagueDashPlayerStats(
            season=season,
            season_type_all_star="Regular Season",
            measure_type_detailed_defense="Base",
            per_mode_detailed="Totals",
            timeout=90,
        )

        df = response.get_data_frames()[0]

        df = df.rename(columns=RENAME)

        df = df.drop(
            columns=[
                c
                for c in df
                if c.endswith("_RANK")
            ],
            errors="ignore",
        )

        df = df.drop(
            columns=[
                "NICKNAME",
                "W",
                "L",
                "W_PCT",
                "NBA_FANTASY_PTS",
                "WNBA_FANTASY_PTS",
                "FP_HIGH_SCORE",
                "BLKA",
                "PFD",
                "TEAM_COUNT",
            ],
            errors="ignore",
        )

    elif category == "Usage":
        response = leaguedashplayerstats.LeagueDashPlayerStats(
            season=season,
            season_type_all_star="Regular Season",
            measure_type_detailed_defense="Usage",
            per_mode_detailed="Totals",
            timeout=90,
        )

        df = response.get_data_frames()[0]

        df = df.rename(columns=RENAME)

        usage_names = {
            "USG_PCT": "USG%"
        }

        usage_names.update(
            {
                col: f"Usage {col[4:]}%"
                for col in df.columns
                if col.startswith("PCT_")
            }
        )

        df = df.rename(columns=usage_names)

        keep = (
            ["Player ID", "USG%"]
            + [
                col
                for col in df.columns
                if col.startswith("Usage ")
                and col.endswith("%")
            ]
        )

        df = df[
            [
                col
                for col in keep
                if col in df.columns
            ]
        ]

        if "USG%" not in df:
            raise ValueError(
                "Usage endpoint did not return USG_PCT."
            )

    elif category == "Hustle":
        df = fetch_with_nba_api(
            leaguehustlestatsplayer.LeagueHustleStatsPlayer,
            season,
        )

        df = df.rename(
            columns={
                **RENAME,
                **HUSTLE_RENAME,
            }
        )

        df = df.drop(
            columns=[
                "Team ID",
                "Team",
                "AGE",
                "G",
                "MIN",
                "SCREEN_ASSISTS",
                "SCREEN_AST_PTS",
            ],
            errors="ignore",
        )

    else:
        measure = {
            "Passing": "Passing",
            "Rebounding": "Rebounding",
            "Touches": "Possessions",
        }[category]

        df = fetch_with_nba_api(
            leaguedashptstats.LeagueDashPtStats,
            season,
            measure,
        )

        renames = {
            "Passing": PASSING_RENAME,
            "Rebounding": REBOUND_RENAME,
            "Touches": TOUCH_RENAME,
        }[category]

        df = df.rename(
            columns={
                **RENAME,
                **renames,
            }
        )

        if category == "Passing":
            drop = [
                "W",
                "L",
                "GP",
                "Team",
                "Team ID",
                "MIN",
                "FT_AST",
                "SECONDARY_AST",
                "AST",
            ]

        elif category == "Rebounding":
            drop = [
                "W",
                "L",
                "Team",
                "Team ID",
                "GP",
                "MIN",
                "OREB",
                "DREB",
                "REB",
            ]

        else:
            drop = list(TOUCH_DROP)

        df = df.drop(
            columns=drop,
            errors="ignore",
        )

    return normalize(df, season)


# ============================================================
# FANTASY SCORING
# ============================================================

def calculate_fantasy(df):
    df = df.copy()

    if (
        not all(c in df.columns for c in SCORING)
        or "GP" not in df
    ):
        return df

    df["Fpts"] = sum(
        pd.to_numeric(
            df[c],
            errors="coerce",
        ).fillna(0) * weight
        for c, weight in SCORING.items()
    ).round(2)

    gp = pd.to_numeric(
        df["GP"],
        errors="coerce",
    ).replace(0, float("nan"))

    df["Fppg"] = (
        df["Fpts"] / gp
    ).round(2)

    if "MIN" in df:
        df["MIN"] = pd.to_numeric(
            df["MIN"],
            errors="coerce",
        ).round(2)

    return df


# ============================================================
# COMBINE DATASETS
# ============================================================

def _combine_raw(seasons, categories):
    tables = {}

    for category in CATEGORIES:
        if category not in categories:
            continue

        frames = [
            load_dataset(s, category)
            for s in seasons
        ]

        frames = [
            f
            for f in frames
            if f is not None and not f.empty
        ]

        if frames:
            table = pd.concat(
                frames,
                ignore_index=True,
            )

            if table.duplicated(KEYS).any():
                raise ValueError(
                    f"Duplicate keys found in {category}."
                )

            tables[category] = table

    if not tables:
        return pd.DataFrame()

    base_name = (
        "Standard"
        if "Standard" in tables
        else next(iter(tables))
    )

    base = tables.pop(base_name).copy()

    for category in CATEGORIES:
        if category not in tables:
            continue

        other = tables[category].copy()

        other = other.drop(
            columns=[
                c
                for c in [
                    "Player",
                    "Team",
                    "Team ID",
                    "AGE",
                ]
                if c in other and c in base
            ],
            errors="ignore",
        )

        overlaps = (
            set(other.columns)
            & set(base.columns)
        ) - set(KEYS)

        other = other.rename(
            columns={
                c: f"{category} | {c}"
                for c in overlaps
            }
        )

        base = base.merge(
            other,
            on=KEYS,
            how="outer",
            validate="one_to_one",
        )

    base = calculate_fantasy(base)

    first = [
        c
        for c in [
            "Season",
            "Player",
            "Player ID",
            "Team",
            "Team ID",
            "AGE",
            "Fpts",
            "Fppg",
        ]
        if c in base
    ]

    base = base[
        first
        + [
            c
            for c in base
            if c not in first
        ]
    ]

    sort = [
        c
        for c in ["Season", "Fppg"]
        if c in base
    ]

    if sort:
        base = base.sort_values(
            sort,
            ascending=[False] * len(sort),
            na_position="last",
        )

    return base.reset_index(drop=True)


# ============================================================
# EXPECTED STATISTICS
# ============================================================

EXPECTED_COLUMNS = [
    "xFT%",
    "xFGM",
    "xFTM",
    "x3PM",
    "xPTS",
    "xREB",
    "xAST",
    "xSTL",
    "xBLK",
    "xTO",
    "xFpts",
    "xFppg",
    "+-Fppg",
]


def _num(frame, column):
    if column not in frame:
        return pd.Series(
            float("nan"),
            index=frame.index,
            dtype="float64",
        )

    return pd.to_numeric(
        frame[column],
        errors="coerce",
    ).astype("float64")


def _ratio(numerator, denominator):
    return (
        numerator.div(
            denominator.where(denominator != 0)
        )
        .replace(
            [
                float("inf"),
                -float("inf"),
            ],
            float("nan"),
        )
    )


def _percentage(series):
    valid = series.dropna()

    if (
        not valid.empty
        and valid.quantile(0.95) > 1.01
    ):
        return series / 100

    return series


def _weighted_history(df, column):
    """Weighted current and previous two season totals."""

    values = _num(
        df,
        column,
    ).fillna(0)

    lookup = pd.Series(
        values.to_numpy(),
        index=pd.MultiIndex.from_arrays(
            [
                df["Player ID"].astype(str),
                df["Season"].astype(int),
            ]
        ),
    )

    ids = df["Player ID"].astype(str)
    years = df["Season"].astype(int)

    out = pd.Series(
        0.0,
        index=df.index,
    )

    for offset, weight in (
        (0, 1.0),
        (1, 0.75),
        (2, 0.35),
    ):
        keys = pd.MultiIndex.from_arrays(
            [
                ids,
                years - offset,
            ]
        )

        out += weight * pd.Series(
            lookup.reindex(keys).fillna(0).to_numpy(),
            index=df.index,
        )

    return out


def _weighted_league(df, column):
    totals = (
        _num(df, column)
        .fillna(0)
        .groupby(df["Season"])
        .sum()
    )

    years = df["Season"].astype(int)

    result = pd.Series(
        0.0,
        index=df.index,
    )

    for offset, weight in (
        (0, 1.0),
        (1, 0.75),
        (2, 0.35),
    ):
        result += weight * pd.Series(
            totals.reindex(
                years - offset
            ).fillna(0).to_numpy(),
            index=df.index,
        )

    return result


def calculate_expected(df):
    """Calculate expected NBA fantasy statistics."""

    if (
        df.empty
        or not {"Season", "Player ID"}.issubset(df.columns)
    ):
        return df

    out = df.copy().reset_index(drop=True)

    season = out["Season"]

    # --------------------------------------------------------
    # Expected shooting
    # --------------------------------------------------------

    fg_pct = _percentage(
        _num(out, "xFG%")
    ).fillna(
        _percentage(_num(out, "FG%"))
    )

    three_pct = _percentage(
        _num(out, "x3P%")
    ).fillna(
        _percentage(_num(out, "3PT%"))
    )

    out["xFGM"] = (
        fg_pct * _num(out, "FGA")
    )

    out["x3PM"] = (
        three_pct * _num(out, "3PA")
    )

    # --------------------------------------------------------
    # Expected free throws
    # --------------------------------------------------------

    wftm = _weighted_history(out, "FTM")
    wfta = _weighted_history(out, "FTA")

    lftm = _weighted_league(out, "FTM")
    lfta = _weighted_league(out, "FTA")

    lgft = _ratio(
        lftm,
        lfta,
    ).fillna(0)

    out["xFT%"] = (
        wftm + 50 * lgft
    ) / (
        wfta + 50
    )

    out["xFTM"] = (
        _num(out, "FTA") * out["xFT%"]
    )

    out["xPTS"] = (
        2 * out["xFGM"]
        + out["xFTM"]
        + out["x3PM"]
    )

    # --------------------------------------------------------
    # League rates
    # --------------------------------------------------------

    def league_rate(numerator, denominator):
        n = (
            _num(out, numerator)
            .fillna(0)
            .groupby(season)
            .sum()
        )

        d = (
            _num(out, denominator)
            .fillna(0)
            .groupby(season)
            .sum()
        )

        return season.map(
            _ratio(n, d).fillna(0)
        ).astype(float)

    # --------------------------------------------------------
    # Expected rebounds
    # --------------------------------------------------------

    dreb_ch = _num(out, "DREB Chances")
    oreb_ch = _num(out, "OREB Chances")

    dreb_defer = _num(
        out,
        "DREB Chances Deferred",
    )

    oreb_defer = _num(
        out,
        "OREB Chances Deferred",
    )

    if all(
        c in out
        for c in (
            "DREB Chances",
            "OREB Chances",
            "DREB Chances Deferred",
            "OREB Chances Deferred",
            "DREB",
            "OREB",
        )
    ):
        dreb_denom = (
            (dreb_ch.fillna(0) - dreb_defer.fillna(0))
            .groupby(season)
            .sum()
        )

        oreb_denom = (
            (oreb_ch.fillna(0) - oreb_defer.fillna(0))
            .groupby(season)
            .sum()
        )

        dreb_num = (
            _num(out, "DREB")
            .fillna(0)
            .groupby(season)
            .sum()
        )

        oreb_num = (
            _num(out, "OREB")
            .fillna(0)
            .groupby(season)
            .sum()
        )

        drate = season.map(
            _ratio(
                dreb_num,
                dreb_denom,
            ).fillna(0)
        )

        orate = season.map(
            _ratio(
                oreb_num,
                oreb_denom,
            ).fillna(0)
        )

        out["xREB"] = (
            (dreb_ch - dreb_defer) * drate
            + (oreb_ch - oreb_defer) * orate
        )

    else:
        out["xREB"] = float("nan")

    # --------------------------------------------------------
    # Expected assists
    # --------------------------------------------------------

    if "Pot_AST" in out and "AST" in out:
        out["xAST"] = (
            _num(out, "Pot_AST")
            * league_rate("AST", "Pot_AST")
        )
    else:
        out["xAST"] = float("nan")

    # --------------------------------------------------------
    # Expected steals
    # --------------------------------------------------------

    if {
        "Deflections",
        "STL",
        "MIN",
    }.issubset(out.columns):

        defl = _num(out, "Deflections")
        stl = _num(out, "STL")
        minutes = _num(out, "MIN")

        valid = (
            defl.notna()
            & stl.notna()
        )

        denominator = (
            defl[valid] ** 2
        ).sum()

        if denominator:
            coeff = max(
                0.0,
                min(
                    1.0,
                    float(
                        (
                            defl[valid] * stl[valid]
                        ).sum() / denominator
                    ),
                ),
            )
        else:
            coeff = 0.0

        league_stl = (
            stl.fillna(0)
            .groupby(season)
            .sum()
        )

        league_defl = (
            defl.fillna(0)
            .groupby(season)
            .sum()
        )

        league_min = (
            minutes.fillna(0)
            .groupby(season)
            .sum()
        )

        residual_rate = _ratio(
            (
                league_stl
                - coeff * league_defl
            ).clip(lower=0),
            league_min,
        ).fillna(0)

        league_rate_by_row = season.map(
            residual_rate
        ).astype(float)

        wstl = _weighted_history(out, "STL")
        wmin = _weighted_history(out, "MIN")
        wdefl = _weighted_history(
            out,
            "Deflections",
        )

        player_residual = (
            wstl - coeff * wdefl
        ).clip(lower=0)

        player_rate = (
            (
                player_residual
                + 500 * league_rate_by_row
            )
            / (wmin + 500)
        ).clip(lower=0)

        out["xSTL"] = (
            minutes * player_rate
            + defl * coeff
        )

    else:
        out["xSTL"] = float("nan")

    # --------------------------------------------------------
    # Expected blocks
    # --------------------------------------------------------

    if (
        "Contested Shots" in out
        and "BLK" in out
    ):
        out["xBLK"] = (
            _num(out, "Contested Shots")
            * league_rate(
                "BLK",
                "Contested Shots",
            )
        )
    else:
        out["xBLK"] = float("nan")

    # --------------------------------------------------------
    # Expected turnovers
    # --------------------------------------------------------

    if (
        "Touches" in out
        and "TOV" in out
    ):
        wto = _weighted_history(out, "TOV")
        wt = _weighted_history(out, "Touches")

        lto = _weighted_league(out, "TOV")
        lt = _weighted_league(out, "Touches")

        out["xTO"] = (
            _num(out, "Touches")
            * (
                wto
                + 1000 * _ratio(
                    lto,
                    lt,
                ).fillna(0)
            )
            / (wt + 1000)
        )

    else:
        out["xTO"] = float("nan")

    # --------------------------------------------------------
    # Expected fantasy points
    # --------------------------------------------------------

    components = {
        "xPTS": 1,
        "xFGM": 2,
        "FGA": -1,
        "xFTM": 1,
        "FTA": -1,
        "x3PM": 1,
        "xREB": 1,
        "xAST": 2,
        "xSTL": 4,
        "xBLK": 4,
        "xTO": -2,
    }

    if all(c in out for c in components):
        out["xFpts"] = sum(
            _num(out, col) * weight
            for col, weight in components.items()
        )
    else:
        out["xFpts"] = float("nan")

    out["xFppg"] = _ratio(
        out["xFpts"],
        _num(out, "GP"),
    )

    out["+-Fppg"] = (
        out["xFppg"]
        - _num(out, "Fppg")
    )

    # --------------------------------------------------------
    # Dataset completeness
    # --------------------------------------------------------

    with connection() as con:
        rows = con.execute(
            """
            SELECT season, category
            FROM datasets
            """
        ).fetchall()

    available = {
        (
            int(year[:4]) + 1,
            category,
        )
        for year, category in rows
    }

    def has_seasons(year, category, offsets=(0,)):
        return all(
            (
                int(year) - offset,
                category,
            ) in available
            for offset in offsets
        )

    years = pd.to_numeric(
        out["Season"],
        errors="coerce",
    )

    history = (0, 1, 2)

    standard = years.map(
        lambda year: has_seasons(
            year,
            "Standard",
        )
    )

    shot = years.map(
        lambda year: (
            year in (2026, 2027)
            and has_seasons(
                year,
                "Shot Difficulty",
            )
        )
    )

    hustle = years.map(
        lambda year: has_seasons(
            year,
            "Hustle",
        )
    )

    rebound = years.map(
        lambda year: has_seasons(
            year,
            "Rebounding",
        )
    )

    passing = years.map(
        lambda year: has_seasons(
            year,
            "Passing",
        )
    )

    touches = years.map(
        lambda year: has_seasons(
            year,
            "Touches",
        )
    )

    std_history = years.map(
        lambda year: has_seasons(
            year,
            "Standard",
            history,
        )
    )

    hustle_history = years.map(
        lambda year: has_seasons(
            year,
            "Hustle",
            history,
        )
    )

    touches_history = years.map(
        lambda year: has_seasons(
            year,
            "Touches",
            history,
        )
    )

    # --------------------------------------------------------
    # Blank unavailable expected statistics
    # --------------------------------------------------------

    out["xFGM"] = out["xFGM"].where(
        standard & shot
    )

    out["x3PM"] = out["x3PM"].where(
        standard & shot
    )

    out["xFT%"] = out["xFT%"].where(
        std_history
    )

    out["xFTM"] = out["xFTM"].where(
        std_history
    )

    out["xPTS"] = (
        2 * out["xFGM"]
        + out["xFTM"]
        + out["x3PM"]
    )

    out["xREB"] = out["xREB"].where(
        standard & rebound
    )

    out["xAST"] = out["xAST"].where(
        standard & passing
    )

    out["xSTL"] = out["xSTL"].where(
        std_history & hustle_history
    )

    out["xBLK"] = out["xBLK"].where(
        standard & hustle
    )

    out["xTO"] = out["xTO"].where(
        std_history & touches_history
    )

    # --------------------------------------------------------
    # Recalculate fantasy totals
    # --------------------------------------------------------

    out["xFpts"] = sum(
        _num(out, col) * weight
        for col, weight in components.items()
    )

    out["xFpts"] = out["xFpts"].where(
        shot & standard
    )

    out["xFppg"] = _ratio(
        out["xFpts"],
        _num(out, "GP"),
    )

    out["+-Fppg"] = (
        out["xFppg"]
        - _num(out, "Fppg")
    )

    for col in EXPECTED_COLUMNS:
        out[col] = out[col].round(
            4 if col == "xFT%" else 2
        )

    return out


# ============================================================
# FINAL COMBINED TABLE
# ============================================================

def expected_statistics_table():
    """Historical expected stats shared by all filtered dashboard views."""
    with connection() as con:
        seasons = [row[0] for row in con.execute('SELECT DISTINCT season FROM datasets')]
    if not seasons:
        return pd.DataFrame(columns=KEYS + EXPECTED_COLUMNS)
    full = _combine_raw(seasons, CATEGORIES)
    if full.empty:
        return pd.DataFrame(columns=KEYS + EXPECTED_COLUMNS)
    full = calculate_expected(full)
    return full[KEYS + EXPECTED_COLUMNS]


def combined_table(seasons, categories, expected=None):
    """Join cached historical expectations onto only the requested datasets."""
    if expected is None:
        expected = expected_statistics_table()

    # Load only the data requested by the dashboard.
    visible = _combine_raw(
        seasons,
        categories,
    )

    if visible.empty:
        return visible

    if expected.empty:
        return visible

    additions = expected

    visible = visible.merge(
        additions,
        on=KEYS,
        how="left",
        validate="one_to_one",
    )

    first = [
        c
        for c in [
            "Season",
            "Player",
            "Player ID",
            "Team",
            "Team ID",
            "AGE",
            "Fpts",
            "Fppg",
            "xFpts",
            "xFppg",
            "+-Fppg",
        ]
        if c in visible
    ]

    return visible[
        first
        + [
            c
            for c in visible
            if c not in first
        ]
    ]

