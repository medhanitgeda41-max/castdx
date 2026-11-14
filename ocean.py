"""Extra ocean series for one point. NASA POWER (in trends.py) is the main source; these three are optional extras that need no login:

  SST_OI    satellite sea surface temperature, NOAA OISST v2.1 (0.25 degree, daily, from Sept 1981), through NOAA's ERDDAP server
  SEALEVEL  sea level from the nearest tide gauge, PSMSL (Permanent Service for Mean Sea Level), monthly, many gauges back to the 1800s
  WAVE      daily largest wave height, WAVEP wave period: Open-Meteo Marine (ECMWF/Meteo-France wave models)

They were written from each service's documentation and could NOT be tested from where this was written.
If one fails, the app shows that row as "No data" with the reason and everything else keeps working. check_sources.py tests them.
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import time
from pathlib import Path

CACHE_DIR = Path.home() / ".rasd_trend_cache"
MIN_DAYS = 20
ERDDAP = "https://coastwatch.pfeg.noaa.gov/erddap/griddap/ncdcOisst21Agg_LonPM180.csv"
PSMSL_LIST = "https://psmsl.org/data/obtaining/rlr.monthly.data/filelist.txt"
PSMSL_DATA = "https://psmsl.org/data/obtaining/rlr.monthly.data/{id}.rlrdata"
MARINE = "https://marine-api.open-meteo.com/v1/marine"
MAX_GAUGE_KM = 250

# our id: (plain name, unit, percent change is meaningful, first year, where it comes from)
OC = {
    "SST_OI": ("Sea surface temperature (satellite)", "°C", False, 1982, "NOAA OISST v2.1 satellite sea surface temperature (NOAA ERDDAP)"),
    "SEALEVEL": ("Sea level (nearest tide gauge)", "mm", False, 1950, "PSMSL tide gauge, relative to land (includes land sinking or rising)"),
    "WAVE": ("Wave height (largest each day)", "m", True, 1980, "Open-Meteo Marine (ECMWF and Meteo-France wave models)"),
    "WAVEP": ("Wave period (largest each day)", "s", False, 1980, "Open-Meteo Marine (ECMWF and Meteo-France wave models)"),
}
OC_IDS = list(OC)
GROUP = "Extra sea data (not NASA POWER)"


class OceanError(Exception):
    pass


def http_get(url: str, params=None, timeout: int = 120):
    import requests
    last = None
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=timeout)
        except requests.RequestException as e:
            last = OceanError(f"could not reach {url.split('/')[2]}: {e}")
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code >= 500 and attempt < 2:
            last = OceanError(f"{url.split('/')[2]} answered {r.status_code}")
            time.sleep(2 * (attempt + 1))
            continue
        return r
    raise last or OceanError("no answer")


HTTP_GET = http_get      # tests replace this


def _monthly(pairs, min_days=MIN_DAYS):
    """{"YYYYMM": mean} from (date string, value) pairs; months with fewer than min_days values are dropped."""
    acc: dict = {}
    for d, v in pairs:
        if v is None or v != v:
            continue
        acc.setdefault(d[:4] + d[5:7], []).append(float(v))
    return {k: sum(v) / len(v) for k, v in acc.items() if len(v) >= min_days}


def _snap(x: float) -> float:
    return math.floor(x / 0.25) * 0.25 + 0.125      # centre of the 0.25 degree OISST cell


def _sea_cell(lat: float, lon: float, get) -> tuple:
    """The nearest 0.25 degree cell that is sea (not land), found from one day of data around the point."""
    la, lo = _snap(lat), _snap(((lon + 180) % 360) - 180)
    q = f"?sst[(2020-06-15T12:00:00Z):1:(2020-06-15T12:00:00Z)][(0.0):1:(0.0)][({la - 0.25}):1:({la + 0.25})][({lo - 0.25}):1:({lo + 0.25})]"
    r = get(ERDDAP + q)
    if r.status_code != 200:
        raise OceanError(f"NOAA ERDDAP answered {r.status_code}: {' '.join(r.text.split())[:100]}")
    best = None
    for line in r.text.splitlines()[2:]:
        c = line.split(",")
        if len(c) >= 5:
            try:
                x, y, v = float(c[3]), float(c[2]), float(c[4])
            except ValueError:
                continue
            if v == v:
                d = (x - lon) ** 2 + (y - lat) ** 2
                if best is None or d < best[0]:
                    best = (d, y, x)
    if not best:
        raise OceanError("no satellite sea temperature here (the point and its neighbours look like land, or ice). Pick a point in open water.")
    return best[1], best[2]


def fetch_sst(lat: float, lon: float, y0: int, y1: int, get) -> dict:
    a = max(y0, 1982)
    end = min(_dt.date(y1, 12, 31), _dt.date.today() - _dt.timedelta(days=10))
    la, lo = _sea_cell(lat, lon, get)
    q = f"?sst[({a}-01-01T12:00:00Z):1:({end.isoformat()}T12:00:00Z)][(0.0):1:(0.0)][({la}):1:({la})][({lo}):1:({lo})]"
    r = get(ERDDAP + q)
    if r.status_code != 200:
        raise OceanError(f"NOAA ERDDAP answered {r.status_code}: {' '.join(r.text.split())[:100]}")
    pairs = []
    for line in r.text.splitlines()[2:]:
        c = line.split(",")
        if len(c) >= 5 and c[0][:4].isdigit():
            try:
                pairs.append((c[0], float(c[4])))
            except ValueError:
                pass
    s = _monthly(pairs)
    if not s:
        raise OceanError("NOAA returned no sea temperature numbers for this point and period")
    return s


def _km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = math.sin((lat2 - lat1) * p / 2) ** 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(a))


def nearest_gauge(lat: float, lon: float, get) -> tuple:
    r = get(PSMSL_LIST)
    if r.status_code != 200:
        raise OceanError(f"PSMSL answered {r.status_code}")
    best = None
    for line in r.text.splitlines():
        c = [x.strip() for x in line.split(";")]
        if len(c) < 4:
            continue
        try:
            sid, la, lo = int(c[0]), float(c[1]), float(c[2])
        except ValueError:
            continue
        d = _km(lat, lon, la, lo)
        if best is None or d < best[0]:
            best = (d, sid, c[3])
    if not best:
        raise OceanError("could not read the PSMSL station list")
    return best


def fetch_sealevel(lat: float, lon: float, y0: int, y1: int, get) -> dict:
    d, sid, name = nearest_gauge(lat, lon, get)
    if d > MAX_GAUGE_KM:
        raise OceanError(f"no tide gauge within {MAX_GAUGE_KM} km (nearest: {name}, {d:.0f} km away)")
    r = get(PSMSL_DATA.format(id=sid))
    if r.status_code != 200:
        raise OceanError(f"PSMSL answered {r.status_code} for gauge {name}")
    out: dict = {}
    for line in r.text.splitlines():
        c = [x.strip() for x in line.split(";")]
        if len(c) < 2:
            continue
        try:
            yr, mm = float(c[0]), float(c[1])
        except ValueError:
            continue
        if mm <= -9990:
            continue
        y = int(yr)
        m = min(12, int((yr - y) * 12 + 1e-6) + 1)
        if y0 <= y <= y1:
            out[f"{y}{m:02d}"] = mm
    if not out:
        raise OceanError(f"gauge {name} ({d:.0f} km away) has no data in this period")
    SEALEVEL_NOTE.clear()
    SEALEVEL_NOTE.append(f"Sea level is from the {name} tide gauge, {d:.0f} km from your point.")
    return out


SEALEVEL_NOTE: list = []


def fetch_waves(lat: float, lon: float, y0: int, y1: int, get, vars_: list) -> dict:
    a = max(y0, 1980)
    end = min(_dt.date(y1, 12, 31), _dt.date.today() - _dt.timedelta(days=5))
    names = {"WAVE": "wave_height_max", "WAVEP": "wave_period_max"}
    r = get(MARINE, {"latitude": lat, "longitude": lon, "start_date": f"{a}-01-01", "end_date": end.isoformat(),
                     "daily": ",".join(names[v] for v in vars_), "timezone": "GMT"})
    try:
        j = r.json()
    except ValueError:
        raise OceanError(f"Open-Meteo Marine answered {r.status_code} with something that is not data")
    if r.status_code != 200 or j.get("error"):
        raise OceanError("Open-Meteo Marine: " + str(j.get("reason", r.status_code))[:120])
    t = j.get("daily", {}).get("time", [])
    out = {}
    for v in vars_:
        s = _monthly(zip(t, j["daily"].get(names[v], []) or []))
        if not s:
            raise OceanError("no wave data here (this point may be on land or far inland)")
        out[v] = s
    return out


def fetch_point(cell: tuple, ids: list, y0: int, y1: int, get=None) -> tuple:
    """Returns ({id: {"YYYYMM": value}}, {id: error text}). One failure does not stop the others."""
    get = get or HTTP_GET
    ids = [i for i in ids if i in OC]
    path = CACHE_DIR / f"oc_{cell[0]}_{cell[1]}_{y0}_{y1}.json"
    try:
        have = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        have = {}
    errs: dict = {}
    lat, lon = cell
    need = [i for i in ids if i not in have]
    for i in [x for x in need if x in ("SST_OI", "SEALEVEL")]:
        try:
            have[i] = (fetch_sst if i == "SST_OI" else fetch_sealevel)(lat, lon, y0, y1, get)
        except Exception as e:      # noqa: BLE001
            errs[i] = str(e)[:200]
    wv = [x for x in need if x in ("WAVE", "WAVEP")]
    if wv:
        try:
            have.update(fetch_waves(lat, lon, y0, y1, get, wv))
        except Exception as e:      # noqa: BLE001
            for i in wv:
                errs[i] = str(e)[:200]
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(have), encoding="utf-8")
    except OSError:
        pass
    return {i: have.get(i, {}) for i in ids}, errs
