"""Climate model projections from the Open-Meteo Climate API (https://climate-api.open-meteo.com/v1/climate).

Seven high-resolution CMIP6 models are asked at once (the more of them that answer, the better the spread shows):
CMCC_CM2_VHR4, FGOALS_f3_H, HiRAM_SIT_HR, MRI_AGCM3_2_S, EC_Earth3P_HR, MPI_ESM1_2_XR, NICAM16_8S.
The person picks a year (2026 to 2050; these models stop at 2050). For each variable we compare the mean of the 11 years around that year
with each model's own 1991 to 2020 mean. The change is then also put on top of the latest observed level of the person's own measure
(wind, surface soil wetness) so the answer starts from real data, not from a model's own bias.

Written from the Open-Meteo documentation and tested only on simulated answers (the workspace could not reach the service).
Known from the documentation: soil moisture exists in only 2 of the 7 models, some models lack some wind or humidity fields,
and the free service has a daily limit, so answers are cached on disk.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

URL = "https://climate-api.open-meteo.com/v1/climate"
CACHE_DIR = Path.home() / ".rasd_trend_cache"
MODELS = ["CMCC_CM2_VHR4", "FGOALS_f3_H", "HiRAM_SIT_HR", "MRI_AGCM3_2_S", "EC_Earth3P_HR", "MPI_ESM1_2_XR", "NICAM16_8S"]
BASE_Y0, BASE_Y1 = 1991, 2020
LAST_YEAR = 2050
HALF = 5                                    # the target is the mean of the years year-5 to year+5

# id: (plain name, unit shown, API name, factor to the shown unit, how the change is shown, which sections)
VARS = {
    "wind": ("Wind speed at 10 m", "m/s", "wind_speed_10m_mean", 1 / 3.6, "percent", ("coast", "soil")),
    "temp": ("Air temperature", "°C", "temperature_2m_mean", 1.0, "add", ("coast", "soil")),
    "rain": ("Rain", "mm/day", "precipitation_sum", 1.0, "percent", ("coast", "soil")),
    "soil": ("Surface soil water (0 to 10 cm)", "m³/m³", "soil_moisture_0_to_10cm_mean", 1.0, "percent", ("soil",)),
}
ORDER = {"coast": ["wind", "temp", "rain"], "soil": ["soil", "rain", "temp", "wind"]}
# the person's own measure that each projection can be put on top of: measure id -> projection id
APPLY = {"WS10M": "wind", "GWETTOP": "soil"}


def window(year: int) -> tuple:
    """The 11 years around `year`, moved inside 2021 to 2050 when the year is near an end."""
    a, b = year - HALF, year + HALF
    if b > LAST_YEAR:
        a, b = LAST_YEAR - 2 * HALF, LAST_YEAR
    if a <= BASE_Y1:
        a, b = BASE_Y1 + 1, BASE_Y1 + 1 + 2 * HALF
    return a, b


class ClimateError(Exception):
    pass


def http_get(url: str, params: dict, timeout: int = 120):
    import requests
    last = None
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=timeout)
        except requests.RequestException as e:
            last = ClimateError("could not reach Open-Meteo climate service (" + type(e).__name__ + ")")
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code >= 500 and attempt < 2:
            last = ClimateError(f"Open-Meteo answered {r.status_code}")
            time.sleep(2 * (attempt + 1))
            continue
        return r
    raise last or ClimateError("no answer")


HTTP_GET = http_get      # tests replace this


def _yearly(js: dict, api_name: str, factor: float) -> dict:
    """{model: {year: mean of that year}} from one answer. A year needs 350 or more daily values."""
    d = js.get("daily") or {}
    t = d.get("time") or []
    out = {}
    for m in MODELS:
        vals = d.get(f"{api_name}_{m}")
        if vals is None and len(MODELS) == 1:
            vals = d.get(api_name)
        if not vals:
            continue
        acc: dict = {}
        for day, v in zip(t, vals):
            if v is None or v != v:
                continue
            acc.setdefault(int(day[:4]), []).append(float(v) * factor)
        ys = {y: sum(v) / len(v) for y, v in acc.items() if len(v) >= 350}
        if ys:
            out[m] = ys
    return out


def _ask(lat: float, lon: float, vid: str, a: int, b: int, get) -> dict:
    name, _u, api, factor = VARS[vid][0], VARS[vid][1], VARS[vid][2], VARS[vid][3]
    r = get(URL, {"latitude": lat, "longitude": lon, "start_date": f"{a}-01-01", "end_date": f"{b}-12-31",
                  "models": ",".join(MODELS), "daily": api, "timezone": "GMT"})
    try:
        js = r.json()
    except ValueError:
        raise ClimateError(f"Open-Meteo answered {r.status_code} with something that is not data")
    if r.status_code != 200 or js.get("error"):
        raise ClimateError("Open-Meteo climate: " + str(js.get("reason", r.status_code))[:140])
    ys = _yearly(js, api, factor)
    if not ys:
        raise ClimateError(f"no model returned {name.lower()} for this place")
    return ys


def yearly(lat: float, lon: float, vid: str, year: int, get=None) -> dict:
    """Yearly means per model for the 1991 to 2020 baseline and the window around `year`. Cached on disk (numbers only)."""
    get = get or HTTP_GET
    lat, lon = round(lat * 4) / 4, round(lon * 4) / 4
    w0, w1 = window(year)
    path = CACHE_DIR / f"clim_{lat}_{lon}_{vid}_{w0}_{w1}.json"
    try:
        have = {m: {int(y): v for y, v in d.items()} for m, d in json.loads(path.read_text(encoding="utf-8")).items()}
        return have
    except (OSError, ValueError):
        pass
    base = _ask(lat, lon, vid, BASE_Y0, BASE_Y1, get)
    tgt = _ask(lat, lon, vid, w0, w1, get)
    out = {m: {**base.get(m, {}), **tgt.get(m, {})} for m in set(base) | set(tgt)}
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out), encoding="utf-8")
    except OSError:
        pass
    return out


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def project_one(lat: float, lon: float, vid: str, year: int, get=None) -> dict:
    name, unit, _api, _f, kind = VARS[vid][:5]
    ys = yearly(lat, lon, vid, year, get)
    w0, w1 = window(year)
    rows = []
    for m in MODELS:
        d = ys.get(m)
        if not d:
            continue
        b = _mean([d.get(y) for y in range(BASE_Y0, BASE_Y1 + 1)])
        t = _mean([d.get(y) for y in range(w0, w1 + 1)])
        if b is None or t is None:
            continue
        ch = (t - b) if kind == "add" else ((t / b - 1) * 100 if b else None)
        if ch is None:
            continue
        rows.append({"model": m, "base": b, "target": t, "change": ch})
    if not rows:
        raise ClimateError(f"no model had enough years of {name.lower()}")
    chs = [r["change"] for r in rows]
    return {"id": vid, "name": name, "unit": unit, "kind": kind, "year": year, "window": [w0, w1], "base_years": [BASE_Y0, BASE_Y1],
            "rows": rows, "n": len(rows), "n_asked": len(MODELS), "mean": sum(chs) / len(chs), "low": min(chs), "high": max(chs),
            "agree": sum(1 for c in chs if c > 0) if len(chs) else 0,
            "few": len(rows) < 5}


def project(lat: float, lon: float, year: int, domain: str, get=None) -> dict:
    year = int(year)
    if year < BASE_Y1 + 1 or year > LAST_YEAR:
        raise ValueError(f"Choose a year from {BASE_Y1 + 6} to {LAST_YEAR}. These models end in {LAST_YEAR}.")
    out, errs = [], {}
    for vid in ORDER["soil" if domain == "soil" else "coast"]:
        try:
            out.append(project_one(lat, lon, vid, year, get))
        except Exception as e:      # noqa: BLE001
            errs[vid] = str(e)[:160]
    if not out:
        raise ClimateError("; ".join(sorted(set(errs.values()))) or "no answer")
    return {"year": year, "items": out, "errors": errs, "models": MODELS, "apply": APPLY,
            "note": "Model projections, not forecasts. Each model is one possible future under the same assumed emissions. The spread between models shows how unsure the answer is."}
