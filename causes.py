"""Possible causes of a trend.

For one place and one target variable (for example "Air temperature") this looks at candidate drivers (ozone, haze, greenhouse gas, water vapour, El Nino ...).
Each candidate is tested the same way as the target (trend test), then compared with the target using standard methods:

  * trend direction check            does the driver change in the direction that physics says would push the target the way it moved?
  * detrended correlation            the ups and downs of the two series after each one's own trend (and seasonal cycle) is removed, so that two things that
                                     merely both drift over time are not mistaken for cause and effect. The p-value uses an effective sample size that accounts
                                     for autocorrelation (Bretherton et al. 1999), and Benjamini-Hochberg correction because several drivers are tested together.
  * first-difference correlation     a second, independent way to remove trends. It must agree.
  * lead / lag + Granger test        a cause has to come before its effect. F-test whether the driver's past improves the prediction of the target beyond the
                                     target's own past, and the same test in the other direction.
  * regression attribution           the sensitivity (target change per unit driver change) is measured from the detrended data, then multiplied by the driver's
                                     own trend to estimate how much of the target's trend that driver can explain.

All of this shows statistical association with a plausible physical mechanism. It cannot prove causation. The app says so.
"""
from __future__ import annotations

import math
import statistics
from concurrent.futures import ThreadPoolExecutor

import stats
import causal
import trends

CO2_URL = "https://gml.noaa.gov/webdata/ccgg/trends/co2/co2_mm_mlo.csv"
ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"

# id: plain name, unit, where the numbers come from
DRIVERS = {
    "TO3": {"name": "Ozone layer thickness", "unit": "Dobson units", "source": "power", "from": "NASA POWER (ozone column from satellite and MERRA-2)"},
    "AOD_55": {"name": "Haze and dust in the air (aerosols)", "unit": "", "source": "power", "from": "NASA POWER (aerosol optical depth, MERRA-2)"},
    "ALLSKY_SFC_LW_DWN": {"name": "Heat radiated back down by the air", "unit": "W/m²", "source": "power", "from": "NASA POWER (CERES satellite)"},
    "TQV": {"name": "Total water vapour in the air column", "unit": "cm", "source": "power", "from": "NASA POWER (MERRA-2)"},
    "CO2": {"name": "Carbon dioxide in the air (global)", "unit": "ppm", "source": "co2", "from": "NOAA Global Monitoring Laboratory, Mauna Loa"},
    "ONI": {"name": "El Niño / La Niña strength", "unit": "°C", "source": "oni", "from": "NOAA Climate Prediction Center (ONI index)"},
    "T2M": {"name": "Air temperature", "unit": "°C", "source": "power", "from": "NASA POWER"},
    "PRECTOTCORR": {"name": "Rain", "unit": "mm/day", "source": "power", "from": "NASA POWER"},
    "RH2M": {"name": "Air humidity (relative)", "unit": "%", "source": "power", "from": "NASA POWER"},
    "CLOUD_AMT": {"name": "Cloud cover", "unit": "%", "source": "power", "from": "NASA POWER"},
    "ALLSKY_SFC_SW_DWN": {"name": "Sunlight reaching the ground", "unit": "kWh/m²/day", "source": "power", "from": "NASA POWER"},
    "WS10M": {"name": "Wind speed at 10 m", "unit": "m/s", "source": "power", "from": "NASA POWER"},
    "GWETROOT": {"name": "Root-zone soil wetness", "unit": "0 to 1", "source": "power", "from": "NASA POWER (MERRA-2)"},
    "GWETTOP": {"name": "Surface soil wetness", "unit": "0 to 1", "source": "power", "from": "NASA POWER (MERRA-2)"},
    "PS": {"name": "Air pressure at the surface", "unit": "kPa", "source": "power", "from": "NASA POWER"},
    "TS": {"name": "Sea surface temperature", "unit": "°C", "source": "power", "from": "NASA POWER (MERRA-2 skin temperature)"},
    "WS50M": {"name": "Wind speed at 50 m", "unit": "m/s", "source": "power", "from": "NASA POWER"},
}

