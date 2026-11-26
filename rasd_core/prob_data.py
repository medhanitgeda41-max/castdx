"""Getting the data and turning it into monthly probabilities for a list of assets.

  Assets      .csv / .xlsx files in the same layout RASD reads (columns are found by normalize.py: name, latitude, longitude,
              asset value ...). Several files can be added; the same asset in two files is merged.
  Weather     daily rain and maximum temperature: from weather cells already saved on this PC (om_*.json, made by
              make_input_dataset.py), or from Open-Meteo on the internet (ERA5, up to 30 years).
  Climate     for a future year only: several Open-Meteo climate models (EC_Earth3P_HR, MPI_ESM1_2_XR, CMCC_CM2_VHR4, MRI_AGCM3_2_S, FGOALS_f3_H); their average change since now is used.
  Earthquakes the USGS catalogue saved in data/quakes_region.json (can be refreshed from the internet).

Nothing is invented: when a lookup fails the asset says so, instead of falling back to made-up numbers.
"""
from __future__ import annotations

import csv
import glob
import io
import json
import math
import os
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path

import combine
import damage
import geo
import hazard_prob as hp
import normalize

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
CACHE_DIR = Path.home() / ".rasd_prob_cache"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
CLIMATE_URL = "https://climate-api.open-meteo.com/v1/climate"
CLIMATE_MODELS = ["EC_Earth3P_HR", "MPI_ESM1_2_XR", "CMCC_CM2_VHR4", "MRI_AGCM3_2_S", "FGOALS_f3_H"]     # Open-Meteo climate API models; the change is the average of those that answer
USGS_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
RAIN_YEARS = 30                               # years of history behind the flood chance
HEAT_YEARS = 15                               # years behind the heat-wave chance (recent climate)
DEFAULT_VALUE = 1_000_000
SOURCES = {"Saved cells first, then the internet": "saved_first", "Internet only": "internet", "Saved cells only (no internet)": "saved_only"}


class DataError(Exception):
    """A message that is meant to be read by the person."""


def current_year() -> int:
    return date.today().year


# ------------------------------------------------------------------ asset files

def read_rows(path: str) -> list[dict]:
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xlsm"):
        import openpyxl
        book = openpyxl.load_workbook(p, read_only=True, data_only=True)
        it = book.active.iter_rows(values_only=True)
        header = [str(h).strip() if h is not None else "" for h in next(it, [])]
        rows = [dict(zip(header, r)) for r in it if any(v not in (None, "") for v in r)]
        book.close()
    else:
        text = p.read_bytes().decode("utf-8-sig", errors="replace")
        rows = [dict(r) for r in csv.DictReader(io.StringIO(text))]
    return [{k: v for k, v in r.items() if k} for r in rows]


def _num(v):
    try:
        x = float(str(v).replace(",", "").strip())
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _name_of(original: dict, normalized: dict, index: int) -> str:
    for key in ("asset_name", "asset_id"):
        if normalized.get(key) not in (None, ""):
            return str(normalized[key]).strip()
    for key, value in original.items():
        if "name" in str(key).lower() and str(value).strip():
            return str(value).strip()
    for value in original.values():
        if isinstance(value, str) and value.strip() and not re.fullmatch(r"[-\d.,\s]+", value.strip()):
            return value.strip()
    return f"Asset {index}"


VALUE_COLUMNS = ("collateral_value_aed", "asset_value_aed", "net_book_value_aed", "cost_aed", "sum_insured_aed")
UNPAID_FIRST = ("unpaid_aed", "unpaid_balance_aed", "unpaid_amount_aed", "remaining_balance_aed", "remaining_principal_aed")
ORIGINAL_COLUMNS = ("loan_amount_aed", "original_loan_aed", "original_amount_aed", "sanctioned_amount_aed", "principal_aed", "facility_amount_aed")
REPAID_COLUMNS = ("repaid_aed", "amount_repaid_aed", "paid_aed", "principal_repaid_aed", "repayments_aed", "amount_paid_aed")
OTHER_INSURER = ("other_insurers_cover_aed", "other_insurers_coverage_aed", "co_insurance_aed", "reinsurance_cover_aed", "other_insurers_aed")
ADAPT_COLUMNS = {
    "flood": ("flood_adaptation_status", "adaptation_status_flood", "flood_adaptation", "flood_protection_status", "flood_protection", "flood_mitigation",
              "flood_mitigation_status", "flood_measures", "flood_resilience", "flood_defence_status", "flood_defense_status", "flood_adaptation_level",
              "flood_adaptation_measure", "flood_adaptation_pct", "flood_adaptation_percent", "flood_control_status"),
    "heat": ("heat_adaptation_status", "adaptation_status_heat", "heat_adaptation", "heat_protection_status", "heat_protection", "heat_mitigation",
             "heat_mitigation_status", "heat_measures", "heat_resilience", "heat_adaptation_level", "heat_adaptation_measure", "heat_adaptation_pct",
             "heat_adaptation_percent", "cooling_status", "heatwave_adaptation", "heat_wave_adaptation", "extreme_heat_adaptation"),
    "quake": ("earthquake_adaptation_status", "adaptation_status_earthquake", "earthquake_adaptation", "seismic_adaptation_status", "seismic_adaptation",
              "quake_adaptation", "quake_adaptation_status", "earthquake_protection", "earthquake_protection_status", "seismic_retrofit", "seismic_retrofit_status",
              "earthquake_retrofit", "earthquake_mitigation", "earthquake_resilience", "earthquake_adaptation_level", "earthquake_adaptation_pct",
              "earthquake_adaptation_percent", "seismic_protection", "seismic_resilience", "seismic_mitigation")}
