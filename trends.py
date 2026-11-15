"""NASA POWER yearly series for any point on Earth, then the trend statistics from stats.py."""
from __future__ import annotations

import json
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import stats

POWER_URL = "https://power.larc.nasa.gov/api/temporal/monthly/point"
CACHE_DIR = Path.home() / ".rasd_trend_cache"
CELL = 0.5                      # the POWER grid is 0.5 x 0.625 degrees; places in the same cell share one download
FIRST_YEAR = 1981

# id, plain name, unit, group, where it comes from, percent change is meaningful
VARIABLES = [
    ("GWETROOT", "Root-zone soil wetness (top 1 m), 0 to 1 scale", "", "Soil", "MERRA-2 model (NASA GMAO); 0 is bone dry, 1 is saturated", False),
    ("GWETTOP", "Surface soil wetness (top 5 cm), 0 to 1 scale", "", "Soil", "MERRA-2 model (NASA GMAO); 0 is bone dry, 1 is saturated", False),
    ("GWETPROF", "Whole soil profile wetness, 0 to 1 scale", "", "Soil", "MERRA-2 model (NASA GMAO); 0 is bone dry, 1 is saturated", False),
    ("TS", "Sea surface temperature (NASA model)", "°C", "Sea and wind", "MERRA-2 model (NASA GMAO); skin temperature, which over open water is the sea surface", False),
    ("WS10M", "Wind speed at 10 m", "m/s", "Sea and wind", "MERRA-2 model (NASA GMAO)", True),
    ("WS50M", "Wind speed at 50 m", "m/s", "Sea and wind", "MERRA-2 model (NASA GMAO)", True),
    ("PS", "Air pressure at the surface", "kPa", "Sea and wind", "MERRA-2 model (NASA GMAO)", True),
    ("T2M", "Air temperature", "°C", "Air above the sea", "MERRA-2 model (NASA GMAO)", False),
    ("T2M_MAX", "Hottest daily air temperature", "°C", "Air above the sea", "MERRA-2 model (NASA GMAO)", False),
    ("T2M_MIN", "Coldest daily air temperature", "°C", "Air above the sea", "MERRA-2 model (NASA GMAO)", False),
    ("RH2M", "Air humidity (relative)", "%", "Air above the sea", "MERRA-2 model (NASA GMAO)", False),
    ("QV2M", "Water vapour in the air", "g/kg", "Air above the sea", "MERRA-2 model (NASA GMAO)", True),
    ("PRECTOTCORR", "Rain", "mm/day", "Rain and sky", "MERRA-2 corrected with satellite and gauge rain (NASA)", True),
    ("CLOUD_AMT", "Cloud cover", "%", "Sunlight and cloud", "NASA satellite cloud data (CERES/MERRA-2)", False),
    ("ALLSKY_SFC_SW_DWN", "Sunlight reaching the ground (all sky)", "kWh/m²/day", "Sunlight and cloud", "CERES satellite (NASA)", True),
    ("CLRSKY_SFC_SW_DWN", "Sunlight reaching the ground (clear sky, no clouds)", "kWh/m²/day", "Sunlight and cloud", "CERES satellite (NASA)", True),
    ("CLOUD_LOSS", "Sunlight blocked by clouds (clear sky minus all sky)", "kWh/m²/day", "Sunlight and cloud", "Worked out here from two NASA POWER series", True),
    ("ALLSKY_SFC_LW_DWN", "Heat radiated back down by the air", "W/m²", "Heat and light", "CERES satellite (NASA)", True),
    ("ALLSKY_SFC_PAR_TOT", "Light that plants use for growth (photosynthetically active radiation)", "W/m²", "Heat and light", "CERES satellite (NASA)", True),
    ("ALLSKY_SFC_UV_INDEX", "Ultraviolet index", "index", "Heat and light", "NASA POWER, where available", True),
    ("EVLAND", "Evaporation from land", "", "Soil water loss", "MERRA-2 model (NASA GMAO)", True),
    ("EVPTRNS", "Water given off by plants (transpiration)", "", "Soil water loss", "MERRA-2 model (NASA GMAO)", True),
    ("TSOIL1", "Soil temperature, top layer", "°C", "Soil water loss", "MERRA-2 model (NASA GMAO)", False),
]
import ocean  # noqa: E402