# target -> [(driver, expected sign, plain mechanism)]. Sign +1: more driver gives more target. -1: more driver gives less target. 0: no clear sign, any direction counts.
_HEAT_COMMON = [
    ("CO2", 1, "Carbon dioxide traps heat in the air (greenhouse effect), so more of it warms the surface."),
    ("TQV", 1, "Water vapour is a strong greenhouse gas. Warmer air holds more of it, which adds to the warming."),
    ("ALLSKY_SFC_LW_DWN", 1, "The air sends heat down to the ground. More of it means a warmer surface."),
    ("AOD_55", -1, "Haze and dust reflect and block sunlight, which cools the surface. Less haze lets more sun through."),
    ("TO3", 0, "A thinner ozone layer lets more ultraviolet light through. Its direct effect on air temperature is small and debated, so any direction counts here."),
    ("ONI", 1, "El Niño years release heat from the Pacific and warm much of the world a few months later."),
    ("PS", 1, "High pressure brings sinking, clear, dry air that heats up, so more high pressure can mean hotter weather."),
    ("PRECTOTCORR", -1, "Rain wets the soil and cools the air by evaporation, so wetter places run cooler."),
]
KB = {
    "T2M": _HEAT_COMMON + [("CLOUD_AMT", -1, "Clouds shade the ground in daytime, so more cloud usually means a cooler day."), ("ALLSKY_SFC_SW_DWN", 1, "More sunlight reaching the ground heats the surface.")],
    "T2M_MAX": _HEAT_COMMON + [("CLOUD_AMT", -1, "Clouds block the sun in daytime, so more cloud means cooler daytime highs."), ("ALLSKY_SFC_SW_DWN", 1, "More sunlight reaching the ground heats the surface in the day.")],
    "T2M_MIN": _HEAT_COMMON + [("CLOUD_AMT", 1, "Clouds act like a blanket at night, so more cloud means warmer nights."), ("ALLSKY_SFC_SW_DWN", 0, "Daytime sun has only an indirect effect on night temperature.")],
    "TS": _HEAT_COMMON + [("CLOUD_AMT", -1, "Clouds shade the ground in daytime."), ("ALLSKY_SFC_SW_DWN", 1, "More sunlight heats the ground directly.")],
    "PRECTOTCORR": [("TQV", 1, "More water vapour in the air gives more water that can fall as rain."), ("T2M", 1, "Warmer air holds about 7% more water per degree, which tends to make rain heavier."),
                    ("CLOUD_AMT", 1, "Rain falls from clouds, so more cloud usually goes with more rain."), ("PS", -1, "Low pressure lifts air and makes rain. High pressure suppresses it."),
                    ("AOD_55", -1, "Heavy haze can reduce rain by keeping cloud droplets too small to fall."), ("ONI", 0, "El Niño and La Niña shift rain belts. The direction depends on the region.")],
    "RH2M": [("T2M", -1, "Warmer air can hold more water, so with the same water the relative humidity drops."), ("PRECTOTCORR", 1, "Rain adds water to the air and soil, which raises humidity."),
             ("CLOUD_AMT", 1, "Cloudy air is moist air."), ("TQV", 1, "More water in the air column raises humidity."), ("WS10M", 0, "Wind can bring dry or moist air depending on where it comes from."),
             ("ONI", 0, "El Niño and La Niña shift moisture. The direction depends on the region.")],
    "QV2M": [("T2M", 1, "Warmer air holds more water vapour (about 7% per degree)."), ("PRECTOTCORR", 1, "Rain wets the ground, and evaporation puts that water back in the air."),
             ("TQV", 1, "The water in the whole air column and near the ground move together."), ("ONI", 0, "El Niño and La Niña change moisture. The direction depends on the region.")],
    "CLOUD_AMT": [("AOD_55", 1, "Dust and haze give cloud droplets something to form on."), ("RH2M", 1, "Humid air forms cloud more easily."), ("TQV", 1, "More water in the air gives more cloud."),
                  ("T2M", 0, "Warming can add or remove cloud depending on the region."), ("PS", -1, "Low pressure lifts air and forms cloud."), ("ONI", 0, "El Niño and La Niña shift cloud. The direction depends on the region.")],
    "ALLSKY_SFC_SW_DWN": [("CLOUD_AMT", -1, "Clouds block sunlight."), ("AOD_55", -1, "Haze and dust block sunlight (this is called global dimming)."),
                          ("TQV", -1, "Water vapour absorbs some sunlight on its way down."), ("TO3", -1, "Ozone absorbs ultraviolet light, so a thicker layer lets slightly less sun through."),
                          ("RH2M", -1, "Humid air is usually cloudier and hazier.")],
    "WS10M": [("PS", 0, "Wind blows from high to low pressure. How strong depends on the pressure difference, not the level."), ("T2M", 0, "Temperature differences drive winds. The direction depends on the region."),
              ("ONI", 0, "El Niño and La Niña shift wind patterns. The direction depends on the region."), ("AOD_55", 0, "Haze changes how the ground and air heat, which can change winds.")],
    "PS": [("T2M", -1, "Warm air rises and spreads out, so the surface pressure is lower."), ("ONI", 0, "El Niño and La Niña shift pressure systems. The direction depends on the region."),
           ("WS10M", 0, "Wind and pressure are linked, but the direction depends on the region.")],
}