PERIL_WORDS = {"flood": "flood", "heat": "heat", "extreme heat": "heat", "earthquake": "quake", "quake": "quake", "seismic": "quake"}


def _pick(row: dict, *names):
    wanted = {re.sub(r"[^a-z0-9]", "", n.lower()) for n in names}
    for k, v in row.items():
        if re.sub(r"[^a-z0-9]", "", str(k).lower()) in wanted and v not in (None, ""):
            return v
    return None


def _unpaid(row: dict):
    for n in UNPAID_FIRST:
        v = _num(_pick(row, n))
        if v is not None:
            return max(v, 0.0)
    o = next((v for v in (_num(_pick(row, c)) for c in ORIGINAL_COLUMNS) if v is not None), None)
    r = next((v for v in (_num(_pick(row, c)) for c in REPAID_COLUMNS) if v is not None), None)
    if o is not None and r is not None:
        return max(o - r, 0.0)
    v = _num(_pick(row, "outstanding_aed", "outstanding_balance_aed"))
    return None if v is None else max(v, 0.0)


def _perils(row: dict):
    """A set of {flood, heat, quake} the policy pays, or None (= all)."""
    text = str(_pick(row, "perils_covered", "perils") or "").strip().lower()
    if not text:
        return None
    found = {k for w, k in PERIL_WORDS.items() if w in text}
    if found:
        return found
    if any(w in text for w in ("all", "comprehensive", "natural", "catastroph", "nat cat")):
        return None
    return set()


def assets_from_rows(rows: list[dict], default_value: float = DEFAULT_VALUE) -> tuple[list[dict], list[str]]:
    """Assets (with the money and building facts the risk and finance tabs need) from the combined rows."""
    notes: list[str] = []
    normalized, report = normalize.normalize_rows(rows, None)
    for w in report.get("warnings", []):
        notes.append(w)
    found: dict = {}
    skipped = 0
    value_missing = 0
    for i, (orig, norm_) in enumerate(zip(rows, normalized), start=1):
        lat, lon = _num(norm_.get("latitude")), _num(norm_.get("longitude"))
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            skipped += 1
            continue
        name = _name_of(orig, norm_, i)
        aid = str(_pick(norm_, "asset_id", "collateral_asset_id", "site_id") or "")
        key = (aid or name.lower(), round(lat, 4), round(lon, 4))
        value = next((v for v in (_num(_pick(norm_, c)) for c in VALUE_COLUMNS) if v), None)
        item = found.setdefault(key, {"id": aid, "name": name, "lat": lat, "lon": lon, "value_aed": None, "material": None, "adaptation": 0.0,
                                      "unpaid_aed": None, "sum_insured_aed": None, "deductible_aed": 0.0, "other_insurers_aed": 0.0, "premium_year_aed": None,
                                      "perils": None, "adapt": {"flood": 0.0, "heat": 0.0, "quake": 0.0}, "row": norm_})
        if item["value_aed"] is None and value:
            item["value_aed"] = value
        material = norm_.get("asset_type") or None
        if material and not item["material"]:
            item["material"] = str(material)
        general = damage.adaptation_level(norm_.get("adaptation_status"))
        for hz, names in ADAPT_COLUMNS.items():          # a column for one hazard wins over the general adaptation status
            raw = _pick(orig, *names)
            level = damage.adaptation_level(raw) if raw is not None else general
            item["adapt"][hz] = max(item["adapt"][hz], level)
        item["adaptation"] = max(item["adaptation"], general, item["adapt"]["flood"], item["adapt"]["heat"])
        for field, val in (("unpaid_aed", _unpaid(norm_)), ("sum_insured_aed", _num(_pick(norm_, "sum_insured_aed"))),
                           ("deductible_aed", _num(_pick(norm_, "deductible_aed"))),
                           ("premium_year_aed", _num(_pick(norm_, "annual_premium_aed"))),
                           ("other_insurers_aed", _num(_pick(norm_, *OTHER_INSURER)))):
            if val is not None and not item[field]:
                item[field] = val
        pr = _perils(norm_)
        if pr is not None:
            item["perils"] = pr
    if skipped:
        notes.append(f"{skipped} row(s) left out because they have no latitude and longitude.")
    assets = []
    for item in found.values():
        item["cell"] = (round(item["lat"], 1), round(item["lon"], 1))
        if item["value_aed"] is None:
            item["value_aed"] = float(default_value)
            value_missing += 1
        assets.append(item)
    if value_missing:
        notes.append(f"{value_missing} asset(s) have no asset value in the files, so AED {default_value:,.0f} was assumed for them.")
    names: dict = {}
    for a in assets:                     # two different assets with the same name get a number, so they can be told apart
        names[a["name"]] = names.get(a["name"], 0) + 1
        if names[a["name"]] > 1:
            a["name"] = f"{a['name']} ({names[a['name']]})"
    return assets, notes