for _i, _g in ocean.OC.items():
    VARIABLES.append((_i, _g[0], _g[1], ocean.GROUP, _g[4], _g[2]))
BY_ID = {v[0]: dict(zip(("id", "name", "unit", "group", "source", "percent_ok"), v)) for v in VARIABLES}
for _i, _g in ocean.OC.items():
    BY_ID[_i]["first_year"] = _g[3]
# near-duplicates kept for the cause search but not offered as measures
# only the main sea characteristics are offered as measures; everything else is used as a possible cause
SOIL_SELECTABLE = ["GWETTOP", "GWETROOT", "GWETPROF"]
COAST_SELECTABLE = ["TS", "WS10M", "SEALEVEL", "WAVE", "WAVEP"]
SOLAR_SELECTABLE = ["ALLSKY_SFC_SW_DWN", "CLRSKY_SFC_SW_DWN", "CLOUD_LOSS", "CLOUD_AMT", "ALLSKY_SFC_LW_DWN", "ALLSKY_SFC_PAR_TOT", "ALLSKY_SFC_UV_INDEX"]
SELECTABLE = COAST_SELECTABLE + SOIL_SELECTABLE + SOLAR_SELECTABLE
DEFAULTS = {"coast": ["TS", "WS10M"], "soil": ["GWETTOP", "GWETROOT", "GWETPROF"], "solar": ["ALLSKY_SFC_SW_DWN", "CLRSKY_SFC_SW_DWN", "CLOUD_LOSS", "ALLSKY_SFC_LW_DWN", "ALLSKY_SFC_PAR_TOT"]}
for _i in BY_ID:
    BY_ID[_i]["domain"] = "soil" if _i in SOIL_SELECTABLE else "solar" if _i in SOLAR_SELECTABLE else "coast"
DEFAULT_SELECTION = ["TS", "WS10M"]
OCEAN_SELECTION = ["TS", "WS10M", "SEALEVEL", "WAVE", "WAVEP"]


class DataError(Exception):
    pass


def get_json(url: str, params: dict, timeout: int = 90) -> dict:
    import requests
    last = None
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=timeout)
        except requests.RequestException as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code != 200:
            last = DataError(f"power.larc.nasa.gov answered {r.status_code}: {r.text[:160]}")
            time.sleep(1.5 * (attempt + 1))
            continue
        return r.json()
    raise DataError(f"Could not reach NASA POWER: {last}")


def parse_power(js: dict, params: list) -> dict:
    """{param: {"YYYYMM": value}} monthly values from a POWER monthly answer. Month 13 (NASA's yearly value) is not used; -999 means missing."""
    par = ((js.get("properties") or {}).get("parameter")) or {}
    out = {}
    for p in params:
        out[p] = {k: float(v) for k, v in (par.get(p) or {}).items() if len(k) == 6 and k[4:] != "13" and v is not None and float(v) > -998}
        if p.startswith("TSOIL") and out[p] and statistics.fmean(out[p].values()) > 150:      # some NASA files give soil temperature in kelvin
            out[p] = {k: v - 273.15 for k, v in out[p].items()}
    return out


def cell_of(lat: float, lon: float) -> tuple:
    return (round(round(lat / CELL) * CELL, 2), round(round(lon / CELL) * CELL, 2))