_SOIL_COMMON = [
    ("PRECTOTCORR", 1, "Rain is the main water that goes into the soil, so less rain means a drier soil."),
    ("T2M", -1, "Hotter air pulls water out of the soil faster (evaporation) and out of plants (transpiration)."),
    ("ALLSKY_SFC_SW_DWN", -1, "More sunlight gives more energy to evaporate soil water."),
    ("RH2M", 1, "Humid air slows evaporation, so the soil keeps its water longer."),
    ("WS10M", -1, "Wind carries away the moist air at the soil surface and dries it faster."),
    ("CLOUD_AMT", 1, "Clouds shade the ground and usually come with rain."),
    ("ONI", 0, "El Niño and La Niña shift rain belts. The direction depends on the region."),
]
_SEA = [
    ("T2M", 1, "Warm air heats the sea surface, and the sea heats the air above it."),
    ("WS10M", -1, "Strong wind mixes and cools the surface and lifts water from below, so weaker wind lets the surface warm."),
    ("CLOUD_AMT", -1, "Clouds shade the sea, so less cloud lets more sun warm it."),
    ("ALLSKY_SFC_SW_DWN", 1, "Sunlight is the main heat source for the sea surface."),
    ("CO2", 1, "Carbon dioxide traps heat in the air (greenhouse effect), and the ocean absorbs most of the extra heat."),
    ("TQV", 1, "Water vapour is a strong greenhouse gas that adds to the warming."),
    ("ONI", 1, "El Niño years warm large parts of the ocean."),
    ("AOD_55", -1, "Haze and dust block sunlight, which cools the surface."),
]
_SEALEVEL = [
    ("TS", 1, "Warm water takes more room (thermal expansion), so a warmer sea stands higher."),
    ("CO2", 1, "Carbon dioxide drives global warming, which expands the ocean and melts ice."),
    ("T2M", 1, "Warmer air melts land ice, which adds water to the sea."),
    ("PS", -1, "Low air pressure lets the sea rise a little under it, so falling pressure goes with a higher sea."),
    ("WS10M", 0, "Wind can push water toward or away from the coast. The direction depends on the coast."),
    ("ONI", 0, "El Niño and La Niña move water around the ocean. The direction depends on the region."),
]
_WAVEC = [
    ("WS10M", 1, "Wind builds waves, so stronger wind means bigger waves."),
    ("PS", -1, "Low pressure brings storms and rough seas."),
    ("TS", 1, "A warmer sea feeds stronger storms."),
    ("ONI", 0, "El Niño and La Niña change storm tracks. The direction depends on the region."),
]
KB.update({"MM_SMD": _SOIL_COMMON, "GWETROOT": _SOIL_COMMON, "GWETTOP": _SOIL_COMMON, "GWETPROF": _SOIL_COMMON})
KB.update({"TS": _SEA, "SST_OI": _SEA, "SEALEVEL": _SEALEVEL, "WAVE": _WAVEC, "WAVEP": _WAVEC, "MM_SWELL": _WAVEC, "WS50M": KB["WS10M"]})
_WATERLOSS = [
    ("T2M", 1, "Hotter air gives more energy to turn soil and plant water into vapour."),
    ("ALLSKY_SFC_SW_DWN", 1, "More sunlight gives more energy for evaporation."),
    ("RH2M", -1, "Dry air pulls water out faster, so more humidity means less water loss."),
    ("WS10M", 1, "Wind carries away the moist air at the surface, so it speeds up water loss."),
    ("PRECTOTCORR", 1, "More rain means more water is there to evaporate."),
    ("CLOUD_AMT", -1, "Clouds shade the ground, so less energy is left for evaporation."),
    ("ONI", 0, "El Niño and La Niña shift rain and temperature. The direction depends on the region."),
]

# ---------------------------------------------------------------- global series parsers
def parse_co2(text: str) -> dict:
    """NOAA Mauna Loa monthly CSV: comment lines start with #, then year,month,decimal,average,... Missing is -99.99."""
    out = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        f = [c.strip() for c in line.split(",")]
        try:
            y, m, v = int(f[0]), int(f[1]), float(f[3])
        except (ValueError, IndexError):
            continue
        if 1 <= m <= 12 and v > 0:
            out[f"{y}{m:02d}"] = v
    return out


_SEASON_MID = {"DJF": 1, "JFM": 2, "FMA": 3, "MAM": 4, "AMJ": 5, "MJJ": 6, "JJA": 7, "JAS": 8, "ASO": 9, "SON": 10, "OND": 11, "NDJ": 12}


def parse_oni(text: str) -> dict:
    """NOAA ONI table: SEAS YR TOTAL ANOM. The three-month season is placed on its middle month."""
    out = {}
    for line in (text or "").splitlines():
        t = line.split()
        if len(t) >= 4 and t[0] in _SEASON_MID and t[1].isdigit():
            try:
                out[f"{int(t[1])}{_SEASON_MID[t[0]]:02d}"] = float(t[3])
            except ValueError:
                continue
    return out