def load_files(paths: list[str], default_value: float = DEFAULT_VALUE):
    """Read, combine (see combine.py) and turn into assets. Returns (assets, combined_rows, steps, warnings)."""
    files = [(Path(p).name, read_rows(p)) for p in paths]
    rows, steps, warnings = combine.combine_files(files)
    if not rows:
        return [], [], steps, warnings
    assets, notes = assets_from_rows(rows, default_value)
    return assets, rows, steps, warnings + notes


def load_assets(paths: list[str], default_value: float = DEFAULT_VALUE):
    assets, _, _, notes = load_files(paths, default_value)
    return assets, notes


# ------------------------------------------------------------------ weather

def _series(times, tmax, rain) -> dict:
    return {"time": list(times), "tmax": list(tmax), "rain": list(rain)}


class SavedCells:
    """Weather cells already on this PC: om_<lat>_<lon>_<start>_<end>.json (one per 0.1 degree cell)."""

    def __init__(self, folder: str | None):
        self.folder = folder
        self.index: dict = {}
        if folder and os.path.isdir(folder):
            for path in sorted(glob.glob(os.path.join(folder, "om_*.json"))):
                m = re.match(r"om_(-?[\d.]+)_(-?[\d.]+)_", os.path.basename(path))
                if m:
                    self.index[(float(m.group(1)), float(m.group(2)))] = path       # the newest file of a cell wins

    def find(self, cell: tuple, max_deg: float = 0.15):
        if cell in self.index:
            return cell, self.index[cell]
        near = [(abs(c[0] - cell[0]) + abs(c[1] - cell[1]), c) for c in self.index
                if abs(c[0] - cell[0]) <= max_deg and abs(c[1] - cell[1]) <= max_deg]
        if near:
            c = min(near)[1]
            return c, self.index[c]
        return None

    @staticmethod
    def load(path: str) -> dict:
        d = json.load(open(path, encoding="utf-8"))
        d = d.get("daily", d)
        return _series(d["time"], d["tmax"], d["rain"])


def guess_cell_folder() -> str | None:
    """Where the saved weather cells probably are: next to the app, or in the RASD folders under Downloads."""
    home = Path.home()
    candidates = [APP_DIR / "weather_cells", APP_DIR / "input_dataset_output" / "cache", APP_DIR.parent / "input_dataset_output" / "cache"]
    for pattern in ("Downloads/RASD*/input_dataset_output/cache", "Downloads/RASD*/*/input_dataset_output/cache",
                    "Downloads/RASD*/*/*/input_dataset_output/cache", "Downloads/RASD*/*/*/*/input_dataset_output/cache"):
        candidates += [Path(p) for p in sorted(glob.glob(str(home / pattern)))]
    for c in candidates:
        if c.is_dir() and any(c.glob("om_*.json")):
            return str(c)
    return None


def _get_json(url: str, params: dict, timeout: int = 60) -> dict:
    import requests
    last = None
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=timeout)
        except requests.RequestException as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code == 429 or "limit exceeded" in r.text[:300].lower():
            raise DataError("Open-Meteo's free limit is used up for now (it resets each day). Use 'Saved cells only', or try again later.")
        if r.status_code != 200:
            last = DataError(f"{url.split('/')[2]} answered {r.status_code}: {r.text[:160]}")
            time.sleep(1.5 * (attempt + 1))
            continue
        return r.json()
    raise DataError(f"Could not reach {url.split('/')[2]}: {last}")


def _cached_json(name: str, fetch):
    path = CACHE_DIR / name
    if path.exists():
        try:
            return json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            pass
    data = fetch()
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        json.dump(data, open(path, "w", encoding="utf-8"))
    except OSError:
        pass
    return data


def live_history(cell: tuple) -> dict:
    """Daily rain and maximum temperature of the cell for the last RAIN_YEARS full years (Open-Meteo, ERA5)."""
    last = current_year() - 1
    first = last - RAIN_YEARS + 1

    def fetch():
        d = _get_json(ARCHIVE_URL, {"latitude": cell[0], "longitude": cell[1], "start_date": f"{first}-01-01",
                                    "end_date": f"{last}-12-31", "daily": "temperature_2m_max,precipitation_sum", "timezone": "UTC"})
        daily = d.get("daily", {})
        if not daily.get("time"):
            raise DataError("Open-Meteo returned no weather for this place.")
        return _series(daily["time"], daily["temperature_2m_max"], daily["precipitation_sum"])
    return _cached_json(f"hist_{cell[0]}_{cell[1]}_{first}_{last}.json", fetch)