def fetch_cell(cell: tuple, params: list, y0: int, y1: int, getter) -> dict:
    """Series for each parameter. Ocean extra ids go to NOAA, PSMSL or Open-Meteo (ocean.py), the rest to NASA POWER.
    The answer also holds "_errors": {id: text} for series that could not be downloaded."""
    derived = "CLOUD_LOSS" in params
    if derived:
        params = [p for p in params if p != "CLOUD_LOSS"] + [x for x in ("CLRSKY_SFC_SW_DWN", "ALLSKY_SFC_SW_DWN") if x not in params]
    g_ids = [p for p in params if p in ocean.OC]
    p_ids = [p for p in params if p not in ocean.OC]
    out: dict = {}
    if g_ids:
        gs, errs = ocean.fetch_point(cell, g_ids, y0, y1)
        out.update(gs)
        if errs:
            out["_errors"] = errs
            if not p_ids and not any(gs.values()):
                raise DataError("Sea data: " + "; ".join(sorted(set(errs.values()))))
    if p_ids:
        out.update(_fetch_power(cell, p_ids, y0, y1, getter))
    if derived:
        clear, allsky = out.get("CLRSKY_SFC_SW_DWN") or {}, out.get("ALLSKY_SFC_SW_DWN") or {}
        out["CLOUD_LOSS"] = {k: clear[k] - allsky[k] for k in clear if k in allsky}
    return out


def _fetch_power(cell: tuple, params: list, y0: int, y1: int, getter) -> dict:
    path = CACHE_DIR / f"power_{cell[0]}_{cell[1]}_{y0}_{y1}.json"
    have: dict = {}
    try:
        have = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        have = {}
    need = [p for p in params if p not in have]
    if need:
        for i in range(0, len(need), 15):
            part = need[i:i + 15]
            q = {"community": "AG", "longitude": cell[1], "latitude": cell[0], "start": y0, "end": y1, "format": "JSON"}
            try:
                have.update(parse_power(getter(POWER_URL, dict(q, parameters=",".join(part))), part))
            except Exception as first:      # noqa: BLE001
                # one rejected parameter name must not take the others down: ask for them one at a time
                got = 0
                for p in part:
                    try:
                        have.update(parse_power(getter(POWER_URL, dict(q, parameters=p)), [p]))
                        got += 1
                    except Exception:       # noqa: BLE001
                        continue
                if not got:
                    raise first
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(have), encoding="utf-8")
        except OSError:
            pass
    return {p: have.get(p, {}) for p in params}


POWER_DAILY_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
MAX_DAILY_YEARS = 5


def parse_power_daily(js: dict, params: list) -> dict:
    """{param: {"YYYYMMDD": value}} from a POWER daily answer. -999 means missing."""
    par = ((js.get("properties") or {}).get("parameter")) or {}
    return {p: {k: float(v) for k, v in (par.get(p) or {}).items() if len(k) == 8 and v is not None and float(v) > -998} for p in params}


def fetch_daily(cell: tuple, params: list, d0, d1, getter) -> dict:
    a, b = d0.strftime("%Y%m%d"), d1.strftime("%Y%m%d")
    path = CACHE_DIR / f"powerday_{cell[0]}_{cell[1]}_{a}_{b}.json"
    have: dict = {}
    try:
        have = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        have = {}
    need = [p for p in params if p not in have]
    for i in range(0, len(need), 15):
        part = need[i:i + 15]
        js = getter(POWER_DAILY_URL, {"parameters": ",".join(part), "community": "AG", "longitude": cell[1], "latitude": cell[0], "start": a, "end": b, "format": "JSON"})
        have.update(parse_power_daily(js, part))
    if need:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(have), encoding="utf-8")
        except OSError:
            pass
    return {p: have.get(p, {}) for p in params}


def build_series_daily(daily: dict, d0, d1) -> dict:
    from datetime import timedelta
    x, vals, labels = [], [], []
    d = d0
    while d <= d1:
        k = d.strftime("%Y%m%d")
        if k in daily:
            x.append(round(d.year + (d.timetuple().tm_yday - 0.5) / 365.25, 6))
            vals.append(daily[k])
            labels.append(d.isoformat())
        d += timedelta(days=1)
    return {"x": x, "values": vals, "labels": labels, "seasons": []}


