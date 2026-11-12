"""Next-season role calibration, fitted on completed historical NBA seasons.

Training: 2017-18 through 2022-23 -> next-season outcomes through 2023-24.
Holdout: 2023-24/2024-25 inputs -> 2024-25/2025-26 outcomes.
This is NOT a rest-of-season validation. Coefficients are fixed to avoid a
runtime dependency on the exported training CSV or scikit-learn.
"""
import numpy as np
import pandas as pd

PARAMS = {
    'MPG': {
        'coef': [22.42772806862155, 6.5330086050187735, -0.39245184518151105, -0.6923931842392342, 0.4519651363051521, -0.7733463523359569],
        'mean': [22.503629413428367, 0.6776415543906801, 0.6856127886323268, 0.7029307282415631, 6.190350268603195],
        'scale': [8.291226924697316, 0.24671634829806557, 1.6581000188310142, 1.2331026042067894, 4.702507193035564],
        'holdout_n': 809, 'baseline_mae': 4.053981955272671, 'calibrated_mae': 3.946693701043704,
    },
    'USG%': {
        'coef': [18.419760213143867, 4.639499536564162, -0.5216213867792441, -0.20006230816280088, 0.3749302152058027, -1.046634367214478],
        'mean': [18.573756660745985, 0.6776415543906801, 0.6856127886323268, 0.7029307282415631, 5.837244942165232],
        'scale': [5.289268225039882, 0.24671634829806557, 1.6581000188310142, 1.2331026042067894, 4.788230403621625],
        'holdout_n': 809, 'baseline_mae': 2.308034610630408, 'calibrated_mae': 2.1761740004244894,
    },
}


def predict_next_role(previous, gp, age, metric):
    """Predict next season's MPG or usage from a completed prior season.

    Usage input and output use fractions (0.18 = 18%), matching NBA dataset.
    Returns NaN if inputs are unavailable. Does not forecast injury/availability.
    """
    p = PARAMS[metric]
    prev = pd.to_numeric(previous, errors='coerce')
    games = pd.to_numeric(gp, errors='coerce')
    years = pd.to_numeric(age, errors='coerce')
    if metric == 'USG%':
        prev = prev * 100
    sample = np.clip(games, 0, 82) / 82
    x = np.column_stack([prev, sample, np.maximum(0, years-29),
                         np.maximum(0, 24-years), prev*(1-sample)])
    z = (x-np.asarray(p['mean']))/np.asarray(p['scale'])
    out = p['coef'][0]+z@np.asarray(p['coef'][1:])
    if metric == 'USG%':
        out = np.clip(out, 5, 40)/100
    else:
        out = np.clip(out, 0, 40)
    out[~np.isfinite(x).all(axis=1)] = np.nan
    return pd.Series(out, index=previous.index)