def get_text(url: str, timeout: int = 60) -> str:
    import requests
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "RASD-Earth-Trends/1.0"})
    if r.status_code != 200:
        raise trends.DataError(f"{url.split('/')[2]} answered {r.status_code}")
    return r.text


TEXT_GETTER = get_text          # tests replace this


# ---------------------------------------------------------------- small statistics
def _mean(v):
    return sum(v) / len(v)


def pearson(a: list, b: list) -> float:
    ma, mb = _mean(a), _mean(b)
    sa = sum((x - ma) ** 2 for x in a)
    sb = sum((y - mb) ** 2 for y in b)
    if sa <= 0 or sb <= 0:
        return 0.0
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(sa * sb)


def corr_p(r: float, n_eff: float) -> float:
    df = int(math.floor(n_eff - 2))
    if df < 1:
        return 1.0
    if abs(r) >= 1.0:
        return 0.0
    return stats.t_two_sided_p(r * math.sqrt(df / (1.0 - r * r)), df)


def detrend(ts: list, vals: list, seasons: list | None) -> list:
    """Residuals after removing a straight-line trend (and, for monthly values, each calendar month's average)."""
    fit = stats.linear_regression([float(t) for t in ts], vals)
    res = list(fit["residuals"])
    if seasons:
        by = {}
        for s, r in zip(seasons, res):
            by.setdefault(s, []).append(r)
        mm = {s: _mean(v) for s, v in by.items()}
        res = [r - mm[s] for s, r in zip(seasons, res)]
    return res


def solve_ls(rows: list, y: list):
    """Least squares by the normal equations. rows = list of regressor lists (an intercept is added). Returns (coefficients, residual sum of squares)."""
    k = len(rows[0]) + 1
    X = [[1.0] + list(r) for r in rows]
    A = [[sum(X[n][i] * X[n][j] for n in range(len(X))) + (1e-10 if i == j else 0.0) for j in range(k)] for i in range(k)]
    b = [sum(X[n][i] * y[n] for n in range(len(X))) for i in range(k)]
    for i in range(k):
        piv = max(range(i, k), key=lambda r: abs(A[r][i]))
        if abs(A[piv][i]) < 1e-14:
            raise ValueError("singular")
        A[i], A[piv] = A[piv], A[i]
        b[i], b[piv] = b[piv], b[i]
        for r in range(i + 1, k):
            f = A[r][i] / A[i][i]
            for c in range(i, k):
                A[r][c] -= f * A[i][c]
            b[r] -= f * b[i]
    beta = [0.0] * k
    for i in range(k - 1, -1, -1):
        beta[i] = (b[i] - sum(A[i][c] * beta[c] for c in range(i + 1, k))) / A[i][i]
    rss = sum((y[n] - sum(beta[j] * X[n][j] for j in range(k))) ** 2 for n in range(len(X)))
    return beta, rss


def granger(target: dict, driver: dict, order: int) -> float | None:
    """p-value that the past of `driver` helps predict `target` beyond the target's own past. Both are {time index: detrended value}."""
    rows_r, rows_f, ys = [], [], []
    for t, y in target.items():
        past_y = [target.get(t - k) for k in range(1, order + 1)]
        past_x = [driver.get(t - k) for k in range(1, order + 1)]
        if None in past_y or None in past_x:
            continue
        rows_r.append(past_y)
        rows_f.append(past_y + past_x)
        ys.append(y)
    n = len(ys)
    d2 = n - 2 * order - 1
    if d2 < 5:
        return None
    try:
        _, rss_r = solve_ls(rows_r, ys)
        _, rss_f = solve_ls(rows_f, ys)
    except ValueError:
        return None
    if rss_f <= 1e-12:
        return 0.0
    F = max(0.0, ((rss_r - rss_f) / order) / (rss_f / d2))
    return float(min(1.0, stats._betai(d2 / 2.0, order / 2.0, d2 / (d2 + order * F))))


def benjamini_hochberg(ps: list) -> list:
    """Adjusted p-values (false discovery rate). None stays None."""
    idx = [i for i, p in enumerate(ps) if p is not None]
    idx.sort(key=lambda i: ps[i])
    m = len(idx)
    out = [None] * len(ps)
    prev = 1.0
    for rank in range(m, 0, -1):
        i = idx[rank - 1]
        prev = min(prev, ps[i] * m / rank)
        out[i] = prev
    return out


def _tindex(x: float, mode: str) -> int:
    return int(round(x)) if mode == "year" else int(round(x * 12 - 0.5))