MON = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTH_FULL = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def build_series(monthly: dict, y0: int, m0: int, y1: int, m1: int, months: list, mode: str) -> dict:
    """monthly = {"YYYYMM": value}. mode "year": one average per year, only for years where every chosen month is inside the period and has data.
    mode "month": every chosen month inside the period that has data."""
    inside = lambda y, m: (y0, m0) <= (y, m) <= (y1, m1)  # noqa: E731
    ms = sorted(set(months))
    x, vals, labels, seasons = [], [], [], []
    for y in range(y0, y1 + 1):
        if mode == "year":
            keys = [f"{y}{m:02d}" for m in ms]
            if all(inside(y, m) for m in ms) and all(k in monthly for k in keys):
                x.append(y)
                vals.append(sum(monthly[k] for k in keys) / len(keys))
                labels.append(str(y))
        else:
            for m in ms:
                k = f"{y}{m:02d}"
                if inside(y, m) and k in monthly:
                    x.append(round(y + (m - 0.5) / 12.0, 4))
                    vals.append(monthly[k])
                    labels.append(f"{MON[m]} {y}")
                    seasons.append(m)
    return {"x": x, "values": vals, "labels": labels, "seasons": seasons}


def period_text(y0, m0, y1, m1, months, mode) -> str:
    ms = sorted(set(months))
    which = "all months" if len(ms) == 12 else "only " + ", ".join(MON[m] for m in ms)
    return f"{MONTH_FULL[m0]} {y0} to {MONTH_FULL[m1]} {y1}, {which}, " + ("one average per year" if mode == "year" else "every month (seasonal test)")


def run(places: list, var_ids: list, y0: int, m0: int, y1: int, m1: int, months: list, mode: str, getter=get_json, d0=None, d1=None) -> dict:
    var_ids = [v for v in var_ids if v in BY_ID]
    if mode == "day":
        return run_daily(places, var_ids, d0, d1, getter)
    months = sorted({int(m) for m in months if 1 <= int(m) <= 12})
    if not var_ids:
        raise ValueError("Choose at least one variable.")
    if not places:
        raise ValueError("Add at least one place.")
    if not months:
        raise ValueError("Choose at least one month of the year.")
    if mode not in ("year", "month"):
        raise ValueError("Choose one value per year or every month.")
    span = (y1 * 12 + m1) - (y0 * 12 + m0) + 1
    if span < (stats.MIN_YEARS * 12 if mode == "year" else stats.MIN_MONTHS):
        raise ValueError(f"The period is too short. \"One value per year\" needs at least {stats.MIN_YEARS} years. \"Every month\" needs at least {stats.MIN_MONTHS} months.")
    cells = sorted({cell_of(p["lat"], p["lon"]) for p in places})
    data, errors, lock = {}, {}, threading.Lock()

    def one(c):
        try:
            r = fetch_cell(c, var_ids, y0, y1, getter)
            with lock:
                data[c] = r
        except Exception as e:      # noqa: BLE001
            with lock:
                errors[c] = str(e)[:160]
    with ThreadPoolExecutor(max_workers=3) as ex:
        list(ex.map(one, cells))
    out = []
    for p in places:
        c = cell_of(p["lat"], p["lon"])
        res = {}
        for v in var_ids:
            info = BY_ID[v]
            ser = build_series((data.get(c) or {}).get(v) or {}, y0, m0, y1, m1, months, mode)
            tr = stats.analyse(ser["x"], ser["values"], info["name"], info["unit"], info["percent_ok"], seasons=ser["seasons"] if mode == "month" else None,
                               min_n=stats.min_points(mode))
            res[v] = {"x": ser["x"], "values": ser["values"], "labels": ser["labels"], "trend": tr}
            why = ((data.get(c) or {}).get("_errors") or {}).get(v)
            if why:
                res[v]["error"] = why
        out.append({"name": p["name"], "lat": p["lat"], "lon": p["lon"], "vars": res, "error": errors.get(c)})
    return {"vars": [BY_ID[v] for v in var_ids], "places": out, "period": {"y0": y0, "m0": m0, "y1": y1, "m1": m1, "months": months, "mode": mode, "text": period_text(y0, m0, y1, m1, months, mode)},
            "failed": len(errors), "errors": sorted(set(errors.values()))[:3]}


