"""Extra series from Meteomatics that NASA POWER and Open-Meteo do not already give (soil water shortage, swell height) (https://api.meteomatics.com). Optional.

The username and password are read ONLY from two environment variables:
    METEOMATICS_USER    METEOMATICS_PASS
They are never written in any file. If either one is missing, this whole feature stays switched off and the app works as before.

Written from Meteomatics' public description of its API and NOT tested against the live service from where it was written:
  - the web address form  /<start>--<end>:P1D/<parameter>/<lat>,<lon>/json
  - the answer shape      {"data":[{"parameter":..,"coordinates":[{"dates":[{"date":..,"value":..}]}]}]}
  - which years your account may read (history depth) and how many requests it allows
If something differs, the app shows "No data" for that row with the reason, and every other row keeps working.
check_sources.py can test this part once the two variables are set.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import time
from pathlib import Path

import ocean

BASE = "https://api.meteomatics.com"
CACHE_DIR = Path.home() / ".rasd_trend_cache"
CHUNK_YEARS = 3          # one request covers three years of daily values

# our id: (plain name, unit, percent change is meaningful, first year to try, Meteomatics parameter, kind)
MM = {
    "MM_SMD": ("Soil moisture deficit (Meteomatics)", "mm", False, 1990, "soil_moisture_deficit:mm", "soil"),
    "MM_SWELL": ("Swell height, average (Meteomatics)", "m", True, 1990, "significant_height_total_swell:m", "sea"),
}
MM_IDS = list(MM)
SOIL_IDS = [i for i, v in MM.items() if v[5] == "soil"]
SEA_IDS = [i for i, v in MM.items() if v[5] == "sea"]
GROUP = {"soil": "Extra soil data (Meteomatics)", "sea": "Extra sea data (Meteomatics)"}
SOURCE = "Meteomatics weather API (needs the account in METEOMATICS_USER and METEOMATICS_PASS)"


class MeteomaticsError(Exception):
    pass


def enabled() -> bool:
    return bool(os.environ.get("METEOMATICS_USER") and os.environ.get("METEOMATICS_PASS"))


def http_get(url: str, timeout: int = 120):
    import requests
    auth = (os.environ.get("METEOMATICS_USER", ""), os.environ.get("METEOMATICS_PASS", ""))
    last = None
    for attempt in range(3):
        try:
            r = requests.get(url, auth=auth, timeout=timeout)
        except requests.RequestException as e:
            # the message is cut to the host name so no address with details is shown
            last = MeteomaticsError("could not reach Meteomatics (" + type(e).__name__ + ")")
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code >= 500 and attempt < 2:
            last = MeteomaticsError(f"Meteomatics answered {r.status_code}")
            time.sleep(2 * (attempt + 1))
            continue
        return r
    raise last or MeteomaticsError("no answer")


HTTP_GET = http_get      # tests replace this


def _why(status: int, text: str) -> str:
    if status in (401, 403):
        return "Meteomatics refused the login or this data (check METEOMATICS_USER, METEOMATICS_PASS and the account's data rights)"
    if status == 404:
        return "Meteomatics has no such data for this place or period"
    if status == 429:
        return "Meteomatics request limit reached (try later)"
    return f"Meteomatics answered {status}: " + " ".join(text.split())[:100]


def parse(js: dict) -> list:
    """[(date text YYYY-MM-DD, value)] from the Meteomatics JSON answer."""
    pairs = []
    for block in js.get("data") or []:
        for co in block.get("coordinates") or []:
            for d in co.get("dates") or []:
                pairs.append((str(d.get("date", ""))[:10], d.get("value")))
    return pairs


def _fetch_one(lat: float, lon: float, y0: int, y1: int, pid: str, get) -> dict:
    param = MM[pid][4]
    pairs, bad = [], []
    last_year = min(y1, _dt.date.today().year)
    y = max(y0, MM[pid][3])
    while y <= last_year:
        e = min(y + CHUNK_YEARS - 1, last_year)
        end = _dt.date(e, 12, 31)
        end = min(end, _dt.date.today() - _dt.timedelta(days=1))
        url = f"{BASE}/{y}-01-01T00:00:00Z--{end.isoformat()}T00:00:00Z:P1D/{param}/{lat},{lon}/json"
        try:
            r = get(url)
            if r.status_code != 200:
                raise MeteomaticsError(_why(r.status_code, r.text))
            pairs += parse(r.json())
        except Exception as ex:      # noqa: BLE001
            bad.append(str(ex)[:160])
            if "refused the login" in bad[-1] or "limit reached" in bad[-1]:
                break                # no point asking again
        y = e + 1
    if not pairs:
        raise MeteomaticsError(bad[0] if bad else "no values returned")
    m = ocean._monthly(pairs)
    if not m:
        raise MeteomaticsError("too few daily values to make monthly means")
    return m


def fetch_point(cell: tuple, ids: list, y0: int, y1: int, get=None) -> tuple:
    """Returns ({id: {"YYYYMM": value}}, {id: error text}). One failure does not stop the others."""
    ids = [i for i in ids if i in MM]
    errs: dict = {}
    if not enabled() and get is None:
        return {i: {} for i in ids}, {i: "Meteomatics is not set up (no METEOMATICS_USER and METEOMATICS_PASS)" for i in ids}
    get = get or HTTP_GET
    path = CACHE_DIR / f"mm_{cell[0]}_{cell[1]}_{y0}_{y1}.json"      # monthly values only, never the login
    try:
        have = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        have = {}
    lat, lon = cell
    for i in [x for x in ids if x not in have]:
        try:
            have[i] = _fetch_one(lat, lon, y0, y1, i, get)
        except Exception as e:      # noqa: BLE001
            errs[i] = str(e)[:200]
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(have), encoding="utf-8")
    except OSError:
        pass
    return {i: have.get(i, {}) for i in ids}, errs