# ---------------------------------------------------------------- one driver against the target
def compare(tser: dict, dser: dict, mode: str) -> dict | None:
    """tser and dser are build_series() outputs. Returns the association numbers, or None when there are too few shared points."""
    tmap = {_tindex(x, mode): (v, (tser["seasons"][i] if mode == "month" else None), x) for i, (x, v) in enumerate(zip(tser["x"], tser["values"]))}
    dmap = {_tindex(x, mode): (v, x) for x, v in zip(dser["x"], dser["values"])}
    ts = sorted(set(tmap) & set(dmap))
    need = 8 if mode == "year" else 24
    if len(ts) < need:
        return None
    seas = [tmap[t][1] for t in ts] if mode == "month" else None
    tv, dv = [tmap[t][0] for t in ts], [dmap[t][0] for t in ts]
    xs = [tmap[t][2] for t in ts]
    rt, rd = detrend(xs, tv, seas), detrend(xs, dv, seas)
    n0 = len(ts)
    r_raw = pearson(tv, dv)
    # lead and lag: positive lag = the driver comes first. The match is judged at the best lag from 0 to K (a cause cannot come after its effect).
    K = 3 if mode == "year" else 6
    minpairs = 8 if mode == "year" else 24
    tt = {t: rt[i] for i, t in enumerate(ts)}
    dd = {t: rd[i] for i, t in enumerate(ts)}
    lags = []
    for k in range(-K, K + 1):
        a, b = [], []
        for t, v in tt.items():
            if t - k in dd:
                a.append(v)
                b.append(dd[t - k])
        if len(a) >= minpairs:
            lags.append((k, pearson(a, b)))
    leads = [kr for kr in lags if kr[0] >= 0]
    kbest = max(leads, key=lambda kr: abs(kr[1]))[0] if leads else 0
    best_any = max(lags, key=lambda kr: abs(kr[1])) if lags else (0, 0.0)
    # aligned arrays at the chosen lag
    A = [(t, tmap[t][0], tmap[t][2], tt[t], dd[t - kbest], dmap[t - kbest][0]) for t in ts if (t - kbest) in dd]
    ats = [a[0] for a in A]
    atv, axs, art, ard, adv = [a[1] for a in A], [a[2] for a in A], [a[3] for a in A], [a[4] for a in A], [a[5] for a in A]
    n = len(A)
    r_det = pearson(art, ard)
    n_eff = float(n)
    a1, b1 = stats.lag1(art), stats.lag1(ard)
    if a1 * b1 > 0:
        n_eff = max(3.0, min(float(n), n * (1 - a1 * b1) / (1 + a1 * b1)))
    p_det = min(1.0, corr_p(r_det, n_eff) * max(1, len(leads)))        # Bonferroni for having searched the lags
    pos = {t: i for i, t in enumerate(ats)}
    da, db = [], []
    for t in ats:
        if t - 1 in pos:
            da.append(art[pos[t]] - art[pos[t - 1]])
            db.append(ard[pos[t]] - ard[pos[t - 1]])
    r_diff = pearson(da, db) if len(da) >= 8 else None
    p_diff = corr_p(r_diff, len(da)) if r_diff is not None else None
    order = 1 if mode == "year" else 3
    g_fwd, g_rev = granger(tt, dd, order), granger(dd, tt, order)
    return {"n": n, "r_raw": r_raw, "r_det": r_det, "p_det": p_det, "n_eff": n_eff, "r_diff": r_diff, "p_diff": p_diff, "match_lag": kbest, "best_lag": best_any[0], "best_lag_r": best_any[1],
            "granger_fwd": g_fwd, "granger_rev": g_rev, "lags": [[k, r] for k, r in lags],
            "_ts": ats, "_xs": axs, "_tv": atv, "_dv": adv, "_rt": art, "_rd": ard}


def _pp(p) -> str:
    return "p < 0.001" if p is not None and p < 0.001 else f"p = {stats.pfmt(p)}"


def _unit_word(mode: str, k: int) -> str:
    u = "year" if mode == "year" else "month"
    return f"{abs(k)} {u}{'' if abs(k) == 1 else 's'}"


def lead_text(c: dict, mode: str) -> str:
    k = c["best_lag"]
    if k == 0:
        return "Strongest link at the same time (no lead or lag)."
    return (f"Strongest link when the driver comes {_unit_word(mode, k)} before the target." if k > 0 else f"Strongest link when the driver comes {_unit_word(mode, k)} after the target (it may be a result, not a cause).")