def live_heat_extra(cell: tuple) -> dict:
    """10 years of daily maximum / minimum / apparent maximum temperature (Open-Meteo archive) for the heat geo factor."""
    def fetch():
        d = _get_json(ARCHIVE_URL, {"latitude": cell[0], "longitude": cell[1], "start_date": f"{current_year() - 10}-01-01",
                                    "end_date": f"{current_year() - 1}-12-31", "timezone": "auto",
                                    "daily": "temperature_2m_max,temperature_2m_min,apparent_temperature_max"}, timeout=120)
        daily = d.get("daily", {})
        if not daily.get("time"):
            raise DataError("no daily data")
        return daily
    return _cached_json(f"heatx_{cell[0]}_{cell[1]}.json", fetch)


SOIL_STEP = 0.05            # soil is fetched per 0.05 degree (about 5 km) square
EXTRAS_WAIT_S = 420         # built-up downloads may take up to 7 minutes; then the app shows what it has
SOIL_WAIT_S = 600           # soil downloads may take up to 10 minutes (separate time from the built-up share)
FETCH_TRIES = 3             # tries per place before it is given up (a pause of RETRY_PAUSE_S between tries)
RETRY_PAUSE_S = 20
SOIL_TRIES = 40             # soil is asked again after each pause until its 10 minutes are used up
_now, _sleep = time.time, time.sleep      # replaceable in tests
_SOIL_LAST = [0.0]
OVERPASS_URLS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]


def _get_plain(url: str, params: dict, timeout: int = 60) -> dict:
    import requests
    try:
        r = requests.get(url, params=params, timeout=timeout)
    except requests.RequestException as e:
        raise DataError(f"Could not reach {url.split('/')[2]}: {str(e)[:100]}")
    if r.status_code != 200:
        raise DataError(f"{url.split('/')[2]} answered {r.status_code}")
    try:
        return r.json()
    except ValueError:
        raise DataError(f"{url.split('/')[2]} did not return data")


def live_soil(k: tuple) -> dict:
    """Clay and sand (%) at the top 5 cm of the soil, from ISRIC SoilGrids (free, 5 requests a minute)."""
    def fetch():
        wait = 12.5 - (time.time() - _SOIL_LAST[0])
        if wait > 0:
            time.sleep(wait)
        d = _get_plain(geo.SOIL_URL, {"lat": k[0], "lon": k[1], "property": ["clay", "sand"], "depth": "0-5cm", "value": "mean"}, timeout=90)
        _SOIL_LAST[0] = time.time()
        s = geo.parse_soil(d)
        if not s:
            raise DataError("no soil values here")
        return s
    return _cached_json(f"soil_{k[0]:.2f}_{k[1]:.2f}.json", fetch)


def live_builtup(k: tuple) -> float:
    """Share (%) of the 200 m circle around the point covered by OpenStreetMap buildings and roads (a proxy for imperviousness)."""
    def fetch():
        q = (f'[out:json][timeout:40];(way["building"](around:{geo.BUILTUP_RADIUS_M},{k[0]},{k[1]});'
             f'way["highway"~"^(motorway|trunk|primary|secondary|tertiary|residential|unclassified|service)$"](around:{geo.BUILTUP_RADIUS_M},{k[0]},{k[1]}););out geom;')
        last = None
        for url in OVERPASS_URLS:
            try:
                v = geo.parse_builtup(_get_plain(url, {"data": q}, timeout=90), k[0])
                if v is not None:
                    return {"builtup_pct": v}
            except DataError as e:
                last = e
        raise last or DataError("no built-up data")
    return _cached_json(f"built_{k[0]:.3f}_{k[1]:.3f}.json", fetch)["builtup_pct"]


def live_model(cell: tuple, name: str) -> dict:
    """Daily rain and maximum temperature of ONE climate model, MODEL_FIRST..MODEL_LAST."""
    def fetch():
        d = _get_json(CLIMATE_URL, {"latitude": cell[0], "longitude": cell[1], "start_date": f"{hp.MODEL_FIRST}-01-01",
                                    "end_date": f"{hp.MODEL_LAST}-12-31", "models": name,
                                    "daily": "temperature_2m_max,precipitation_sum"}, timeout=120)
        daily = d.get("daily", {})
        if not daily.get("time") or not any(v is not None for v in daily.get("temperature_2m_max", [])):
            raise DataError(f"{name} returned nothing for this place.")
        return _series(daily["time"], daily["temperature_2m_max"], daily["precipitation_sum"])
    return _cached_json(f"model_{cell[0]}_{cell[1]}_{name}.json", fetch)


# ------------------------------------------------------------------ earthquakes

_QUAKES: dict = {}


def _make_catalogue(rows: list) -> dict:
    events = hp.parse_events(rows)
    depths = {}
    for e in rows:          # depth (km) is the optional 5th value of a row
        try:
            if len(e) > 4 and e[4] is not None:
                depths[(float(e[0]), float(e[1]), date.fromisoformat(str(e[3])[:10]).toordinal())] = float(e[4])
        except (ValueError, TypeError):
            continue
    main = hp.decluster(events)
    return {"depth": depths, "main": main, "years": hp.catalogue_years(events) if events else hp.catalogue_years([(0, 0, 0, date.today().toordinal())]),
            "b": hp.b_value(main), "events": len(events), "end": date.fromordinal(max(e[3] for e in events)).isoformat() if events else ""}