def run_daily(places: list, var_ids: list, d0, d1, getter) -> dict:
    """Exact dates, one value per day. Strong day-to-day similarity is corrected for in the p-values."""
    var_ids = [v for v in var_ids if v not in ocean.OC and v != "CLOUD_LOSS"]
    if not var_ids:
        raise ValueError("Choose at least one variable.")
    if not places:
        raise ValueError("Add at least one place.")
    if d0 is None or d1 is None or d1 < d0:
        raise ValueError("Choose a start date that is before the end date.")
    days = (d1 - d0).days + 1
    if days < 8:
        raise ValueError("Daily mode needs at least 8 days.")
    if days > MAX_DAILY_YEARS * 366:
        raise ValueError(f"Daily mode covers up to {MAX_DAILY_YEARS} years. For longer periods use \"one value per year\" or \"every month\".")
    cells = sorted({cell_of(p["lat"], p["lon"]) for p in places})
    data, errors, lock = {}, {}, threading.Lock()

    def one(c):
        try:
            r = fetch_daily(c, var_ids, d0, d1, getter)
            with lock:
                data[c] = r
        except Exception as e:      # noqa: BLE001
            with lock:
                errors[c] = str(e)[:160]
    with ThreadPoolExecutor(max_workers=3) as ex:
        list(ex.map(one, cells))
    out = []
    for p in places:
        c = cell_of(p["lat"], p["lon"])
        res = {}
        for v in var_ids:
            info = BY_ID[v]
            ser = build_series_daily((data.get(c) or {}).get(v) or {}, d0, d1)
            tr = stats.analyse(ser["x"], ser["values"], info["name"], info["unit"], info["percent_ok"], min_n=8, autocorr=True)
            res[v] = {"x": ser["x"], "values": ser["values"], "labels": ser["labels"], "trend": tr}
        out.append({"name": p["name"], "lat": p["lat"], "lon": p["lon"], "vars": res, "error": errors.get(c)})
    text = f"{d0.strftime('%d %b %Y')} to {d1.strftime('%d %b %Y')}, every day (p-values corrected for day-to-day similarity)"
    return {"vars": [BY_ID[v] for v in var_ids], "places": out, "period": {"d0": d0.isoformat(), "d1": d1.isoformat(), "mode": "day", "text": text},
            "failed": len(errors), "errors": sorted(set(errors.values()))[:3]}


def csv_rows(res: dict) -> list:
    rows = [["place", "latitude", "longitude", "variable", "unit", "values_used", "first_value", "last_value", "mean",
             "change_per_rate_unit_theil_sen", "sen_ci95_low", "sen_ci95_high", "regression_slope_per_rate_unit", "regression_ci95_low", "regression_ci95_high",
             "t_statistic", "t_test_p", "mann_kendall_z", "mann_kendall_p", "kendall_tau", "total_change_over_period", "significant_at_95", "methods_agree", "source", "rate_unit"]]
    for a in res["places"]:
        for v in res["vars"]:
            t = a["vars"][v["id"]]["trend"]
            if not t:
                rows.append([a["name"], a["lat"], a["lon"], v["name"], v["unit"], 0] + [""] * 15 + ["no data", "", v["source"], ""])
                continue
            r = lambda x, n=4: "" if x is None else round(x, n)  # noqa: E731
            rows.append([a["name"], a["lat"], a["lon"], v["name"], v["unit"], t["n"], t["first"], t["last"], r(t["mean"]), r(t["sen"]["slope_decade"]), r(t["sen"]["ci_low"]), r(t["sen"]["ci_high"]),
                         r(t["ols"]["slope_decade"]), r(t["ols"]["ci_low"]), r(t["ols"]["ci_high"]), r(t["ols"]["t"], 3), r(t["ols"]["p"], 5), r(t["mk"]["z"], 3), r(t["mk"]["p"], 5),
                         r(t["mk"]["tau"], 3), r(t["total"]), "yes" if t["significant"] else "no", "yes" if t["agree"] else "no", v["source"], t["rate_label"]])
    return rows


def series_csv_rows(res: dict) -> list:
    rows = [["place", "variable", "unit", "period", "value"]]
    for a in res["places"]:
        for v in res["vars"]:
            d = a["vars"][v["id"]]
            for y, x in zip(d["labels"], d["values"]):
                rows.append([a["name"], v["name"], v["unit"], y, round(x, 5)])
    return rows