def judge(drv: dict, tr_t: dict, tr_d: dict | None, c: dict | None, sign: int, mode: str) -> tuple:
    """Four checks, each a yes or no with a plain reason. Returns (checks, score)."""
    checks = []
    if tr_d is None or c is None:
        return checks, 0, "normal"
    smooth = tr_d["ols"]["r2"] >= SMOOTH_R2
    # 1 trend direction
    if not tr_d["significant"]:
        checks.append({"ok": False, "name": "Trend direction", "text": "The driver itself shows no significant trend, so it cannot explain a trend."})
    elif sign == 0:
        checks.append({"ok": True, "name": "Trend direction", "text": f"The driver also has a significant trend ({tr_d['direction']}). The direction that matters depends on the region."})
    else:
        pushes = (1 if tr_d["slope_decade"] > 0 else -1) * sign
        target_dir = 1 if tr_t["slope_decade"] > 0 else -1
        ok = tr_t["significant"] and pushes == target_dir
        what = "up" if pushes > 0 else "down"
        checks.append({"ok": ok, "name": "Trend direction", "text": (f"The driver is {tr_d['direction']}, which should push the target {what}. The target moved {'that way' if ok else 'the other way or has no clear trend'}."
                                                                  if tr_t["significant"] else f"The driver is {tr_d['direction']}, which should push the target {what}, but the target has no significant trend.")})
    if smooth:
        # a steady drift has no ups and downs left after detrending, so the remaining tests cannot say anything about it
        return checks, (1 if checks[0]["ok"] else 0), "untestable"
    # 2 detrended correlation
    r, pa = c["r_det"], c.get("p_adj", c["p_det"])
    sign_ok = sign == 0 or (r > 0) == (sign > 0)
    ok2 = pa < 0.05 and sign_ok
    if pa >= 0.05:
        t2 = f"After removing each trend, the ups and downs do not match significantly (r = {r:+.2f}, adjusted {_pp(pa)})."
    elif not sign_ok:
        t2 = f"The ups and downs match (r = {r:+.2f}, adjusted {_pp(pa)}), but in the opposite direction to what the physics predicts."
    else:
        ml = c.get("match_lag", 0)
        t2 = f"After removing each trend, the ups and downs still match (r = {r:+.2f}, adjusted {_pp(pa)})" + (f", with the driver leading by {_unit_word(mode, ml)}." if ml else ".")
    checks.append({"ok": ok2, "name": "Year-to-year match", "text": t2})
    # 3 first differences
    rd_, pd_ = c["r_diff"], c["p_diff"]
    if rd_ is None:
        checks.append({"ok": False, "name": "Change-to-change match", "text": "Too few consecutive values to check this."})
    else:
        ok3 = pd_ < 0.05 and (sign == 0 or (rd_ > 0) == (sign > 0))
        checks.append({"ok": ok3, "name": "Change-to-change match", "text": f"A second way of removing trends (step-by-step changes) {'agrees' if ok3 else 'does not agree'} (r = {rd_:+.2f}, {_pp(pd_)})."})
    # 4 order in time
    gf, gr = c.get("granger_fwd_adj", c["granger_fwd"]), c["granger_rev"]
    if gf is None:
        checks.append({"ok": False, "name": "Driver comes first", "text": "Too few values for the lead test."})
    else:
        ok4 = gf < 0.05 and (gr is None or gr >= gf)
        if ok4:
            t4 = f"The driver's past helps predict the target (Granger, adjusted {_pp(gf)}), more than the other way round."
        elif gf < 0.05:
            t4 = f"The driver's past predicts the target ({_pp(gf)}), but the target predicts the driver just as well ({_pp(gr)}). The cause may run both ways."
        else:
            t4 = f"The driver's past does not predict the target beyond the target's own past (Granger {_pp(gf)})."
        checks.append({"ok": ok4, "name": "Driver comes first", "text": t4})
    return checks, sum(1 for k in checks if k["ok"]), "normal"


SMOOTH_R2 = 0.95
LABELS = {4: "Strongest support", 3: "Good support", 2: "Some support"}


def label_for(score: int, kind: str, checks: list) -> str:
    if kind == "untestable":
        return "Fits the physics, but this data cannot test it" if score else "Little or no support"
    var_ok = sum(1 for k in checks[1:] if k["ok"])
    if checks and not checks[0]["ok"] and (var_ok >= 2 or (len(checks) > 3 and checks[3]["ok"])):
        return "Explains the ups and downs, not the long-term trend"
    return LABELS.get(score, "Little or no support")