def load_quakes(force: bool = False) -> dict:
    """The catalogue around the assets of the last run. Without one (no internet yet) it is empty and earthquakes count as 'no data'."""
    if _QUAKES and not force:
        return _QUAKES
    path = CACHE_DIR / "quakes_region.json"
    rows = []
    try:
        rows = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        pass
    _QUAKES.clear()
    _QUAKES.update(_make_catalogue(rows))
    _QUAKES["empty"] = not rows
    return _QUAKES


def quake_tiles(assets: list, pad: float = 2.0, size: float = 2.0) -> list:
    """Boxes (south, west, north, east) around the assets: one per 2-degree square that holds an asset, plus 2 degrees (about 220 km) of margin."""
    cells = {(math.floor(a["lat"] / size), math.floor(a["lon"] / size)) for a in assets}
    return [(max(-90.0, i * size - pad), max(-180.0, j * size - pad), min(90.0, (i + 1) * size + pad), min(180.0, (j + 1) * size + pad)) for i, j in sorted(cells)]


_TILE_ROWS: dict = {}


def tile_rows(tile: tuple) -> list:
    """Earthquakes (lat, lon, magnitude, date, depth) in one box, saved on disk so a place is only downloaded once."""
    key = tuple(round(x, 2) for x in tile)
    if key in _TILE_ROWS:
        return _TILE_ROWS[key]
    path = CACHE_DIR / ("quakes_tile_%s_%s_%s_%s.json" % key)
    try:
        rows = json.load(open(path, encoding="utf-8"))
        _TILE_ROWS[key] = rows
        return rows
    except (OSError, ValueError):
        pass
    s, w, n, e = key
    d = _get_json(USGS_URL, {"format": "geojson", "minlatitude": s, "minlongitude": w, "maxlatitude": n, "maxlongitude": e,
                             "minmagnitude": hp.QUAKE_MIN_MAG, "starttime": hp.QUAKE_START.isoformat(), "orderby": "time", "limit": 20000})
    rows = []
    for f in d.get("features", []):
        mag = (f.get("properties") or {}).get("mag")
        t = (f.get("properties") or {}).get("time")
        c = (f.get("geometry") or {}).get("coordinates") or []
        if mag is not None and t is not None and len(c) >= 2:
            day = datetime.fromtimestamp(t / 1000, timezone.utc).date().isoformat()
            rows.append([float(c[1]), float(c[0]), float(mag), day, float(c[2]) if len(c) > 2 and c[2] is not None else None])
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        json.dump(rows, open(path, "w", encoding="utf-8"))
    except OSError:
        pass
    _TILE_ROWS[key] = rows
    return rows


def refresh_quakes(assets: list | None = None) -> int:
    """Load the USGS catalogue (magnitude 4.5 and above since 1975) around the assets, wherever in the world they are."""
    if not assets:
        raise DataError("No assets to look up earthquakes for.")
    seen, rows = set(), []
    for tile in quake_tiles(assets):
        for r in tile_rows(tile):
            key = (round(r[0], 3), round(r[1], 3), r[3], r[2])
            if key not in seen:
                seen.add(key)
                rows.append(r)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    json.dump(rows, open(CACHE_DIR / "quakes_region.json", "w", encoding="utf-8"))
    load_quakes(force=True)
    return len(rows)


def load_prior() -> dict | None:
    """No regional prior: every place is judged on its own weather record, anywhere in the world."""
    return None


# ------------------------------------------------------------------ the engine