# ---------------------------------------------------------------- the whole analysis
def analyse_causes(place: dict, target: str, y0: int, m0: int, y1: int, m1: int, months: list, mode: str, getter=trends.get_json, text_getter=None) -> dict:
    text_getter = text_getter or TEXT_GETTER
    if target not in KB:
        raise ValueError("Possible causes are not available for this variable yet.")
    months = sorted({int(m) for m in months if 1 <= int(m) <= 12})
    if not months:
        raise ValueError("Choose at least one month of the year.")
    cell = trends.cell_of(place["lat"], place["lon"])
    info = trends.BY_ID[target]
    tm = trends.fetch_cell(cell, [target], y0, y1, getter)[target]
    tser = trends.build_series(tm, y0, m0, y1, m1, months, mode)
    need = 8 if mode == "year" else 24      # comparing two series needs more values than testing one trend
    tr_t = stats.analyse(tser["x"], tser["values"], info["name"], info["unit"], info["percent_ok"], seasons=tser["seasons"] if mode == "month" else None, min_n=need, rate=(10.0, "decade"))
    if tr_t is None:
        raise ValueError("Not enough data for the target variable in this period.")

    def load(entry):
        did, sign, mech = entry
        d = DRIVERS[did]
        rec = {"id": did, "name": d["name"], "unit": d["unit"], "from": d["from"], "sign": sign, "mechanism": mech, "error": None}
        try:
            src = d["source"]
            if src == "power":
                monthly = trends.fetch_cell(cell, [did], y0, y1, getter)[did]
            elif src == "co2":
                monthly = parse_co2(text_getter(CO2_URL))
            else:
                monthly = parse_oni(text_getter(ONI_URL))
            if not monthly:
                raise trends.DataError("No data came back for this driver at this place.")
            ser = trends.build_series(monthly, y0, m0, y1, m1, months, mode)
            rec["_ser"] = ser
            rec["trend"] = stats.analyse(ser["x"], ser["values"], d["name"], d["unit"], False, seasons=ser["seasons"] if mode == "month" else None, min_n=need, rate=(10.0, "decade"))
            if rec["trend"] is None:
                raise trends.DataError("Too few values for this driver in this period.")
        except Exception as e:      # noqa: BLE001
            rec["error"] = str(e)[:160]
        return rec

    with ThreadPoolExecutor(max_workers=3) as ex:
        recs = list(ex.map(load, [e for e in KB[target] if e[0] != target]))
    for r in recs:
        r["cmp"] = None
        if r["error"] is None:
            r["cmp"] = compare(tser, r["_ser"], mode)
            if r["cmp"] is None:
                r["error"] = "Too few months or years overlap with the target."
    ok = [r for r in recs if r["cmp"]]
    adj = benjamini_hochberg([r["cmp"]["p_det"] for r in ok])
    adj_g = benjamini_hochberg([r["cmp"]["granger_fwd"] for r in ok])
    for r, a, ag in zip(ok, adj, adj_g):
        r["cmp"]["p_adj"] = a
        r["cmp"]["granger_fwd_adj"] = ag
        r["checks"], r["score"], r["kind"] = judge(r, tr_t, r["trend"], r["cmp"], r["sign"], mode)
        r["label"] = label_for(r["score"], r["kind"], r["checks"])
        r["leads"] = len(r["checks"]) > 3 and r["checks"][3]["ok"]
    ok.sort(key=lambda r: (-r["score"] - (0.5 if r["kind"] == "untestable" and r["score"] else 0), -abs(r["cmp"]["r_det"])))
    best = best_explanation(tser, tr_t, ok, mode)
    causal_out = _causal_layer(tser, ok, mode)
    out_d = []
    for r in ok + [r for r in recs if not r["cmp"]]:
        c = r["cmp"]
        item = {"id": r["id"], "name": r["name"], "unit": r["unit"], "from": r["from"], "mechanism": r["mechanism"], "expected": {1: "more driver, more target", -1: "more driver, less target", 0: "either direction"}[r["sign"]], "error": r["error"]}
        if c:
            tr = r["trend"]
            item.update({"score": r["score"], "label": r["label"], "kind": r["kind"], "checks": r["checks"],
                         "may_be_feedback": bool(r["kind"] == "normal" and len(r["checks"]) > 1 and r["checks"][1]["ok"] and not r["leads"]), "n": c["n"], "r_raw": c["r_raw"], "r_det": c["r_det"], "p_det": c["p_det"], "p_adj": c["p_adj"],
                         "r_diff": c["r_diff"], "p_diff": c["p_diff"], "best_lag": c["best_lag"], "lead_text": lead_text(c, mode), "granger_fwd": c["granger_fwd"], "granger_rev": c["granger_rev"],
                         "may_be_effect": bool(c["granger_rev"] is not None and c["granger_rev"] < 0.05 and (c["granger_fwd"] is None or c["granger_fwd"] >= 0.05)),
                         "trend": {k: tr[k] for k in ("direction", "significant", "slope_decade", "p", "sentence", "verdict")},
                         "causal": (causal_out.get("by_driver") or {}).get(r["id"]),
                         "series": {"x": r["_ser"]["x"], "values": r["_ser"]["values"], "labels": r["_ser"]["labels"]}})
        out_d.append(item)
    return {"place": place, "target": {"id": target, "name": info["name"], "unit": info["unit"], "trend": {k: tr_t[k] for k in ("direction", "significant", "slope_decade", "p", "sentence", "verdict", "n")},
                                       "series": {"x": tser["x"], "values": tser["values"], "labels": tser["labels"]}},
            "drivers": out_d, "best": best, "mode": mode, "causal": {k: v for k, v in causal_out.items() if k != "by_driver"},
            "notes": ["This shows which candidates fit the target in the data and in physics. It is evidence of association, not proof of cause.",
                      "Drivers are read at the same place from the same model grid, so they carry the model's own limits."]}