class Engine:
    """Holds the assets and their weather, and makes the monthly probabilities for any year."""

    def __init__(self):
        self.assets: list[dict] = []
        self.cells: dict = {}          # cell -> {"series", "info", "model", "error"}
        self.prior = load_prior()
        self.quake_min_mag = hp.QUAKE_MIN_MAG
        self._memo: dict = {}
        self.notes: list[str] = []

    # --- loading
    def set_assets(self, assets: list[dict]):
        self.assets = assets
        self._memo.clear()

    def fetch_history(self, source: str, saved: SavedCells | None, progress=lambda text: None):
        """Get the weather history of every cell the assets sit in."""
        self.notes = []
        cells = sorted({a["cell"] for a in self.assets})
        for i, cell in enumerate(cells, start=1):
            if cell in self.cells and self.cells[cell].get("series"):
                continue
            progress(f"Weather history {i} of {len(cells)} (cell {cell[0]}, {cell[1]})")
            entry = {"series": None, "info": "", "model": None, "error": None}
            try:
                found = saved.find(cell) if (saved and source in ("saved_first", "saved_only")) else None
                if found:
                    entry["series"] = SavedCells.load(found[1])
                    shift = "" if found[0] == cell else f", the neighbouring cell {found[0][0]}, {found[0][1]}"
                    entry["info"] = f"saved cell{shift}"
                elif source == "saved_only":
                    raise DataError("no saved weather cell for this place")
                else:
                    entry["series"] = live_history(cell)
                    entry["info"] = "Open-Meteo history (internet)"
            except DataError as e:
                entry["error"] = str(e)
            except Exception as e:      # noqa: BLE001 - shown to the person, not hidden
                entry["error"] = f"{type(e).__name__}: {e}"
            self.cells[cell] = entry
        self._memo.clear()
        return [c for c in cells if self.cells[c]["error"]]

    extras_done = False
    extras_incomplete = False
    extras_time = 0.0

    def fetch_extras(self, source: str, progress=lambda text: None) -> list:
        """The extra geographic features, fetched when risk is assessed, and the three geo factors made from them:
          terrain around each asset (Open-Meteo elevation: slope, low spot)  - clay and sand in the soil (SoilGrids)  - built-up share around the asset
          (OpenStreetMap buildings and roads, a proxy for imperviousness)  - humidity and hot nights (Open-Meteo daily records).
        Returns notes. Whatever cannot be fetched stays empty and that part of the factor is neutral."""
        notes: list = []
        internet = source != "saved_only"
        try:
            geo.retry_wadi(self.assets, internet, notes, progress)      # the wadi map is asked for again if loading could not get it
            self._memo.clear()
        except Exception as e:      # noqa: BLE001
            notes.append(f"Wadis could not be downloaded ({str(e)[:80]}).")
        progress("Fetching ground shape around each asset (terrain)...")
        try:
            got = geo.attach_terrain(self.assets, internet, notes)
            if not got:
                notes.append("Terrain (slope, low spots) could not be fetched, so the geo factors have no ground-shape part.")
        except Exception as e:      # noqa: BLE001
            notes.append(f"Terrain could not be fetched ({str(e)[:80]}).")
        # humidity and hot nights, once per weather cell
        cells = sorted({a["cell"] for a in self.assets})
        bad = streak = 0
        for i, cell in enumerate(cells, start=1):
            m = None
            progress(f"Fetching humidity and hot nights: place {i} of {len(cells)}...")
            if (internet and streak < 3) or (CACHE_DIR / f"heatx_{cell[0]}_{cell[1]}.json").exists():
                try:
                    m = geo.heat_metrics(live_heat_extra(cell))
                    streak = 0
                except Exception:      # noqa: BLE001
                    bad += 1
                    streak += 1
            elif internet:
                bad += 1            # three failures in a row: the service is down, do not wait for every place
            self.cells.setdefault(cell, {})["heat_extra"] = m
            for a in self.assets:
                if a["cell"] == cell and m:
                    g = a.setdefault("geo", {})
                    g["hot_night_pct"] = round(100 * m["hot_night_share"], 1)
                    g["humid_excess_c"] = None if m["humid_excess_c"] is None else round(m["humid_excess_c"], 1)
        if bad:
            notes.append(f"Humidity and hot nights could not be fetched for {bad} place(s), so that part of their heat geo factor is left out.")
        # soil: at most SOIL_MAX_NEW new downloads per run (SoilGrids allows 5 per minute); the rest are fetched the next time
        soil_keys = sorted({(round(a["lat"] / SOIL_STEP) * SOIL_STEP, round(a["lon"] / SOIL_STEP) * SOIL_STEP) for a in self.assets})
        # The app waits for slow services: soil and built-up share one time budget (EXTRAS_WAIT_S, 7 minutes). A failed download is retried
        # after a pause; when the time is used up, whatever has been fetched is shown and the rest stays "unable to fetch".
        t0 = _now()
        soil_deadline = t0 + SOIL_WAIT_S                # soil may use up to 10 minutes; built-up then gets its own 7 minutes
        deadline = None

        def left(limit):
            s = max(0, int(limit - _now()))
            return f"{s // 60}:{s % 60:02d} left"

        soil, soil_bad = {}, 0
        for i, k in enumerate(soil_keys, start=1):
            name = f"soil_{k[0]:.2f}_{k[1]:.2f}.json"
            cached = (CACHE_DIR / name).exists()
            if not internet and not cached:
                continue
            for attempt in range(1 if cached else SOIL_TRIES):
                if not cached and _now() >= soil_deadline:
                    break
                progress(f"Fetching soil (clay, sand): place {i} of {len(soil_keys)}" + ("" if cached else
                         f" (SoilGrids allows 5 requests a minute; waiting is normal; {left(soil_deadline)})") + "...")
                try:
                    soil[k] = live_soil(k)
                    break
                except Exception as ex:      # noqa: BLE001
                    if "no soil values" in str(ex):
                        break                    # SoilGrids has no value at this spot: asking again will not help
                    if attempt + 1 < SOIL_TRIES and _now() + RETRY_PAUSE_S < soil_deadline:
                        progress(f"Soil place {i} of {len(soil_keys)} did not answer; trying again in {RETRY_PAUSE_S} s ({left(soil_deadline)})...")
                        _sleep(RETRY_PAUSE_S)
            if k not in soil:
                soil_bad += 1
        # built-up share, per asset location (rounded to about 200 m)
        built, built_bad = {}, 0
        deadline = _now() + EXTRAS_WAIT_S
        for i, a in enumerate(self.assets, start=1):
            k = (round(a["lat"], 3), round(a["lon"], 3))
            if k in built:
                continue
            cached = (CACHE_DIR / f"built_{k[0]:.3f}_{k[1]:.3f}.json").exists()
            if not internet and not cached:
                continue
            for attempt in range(1 if cached else FETCH_TRIES):
                if not cached and _now() >= deadline:
                    break
                progress(f"Fetching built-up area (OpenStreetMap): asset {i} of {len(self.assets)}" + ("" if cached else f" ({left(deadline)})") + "...")
                try:
                    built[k] = live_builtup(k)
                    break
                except Exception:      # noqa: BLE001
                    if attempt + 1 < FETCH_TRIES and _now() + RETRY_PAUSE_S < deadline:
                        progress(f"Built-up asset {i} of {len(self.assets)} did not answer; trying again in {RETRY_PAUSE_S} s ({left(deadline)})...")
                        _sleep(RETRY_PAUSE_S)
            if k not in built:
                built_bad += 1
        for a in self.assets:
            g = a.setdefault("geo", {})
            s = soil.get((round(a["lat"] / SOIL_STEP) * SOIL_STEP, round(a["lon"] / SOIL_STEP) * SOIL_STEP))
            if s:
                g.update(s)
            b = built.get((round(a["lat"], 3), round(a["lon"], 3)))
            if b is not None:
                g["builtup_pct"] = b
        if soil_bad:
            notes.append(f"Soil (clay, sand) is missing for {soil_bad} area(s) after waiting up to {SOIL_WAIT_S // 60} minutes: SoilGrids may be slow, limited to 5 requests a minute, or paused by ISRIC. "
                         "Assess risk again later and the rest is fetched (what was fetched is saved); until then the soil part of the factors is neutral.")
        if built_bad:
            notes.append(f"Built-up share could not be fetched for {built_bad} asset(s) after waiting (OpenStreetMap servers busy or unreachable); Assess risk again later to fetch the rest; that part is left out until then.")
        geo.combine_factors(self.assets)
        self.extras_incomplete = bool(soil_bad or built_bad or bad or any("Terrain" in n for n in notes))
        self.extras_time = time.time()
        self.extras_done = True
        self._memo.clear()
        return notes

    def fetch_geo(self, source: str, progress=lambda text: None) -> list:
        """Distance to coast, ground height, distance to wadi and flood zone of every asset (geo.py). Returns notes."""
        self.extras_done = False
        self._memo.clear()
        return geo.attach(self.assets, internet=(source != "saved_only"), progress=progress)

    def years_of(self, cell: tuple) -> str:
        s = (self.cells.get(cell) or {}).get("series")
        if not s:
            return ""
        years = sorted({int(t[:4]) for t in s["time"]})
        return f"{years[0]}-{years[-1]}"

    def needs_model(self, years) -> bool:
        return any(y > current_year() for y in years) and any(
            c.get("series") and c.get("model") is None and not c.get("model_error") for c in self.cells.values())

    def fetch_model(self, progress=lambda text: None):
        cells = [c for c, e in self.cells.items() if e.get("series") and e.get("model") is None and not e.get("model_error")]
        for i, cell in enumerate(cells, start=1):
            got, errs = {}, []
            for name in CLIMATE_MODELS:
                progress(f"Climate models: place {i} of {len(cells)} (cell {cell[0]}, {cell[1]}), {name} ...")
                try:
                    got[name] = live_model(cell, name)
                except Exception as e:      # noqa: BLE001
                    errs.append(f"{name}: {e}")
            if got:
                self.cells[cell]["model"] = got
                self.cells[cell]["model_skipped"] = errs
            else:
                self.cells[cell]["model_error"] = "; ".join(errs) or "no climate model answered"
        self._memo.clear()

    # --- probabilities
    def _weather(self, cell: tuple, year: int) -> dict:
        key = (cell, year)
        if key in self._memo:
            return self._memo[key]
        entry = self.cells.get(cell) or {}
        series = entry.get("series")
        if not series:
            out = {"flood": None, "heat": None, "severity": None, "note": entry.get("error") or "no weather history", "adjusted": False}
            self._memo[key] = out
            return out
        note, adjusted = "", False
        change = None
        if year > current_year():
            if entry.get("model"):
                ms = entry["model"]
                if "time" in ms:                      # a single model series
                    ms = {"model": ms}
                changes = [hp.climate_change(m["time"], m["tmax"], m["rain"], year) for m in ms.values()]
                change = hp.ensemble_change(changes)
                adjusted = True
                note = (f"average change of {change['n_models']} climate model(s) applied (model years {change['window'][0]}-{change['window'][1]}); "
                        f"yearly warming {change['dT_range'][0]:+.1f} to {change['dT_range'][1]:+.1f} C between the models")
            else:
                note = "future climate not available, so this is today's baseline" + (
                    f" ({entry.get('model_error')})" if entry.get("model_error") else "")
        counts = hp.adjusted_counts(series["time"], series["tmax"], series["rain"], change) if change else \
            self._baseline_counts(cell)
        years_all = sorted({y for (y, _) in counts})
        rain_sums = hp.month_sums(counts, set(years_all[-RAIN_YEARS:]))
        heat_sums = hp.month_sums(counts, set(years_all[-HEAT_YEARS:]))
        probs = hp.weather_probabilities(rain_sums, self.prior, heat_sums)
        sev = hp.weather_severity(series["time"], series["tmax"], series["rain"], change)
        out = {"flood": probs["flood"], "heat": probs["heat"], "note": note, "adjusted": adjusted, "severity": sev,
               "months_flood": min(rain_sums["n"]) if rain_sums["n"] else 0}
        self._memo[key] = out
        return out

    def _baseline_counts(self, cell: tuple) -> dict:
        key = (cell, "counts")
        if key not in self._memo:
            s = self.cells[cell]["series"]
            self._memo[key] = hp.month_counts(s["time"], s["tmax"], s["rain"])
        return self._memo[key]

    def probs(self, asset: dict, year: int) -> dict:
        """{"flood": [12], "heat": [12], "quake": [12], "note": ...} (None for a hazard that could not be worked out)."""
        w = self._weather(asset["cell"], year)
        q = load_quakes()
        if q.get("empty"):                       # no catalogue downloaded: say "no data" as zero chance
            rate, n = 0.0, 0
        else:
            rate, n = hp.quake_rate(q["main"], asset["lat"], asset["lon"], q["years"], self.quake_min_mag, b=q["b"])
        pq = hp.quake_monthly_prob(rate)
        sev = w["severity"]
        qe = hp.quake_size_by_month(hp.quake_loss_events(q["main"], asset["lat"], asset["lon"], q.get("depth")))
        mag = qe["mag_all"]                      # all-year average magnitude of the main shocks of magnitude 6 or more within 150 km
        qmult = [damage.quake_multiplier(qe["dist"][m], qe["depth"][m])[1] for m in range(12)]
        if sev:
            rain_m = [sev["rain_by_month"][m] if sev["rain_by_month"][m] is not None else sev["rain_mm"] for m in range(12)]
            heat_m = [sev["heat_by_month"][m] if sev["heat_by_month"][m] is not None else sev["heat_c"] for m in range(12)]
            rain_n, heat_n = sev["rain_n"], sev["heat_n"]
        else:
            rain_m, heat_m, rain_n, heat_n = [hp.RAIN_MM] * 12, [hp.HEAT_C] * 12, [0] * 12, [0] * 12
        sv = {"flood": [damage._unit(x, *damage.RAIN_RANGE) for x in rain_m],
              "heat": [damage._unit(x, *damage.HEAT_RANGE) for x in heat_m],
              "quake": [damage._unit(x, *damage.QUAKE_RANGE) for x in qe["mag"]]}
        gg = asset.get("geo") or {}
        gf, hf = gg.get("factor", 1.0), gg.get("heat_geo", 1.0)
        qmult = [min(1.5, max(0.5, x * gg.get("site_quake", 1.0))) for x in qmult]       # earthquake geo factor = event distance/depth x ground
        loss = damage.monthly_losses(sv, asset.get("material"), asset.get("adapt") or asset.get("adaptation", 0.0), gf, qmult, hf)
        probs = {"flood": w["flood"], "heat": w["heat"], "quake": [pq] * 12}
        dmg = damage.monthly_damage(probs, loss)
        return {**probs, "note": w["note"], "adjusted": w["adjusted"], "quake_events": n,
                "size": {"rain_mm": rain_m, "heat_c": heat_m, "quake_mag": mag, "quake_mag_m": qe["mag"], "rain_n": rain_n, "heat_n": heat_n},
                "quake_info": {**qe, "factor": qmult, "depth_known": q.get("depth") is not None and bool(q.get("depth"))},
                "severity": sv, "loss_if_event": loss, "damage": dmg}

    def month_value(self, asset: dict, year: int, month: int, hazard: str):
        if hazard in ("damage", "damage_flood", "damage_heat", "damage_quake"):
            d = self.probs(asset, year)["damage"]["total" if hazard == "damage" else hazard[7:]]
            return None if d is None else d[month - 1]
        p = self.probs(asset, year)[hazard]
        return None if p is None else p[month - 1]

    def annual(self, asset: dict, year: int, hazard: str):
        if hazard.startswith("damage"):
            d = self.probs(asset, year)["damage"]["total" if hazard == "damage" else hazard[7:]]
            return None if d is None else min(1.0, sum(d))        # expected share of value lost over the year
        p = self.probs(asset, year)[hazard]
        return None if p is None else hp.annual_prob(p)


def fmt_pct(p) -> str:
    if p is None:
        return "n/a"
    if p < 0.0005:
        return "<0.1%"
    return f"{p * 100:.1f}%" if p < 0.1 else f"{p * 100:.0f}%"