def _causal_layer(tser: dict, ok: list, mode: str) -> dict:
    """PCMCI-style test and CCM on the detrended, deseasonalised series of the target and every usable driver."""
    if not ok:
        return {"available": False, "reason": "No usable driver."}
    try:
        def prep(ser):
            seas = ser["seasons"] if mode == "month" else None
            res = detrend(ser["x"], ser["values"], seas)
            return [_tindex(x, mode) for x in ser["x"]], res
        series = [prep(tser)] + [prep(r["_ser"]) for r in ok]
        return causal.run(series, ["__target__"] + [r["id"] for r in ok], mode)
    except Exception as e:      # noqa: BLE001 - the causal layer must never break the rest of the analysis
        return {"available": False, "reason": f"Could not run: {type(e).__name__}"}


def best_explanation(tser: dict, tr_t: dict, ranked: list, mode: str) -> dict:
    """Which testable drivers can explain part of the trend?
    Only drivers that (a) have their own significant trend, (b) have ups and downs to measure a sensitivity from, and (c) come before the target in time are used.
    Drivers that only move together with the target at the same time may be feedbacks (a result as well as a cause) and are not counted: they would double count.
    The sensitivity (target change per unit driver change) is measured on detrended data. Each driver's own trend is then multiplied by it."""
    fits = [{"id": r["id"], "name": r["name"]} for r in ranked if r["kind"] == "untestable" and r["score"]]
    out = {"drivers": [], "fits_direction": fits}
    if not tr_t["significant"] or abs(tr_t["slope_decade"]) < 1e-12:
        out["text"] = "The target has no significant trend, so there is no trend to explain."
        return out
    pick = []
    for r in ranked:
        if r["kind"] != "normal" or r["score"] < 3 or not r["trend"]["significant"] or not r["leads"] or not r["checks"][1]["ok"]:
            continue
        if any(abs(pearson(r["cmp"]["_rd"], q["cmp"]["_rd"])) > 0.85 for q in pick if len(q["cmp"]["_rd"]) == len(r["cmp"]["_rd"])):
            continue
        pick.append(r)
        if len(pick) == 3:
            break
    steady = " Steady drivers (such as CO2) cannot be split out here." if fits else ""
    if not pick:
        out["text"] = "No cause passes all checks, so no share can be given." + steady
        return out
    common = set(pick[0]["cmp"]["_ts"])
    for r in pick[1:]:
        common &= set(r["cmp"]["_ts"])
    ts = sorted(common)
    if len(ts) < (8 if mode == "year" else 24):
        out["text"] = "The candidates do not share enough time points to combine them." + steady
        return out
    col = []
    for r in pick:
        pos = {t: i for i, t in enumerate(r["cmp"]["_ts"])}
        col.append(([r["cmp"]["_rd"][pos[t]] for t in ts], [r["cmp"]["_dv"][pos[t]] for t in ts], [r["cmp"]["_xs"][pos[t]] for t in ts]))
    p0 = {t: i for i, t in enumerate(pick[0]["cmp"]["_ts"])}
    rt = [pick[0]["cmp"]["_rt"][p0[t]] for t in ts]
    tv = [pick[0]["cmp"]["_tv"][p0[t]] for t in ts]
    xs = col[0][2]
    try:
        beta, rss = solve_ls([[c[0][i] for c in col] for i in range(len(ts))], rt)
    except ValueError:
        out["text"] = "The candidates are too alike to separate." + steady
        return out
    sst = sum((v - _mean(rt)) ** 2 for v in rt)
    r2 = 1 - rss / sst if sst > 0 else 0.0
    items, modelled = [], [_mean(tv)] * len(ts)
    for j, r in enumerate(pick):
        b, slope_dec = beta[j + 1], r["trend"]["slope_decade"]
        dm = _mean(col[j][1])
        modelled = [m + b * (col[j][1][i] - dm) for i, m in enumerate(modelled)]
        items.append({"id": r["id"], "name": r["name"], "driver_unit": r["unit"], "sensitivity": b, "driver_trend_decade": slope_dec, "contribution_decade": b * slope_dec, "share": b * slope_dec / tr_t["slope_decade"]})
    total = sum(i["contribution_decade"] for i in items)
    share = total / tr_t["slope_decade"]
    out.update({"drivers": items, "explained_share": share, "r2": r2, "total_decade": total, "target_decade": tr_t["slope_decade"], "series": {"x": xs, "actual": tv, "modelled": modelled},
                "text": f"Together these drivers can account for about {share * 100:.0f}% of the target's trend ({total:+.3g} of {tr_t['slope_decade']:+.3g} per decade) and about {max(0.0, r2) * 100:.0f}% of its ups and downs once trends are removed. The rest is not explained by these candidates." + steady
                        + (" A share above 100% means the numbers are tangled together, so do not read it as an exact split." if share > 1.2 or share < -0.2 else "")})
    return out
