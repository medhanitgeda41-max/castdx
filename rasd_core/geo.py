"""Geography of each asset and how it changes the flood loss: distance to the coast, ground height, distance to a wadi, flood zone.

Where each fact comes from (a value in the asset file always wins):
  distance to coast  the file column "distance to coast", else computed here from the OpenStreetMap coastline that comes with the
                     app (data/coast_segments.json.gz, 268,000 segments). Works offline.
  ground height      the file column "elevation", else the Open-Meteo elevation service (internet).
  distance to wadi   the file column "distance to wadi", else wadi lines: GeoJSON line files named wadi*.geojson in the layers folder,
                     else the OpenStreetMap wadis downloaded once (internet, remembered on this PC). Unknown stays unknown.
  flood zone         the file column "flood zone" (Low / Moderate / High / Very high), else polygons in the layers folder.

geographic exposure (0 to 1) = weighted mean of the parts that are known (a missing fact does not count as zero risk):
  low ground 0.30   (0 m scores 1, 8 m or more scores 0)       } the same scoring as the first RASD flood rule
  near coast 0.20   (on the shore 1, 10 km or more inland 0)   }
  flood zone 0.20   (Low 0.1, Moderate 0.4, High 0.8, Very high 1)
  near wadi  0.20   (on the wadi 1, 2 km or more away 0)       (wadi weight and the 2 km are RASD assumptions, like the rest)
flood loss factor = 0.5 + exposure: 0.5 for a high, inland site far from any wadi, 1.0 when nothing is known or the site is average,
1.5 for a low coastal site on a wadi. It multiplies only the flood loss; the flood CHANCE is still counted from rain records.

EXTRA GEO FACTORS (fetched when risk is assessed; all weights below are RASD assumptions, not calibrated):
  flood geo factor      the factor above times a terrain adjustment: the asset sits lower than the ground 1 km around it (a hollow where water
                        collects) -> up to x1.15; on a hill or ridge -> down to x0.90. Result kept between 0.5 and 1.5.
  earthquake geo factor the factor from the distance/depth of past earthquakes (damage.quake_multiplier) times a ground factor: very flat ground
                        1 km around is usually sediment that shakes harder (x1.15); steep ground is usually rock (x0.90). The idea follows Allen & Wald
                        (USGS, 2007), who estimate soil stiffness from terrain slope; their table is NOT used. Kept between 0.5 and 1.5.
  heat geo factor       0.8 to 1.2 from two things in 10 years of daily Open-Meteo records of the place: how much hotter it FEELS than it is on hot
                        days (humidity, apparent minus real maximum temperature) and how often summer nights stay at 30 C or more.
"""
from __future__ import annotations

import gzip
import json
import math
import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
LAYER_DIRS = [APP_DIR / "layers"] + ([Path(sys.executable).resolve().parent / "layers"] if getattr(sys, "frozen", False) else [])
CACHE_DIR = Path.home() / ".rasd_prob_cache"
UAE_BOX = (22.0, 51.0, 26.7, 57.0)            # south, west, north, east
SEARCH_KM = 300.0                             # distances are searched this far; 300 means "none within 300 km"
LOW_GROUND_M = 8.0
COAST_NEAR_KM = 10.0
WADI_NEAR_KM = 2.0
ZONE_SCORE = {"low": 0.10, "moderate": 0.40, "medium": 0.40, "high": 0.80, "very high": 1.0}
WEIGHTS = {"elevation": 0.30, "coast": 0.20, "zone": 0.20, "wadi": 0.20}
FACTOR_BASE = 0.5
OVERPASS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter")
GRID = 0.1                                    # degrees per index cell

_STATE: dict = {}


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


# ------------------------------------------------------------------ distance to a set of line segments

def _seg_km(lat, lon, s) -> float:
    kx = 111.32 * math.cos(math.radians(lat))
    ky = 110.57
    ax, ay = (s[1] - lon) * kx, (s[0] - lat) * ky
    bx, by = (s[3] - lon) * kx, (s[2] - lat) * ky
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else _clamp(-(ax * dx + ay * dy) / l2)
    return math.hypot(ax + t * dx, ay + t * dy)


class Lines:
    """Line segments [lat1, lon1, lat2, lon2] with a grid index, so a distance does not have to look at all of them."""

    def __init__(self, segments: list):
        self.grid: dict = {}
        self.n = len(segments)
        for s in segments:
            i0, i1 = sorted((int(math.floor(s[0] / GRID)), int(math.floor(s[2] / GRID))))
            j0, j1 = sorted((int(math.floor(s[1] / GRID)), int(math.floor(s[3] / GRID))))
            for i in range(i0, i1 + 1):
                for j in range(j0, j1 + 1):
                    self.grid.setdefault((i, j), []).append(s)

    def distance_km(self, lat: float, lon: float) -> float:
        """Km to the nearest segment; SEARCH_KM when none is within that distance."""
        ri = int(SEARCH_KM / 110.0 / GRID) + 1
        rj = int(SEARCH_KM / (110.0 * max(0.2, math.cos(math.radians(lat)))) / GRID) + 1
        ci, cj = int(math.floor(lat / GRID)), int(math.floor(lon / GRID))
        best = None
        seen = set()
        for i in range(ci - ri, ci + ri + 1):
            for j in range(cj - rj, cj + rj + 1):
                for s in self.grid.get((i, j), ()):
                    if id(s) in seen:
                        continue
                    seen.add(id(s))
                    d = _seg_km(lat, lon, s)
                    if best is None or d < best:
                        best = d
        return best if best is not None and best <= SEARCH_KM else SEARCH_KM


# ------------------------------------------------------------------ coast (offline)

def coast() -> Lines | None:
    if "coast" not in _STATE:
        lines = None
        try:
            with gzip.open(DATA_DIR / "coast_segments.json.gz", "rt", encoding="utf-8") as f:
                seg = json.load(f)
            lines = Lines(seg) if len(seg) > 5000 else None        # a tiny file is not a coastline
        except (OSError, ValueError):
            lines = None
        _STATE["coast"] = lines
    return _STATE["coast"]


# ------------------------------------------------------------------ layers: wadi lines and flood-zone polygons

def _geojson_features(path: Path) -> list:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data.get("features", []) if data.get("type") == "FeatureCollection" else [data]


def _layer_files(prefix: str | None) -> list:
    files = []
    for d in LAYER_DIRS:                      # next to the app (or next to the .exe) in a folder called "layers"
        if d.is_dir():
            files += sorted(d.glob("*.geojson")) + sorted(d.glob("*.json"))
    is_wadi = lambda p: p.name.lower().startswith("wadi")        # noqa: E731
    return [p for p in files if (is_wadi(p) if prefix == "wadi" else not is_wadi(p))]


def _line_parts(geometry: dict) -> list:
    t, c = geometry.get("type"), geometry.get("coordinates", [])
    if t == "LineString":
        return [c]
    if t == "MultiLineString":
        return list(c)
    return []


def _wadi_from_layers() -> list:
    segs = []
    for p in _layer_files("wadi"):
        for feat in _geojson_features(p):
            for line in _line_parts(feat.get("geometry") or {}):
                for a, b in zip(line, line[1:]):
                    segs.append([a[1], a[0], b[1], b[0]])
    return segs


_WADI_FAILED = [0]


def _wadi_tiles(rows: int = 3, cols: int = 2) -> list:
    s, w, n, e = UAE_BOX
    out = []
    for i in range(rows):
        for j in range(cols):
            out.append((s + (n - s) * i / rows, w + (e - w) * j / cols, s + (n - s) * (i + 1) / rows, w + (e - w) * (j + 1) / cols))
    return out


def _wadi_query(box) -> str:
    bb = ",".join(f"{v:.4f}" for v in box)
    return ("[out:json][timeout:90];("
            f'way["waterway"="wadi"]({bb});'
            f'way["waterway"~"^(stream|river|drain|ditch|canal)$"]["intermittent"="yes"]({bb});'
            f'way["waterway"~"^(stream|river|drain)$"]["name"~"wadi|Wadi|\u0648\u0627\u062f\u064a"]({bb});'
            f'way["natural"="valley"]["name"~"wadi|Wadi|\u0648\u0627\u062f\u064a"]({bb});'
            ");out geom;")


WADI_WAIT_S = 600           # the wadi download may take up to 10 minutes (slow free servers are waited for and asked again)
WADI_RETRY_PAUSE_S = 20
_now, _sleep = time.time, time.sleep      # replaceable in tests


def _wadi_from_osm(progress=lambda t: None) -> list:
    """Wadi lines from OpenStreetMap (free, no subscription). The UAE is asked for in six pieces, because one request for the whole country
    is often too big for the free Overpass servers. A piece that fails on both servers is asked again after a pause, for up to WADI_WAIT_S;
    pieces still missing then are counted in _WADI_FAILED."""
    import requests
    deadline = _now() + WADI_WAIT_S
    pending, segs, last = list(_wadi_tiles()), [], None
    total = len(pending)
    while pending:
        for box in list(pending):
            if _now() >= deadline:
                break
            progress(f"Downloading wadis from OpenStreetMap: piece {total - len(pending) + 1} of {total} ({max(0, int(deadline - _now())) // 60} min left)...")
            query = _wadi_query(box)
            for url in OVERPASS:
                try:
                    r = requests.post(url, data={"data": query}, timeout=120)
                    r.raise_for_status()
                    for el in r.json().get("elements", []):
                        pts = [(g["lat"], g["lon"]) for g in el.get("geometry", [])]
                        segs += [[a[0], a[1], b[0], b[1]] for a, b in zip(pts, pts[1:])]
                    pending.remove(box)
                    break
                except Exception as ex:          # noqa: BLE001 - try the next mirror
                    last = ex
        if not pending or _now() + WADI_RETRY_PAUSE_S >= deadline:
            break
        progress(f"Wadis: {len(pending)} piece(s) did not answer; trying again in {WADI_RETRY_PAUSE_S} s...")
        _sleep(WADI_RETRY_PAUSE_S)
    _WADI_FAILED[0] = len(pending)
    if not segs and pending:
        raise RuntimeError(str(last)[:120])
    return [[round(v, 5) for v in s_] for s_ in segs]


def wadi(internet: bool, notes: list, progress=lambda t: None) -> Lines | None:
    """Wadi lines, or None when none are available (then the wadi part is simply not used)."""
    if "wadi" in _STATE and (_STATE["wadi"] is not None or not internet):
        return _STATE["wadi"]
    segs = _wadi_from_layers()
    if not segs:
        cache = CACHE_DIR / "wadi_lines.json"
        if cache.exists():
            try:
                segs = json.loads(cache.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                segs = []
        if not segs and internet:
            progress("Downloading wadis from OpenStreetMap...")
            try:
                segs = _wadi_from_osm(progress)
                if segs:
                    if _WADI_FAILED[0]:
                        notes.append(f"Wadis: {_WADI_FAILED[0]} of 6 map pieces could not be downloaded from OpenStreetMap, so wadis in those areas are missing for this run.")
                    else:
                        try:
                            CACHE_DIR.mkdir(parents=True, exist_ok=True)
                            cache.write_text(json.dumps(segs), encoding="utf-8")
                        except OSError:
                            pass
                else:
                    notes.append("OpenStreetMap returned no wadis for the UAE, so distance to wadi is not used (add a wadi*.geojson file to the layers folder, or a 'distance to wadi' column).")
            except Exception as ex:      # noqa: BLE001
                notes.append(f"Wadis could not be downloaded ({ex}), so distance to wadi is not used.")
    _STATE["wadi"] = Lines(segs) if segs else None
    return _STATE["wadi"]


def _in_ring(x, y, ring) -> bool:
    inside, j = False, len(ring) - 1
    for i in range(len(ring)):
        xi, yi, xj, yj = ring[i][0], ring[i][1], ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def _zones() -> list:
    if "zones" not in _STATE:
        polys = []
        for p in _layer_files("zone"):
            for feat in _geojson_features(p):
                zone = str((feat.get("properties") or {}).get("flood_zone", "")).strip().lower()
                if zone not in ZONE_SCORE:
                    continue
                g = feat.get("geometry") or {}
                rings_list = [g.get("coordinates", [])] if g.get("type") == "Polygon" else (g.get("coordinates", []) if g.get("type") == "MultiPolygon" else [])
                for rings in rings_list:
                    if rings:
                        polys.append((ZONE_SCORE[zone], zone, rings))
        _STATE["zones"] = polys
    return _STATE["zones"]


def zone_at(lat: float, lon: float) -> str | None:
    best = None
    for score, zone, rings in _zones():
        if _in_ring(lon, lat, rings[0]) and not any(_in_ring(lon, lat, h) for h in rings[1:]):
            if best is None or score > best[0]:
                best = (score, zone)
    return best[1] if best else None


# ------------------------------------------------------------------ elevation (internet)

def elevations(points: list, internet: bool, notes: list) -> dict:
    """{(lat, lon): metres} for the points (rounded to 3 decimals), from the cache and the Open-Meteo elevation service."""
    store: dict = {}
    cache = CACHE_DIR / "elevation_points.json"
    if cache.exists():
        try:
            store = {tuple(map(float, k.split(","))): v for k, v in json.loads(cache.read_text(encoding="utf-8")).items()}
        except (OSError, ValueError):
            store = {}
    missing = [p for p in points if p not in store]
    if missing and internet:
        try:
            import requests
            for i in range(0, len(missing), 100):
                batch = missing[i:i + 100]
                r = requests.get("https://api.open-meteo.com/v1/elevation", timeout=30,
                                 params={"latitude": ",".join(str(p[0]) for p in batch), "longitude": ",".join(str(p[1]) for p in batch)})
                r.raise_for_status()
                for p, v in zip(batch, r.json().get("elevation", [])):
                    if v is not None:
                        store[p] = float(v)
            try:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps({f"{k[0]},{k[1]}": v for k, v in store.items()}), encoding="utf-8")
            except OSError:
                pass
        except Exception as ex:          # noqa: BLE001
            notes.append(f"Ground height could not be downloaded ({str(ex)[:100]}), so it is not used for assets without an elevation column.")
    return {p: store[p] for p in points if p in store}


# ------------------------------------------------------------------ the exposure and the factor

def exposure(elevation_m=None, coast_km=None, wadi_km=None, zone=None) -> float | None:
    parts = {}
    if elevation_m is not None:
        parts["elevation"] = _clamp((LOW_GROUND_M - elevation_m) / LOW_GROUND_M)
    if coast_km is not None:
        parts["coast"] = _clamp(1.0 - coast_km / COAST_NEAR_KM)
    if wadi_km is not None:
        parts["wadi"] = _clamp(1.0 - wadi_km / WADI_NEAR_KM)
    z = str(zone or "").strip().lower()
    if z in ZONE_SCORE:
        parts["zone"] = ZONE_SCORE[z]
    if not parts:
        return None
    return sum(WEIGHTS[k] * v for k, v in parts.items()) / sum(WEIGHTS[k] for k in parts)


def flood_factor(exp: float | None) -> float:
    """1.0 when no geography is known; otherwise 0.5 to 1.5."""
    return 1.0 if exp is None else FACTOR_BASE + exp


def attach(assets: list, internet: bool = True, progress=lambda t: None, use_wadi: bool = True) -> list:
    """Adds asset["geo"] = {elevation_m, coast_km, wadi_km, zone, exposure, factor, used} to every asset. Returns notes."""
    notes: list = []
    rows = [a.get("row") or {} for a in assets]
    c = coast()
    if c is None and any(_num(r.get("distance_to_coast_km")) is None for r in rows):
        notes.append("The coastline file is missing, so distance to coast is only used where the file has it.")
    pts = {a["name"]: (round(a["lat"], 3), round(a["lon"], 3)) for a in assets}
    need = sorted({pts[a["name"]] for a, r in zip(assets, rows) if _num(r.get("elevation_m")) is None})
    elev = elevations(need, internet, notes) if need else {}
    need_wadi = use_wadi and any(_num(r.get("distance_to_wadi_km")) is None for r in rows)
    w = wadi(internet, notes, progress) if need_wadi else None
    for a, r in zip(assets, rows):
        e = _num(r.get("elevation_m"))
        e_src = "file" if e is not None else None
        if e is None and pts[a["name"]] in elev:
            e, e_src = elev[pts[a["name"]]], "Open-Meteo"
        d = _num(r.get("distance_to_coast_km"))
        d_src = "file" if d is not None else None
        if d is None and c is not None:
            d, d_src = c.distance_km(a["lat"], a["lon"]), "OpenStreetMap coastline"
        wd = _num(r.get("distance_to_wadi_km"))
        w_src = "file" if wd is not None else None
        if wd is None and w is not None:
            wd, w_src = w.distance_km(a["lat"], a["lon"]), "wadi lines"
        z = str(r.get("flood_zone") or "").strip() or None
        z_src = "file" if z else None
        if not z:
            z = zone_at(a["lat"], a["lon"])
            z_src = "flood-zone layer" if z else None
        ex = exposure(e, d, wd, z)
        a["geo"] = {"elevation_m": e, "coast_km": d, "wadi_km": wd, "zone": z, "exposure": ex, "factor": flood_factor(ex),
                    "used": {"elevation": e_src, "coast": d_src, "wadi": w_src, "zone": z_src}}
    known = [a["geo"]["exposure"] is not None for a in assets]
    if assets and not any(known):
        notes.append("No geography was available for any asset, so the flood loss is not adjusted.")
    elif assets and w is None and need_wadi and use_wadi:
        notes.append("Distance to wadi is not available (no wadi data), so it is left out of the geographic exposure.")
    return notes


def retry_wadi(assets: list, internet: bool, notes: list, progress=lambda t: None) -> None:
    """The wadi map is first asked for when the files are loaded. If that failed, it is asked for again here (when risk is assessed),
    and the distance to wadi and the flood exposure are filled in for the assets that still have none."""
    todo = [a for a in assets if a.get("geo") and a["geo"].get("wadi_km") is None
            and _num((a.get("row") or {}).get("distance_to_wadi_km")) is None]
    if not todo or not internet:
        return
    w = wadi(internet, notes, progress)
    if w is None:
        return
    for a in todo:
        g = a["geo"]
        g["wadi_km"] = w.distance_km(a["lat"], a["lon"])
        g["used"]["wadi"] = "wadi lines"
        g["exposure"] = exposure(g.get("elevation_m"), g.get("coast_km"), g["wadi_km"], g.get("zone"))
        g["factor"] = flood_factor(g["exposure"])


# ------------------------------------------------------------------ extra geo factors (terrain, ground, humidity, hot nights)
RING_DEG = 0.01                          # about 1.1 km
HOT_NIGHT_C = 30.0
HOT_DAY_C = 38.0


def ring_points(lat: float, lon: float) -> list:
    d = RING_DEG
    return [(round(lat + a * d, 3), round(lon + b * d, 3)) for a in (-1, 0, 1) for b in (-1, 0, 1) if (a, b) != (0, 0)]


def terrain_metrics(center_m: float, ring_m: list) -> dict | None:
    """low_spot_m > 0 when the asset is lower than the ground around it; slope in metres per metre over about 1.1 km."""
    ring = [v for v in ring_m if v is not None]
    if center_m is None or len(ring) < 4:
        return None
    return {"low_spot_m": sum(ring) / len(ring) - center_m, "slope": sum(abs(v - center_m) for v in ring) / len(ring) / (RING_DEG * 111000)}


def terrain_flood_adjust(low_spot_m: float) -> float:
    return 1.15 if low_spot_m >= 5 else 1.07 if low_spot_m >= 2 else 0.90 if low_spot_m <= -5 else 0.95 if low_spot_m <= -2 else 1.0


def site_quake_factor(slope: float) -> float:
    return 1.15 if slope < 0.005 else 1.0 if slope < 0.03 else 0.90


def heat_metrics(daily: dict) -> dict | None:
    """From daily temperature_2m_max / temperature_2m_min / apparent_temperature_max (and time)."""
    try:
        t, tx, tn, ap = daily["time"], daily["temperature_2m_max"], daily["temperature_2m_min"], daily["apparent_temperature_max"]
    except (KeyError, TypeError):
        return None
    nights = hot_nights = 0
    ex = []
    for i, day in enumerate(t):
        if i < len(tn) and tn[i] is not None and day[5:7] in ("06", "07", "08", "09"):
            nights += 1
            hot_nights += tn[i] >= HOT_NIGHT_C
        if i < len(tx) and i < len(ap) and tx[i] is not None and ap[i] is not None and tx[i] >= HOT_DAY_C:
            ex.append(ap[i] - tx[i])
    if nights < 100:
        return None
    return {"hot_night_share": hot_nights / nights, "humid_excess_c": (sum(ex) / len(ex)) if len(ex) >= 10 else None, "hot_days": len(ex)}


def heat_geo_factor(m: dict | None, builtup_pct: float | None = None) -> float:
    """1.0 when nothing is known; else 0.8 to 1.2. Parts (weights): humid hot days 0.4, hot summer nights 0.3, built-up share 0.3 (urban heat island).
    Parts that are not known are left out and the rest re-weighted."""
    parts, w = 0.0, 0.0
    if m and m.get("humid_excess_c") is not None:
        parts += 0.4 * _clamp((m["humid_excess_c"] - 2.0) / 8.0)
        w += 0.4
    if m:
        parts += 0.3 * _clamp(m["hot_night_share"] / 0.5)
        w += 0.3
    if builtup_pct is not None:
        parts += 0.3 * _clamp(builtup_pct / 50.0)
        w += 0.3
    return 0.8 + 0.4 * (parts / w) if w else 1.0


def heat_parts(m: dict | None, builtup_pct: float | None = None) -> dict:
    """The score (0 to 1) of each part of the heat geo factor; None when that part is not known (and so left out)."""
    return {"humid": None if not (m and m.get("humid_excess_c") is not None) else _clamp((m["humid_excess_c"] - 2.0) / 8.0),
            "night": None if not m else _clamp(m["hot_night_share"] / 0.5),
            "built": None if builtup_pct is None else _clamp(builtup_pct / 50.0)}


def slope_degrees(slope: float) -> float:
    return math.degrees(math.atan(slope))


def flood_soil_adjust(clay_pct: float | None) -> float:
    """Clay soil lets little water in, so rain runs off and collects (x1.08 at 40% clay or more); sandy, clay-poor soil drains (x0.95 at 15% or less)."""
    if clay_pct is None:
        return 1.0
    return 1.08 if clay_pct >= 40 else 1.04 if clay_pct >= 28 else 0.95 if clay_pct <= 15 else 1.0


def flood_builtup_adjust(builtup_pct: float | None) -> float:
    """Roofs and roads shed rain instead of soaking it up: more built-up ground around the asset, more surface water (x0.97 to x1.10)."""
    if builtup_pct is None:
        return 1.0
    return 1.10 if builtup_pct >= 40 else 1.05 if builtup_pct >= 20 else 0.97 if builtup_pct < 5 else 1.0


def quake_soil_adjust(clay_pct: float | None, sand_pct: float | None, coast_km, elevation_m) -> float:
    """Soft clay soil shakes harder (x1.05 at 40% clay or more). Loose sand on low ground next to the sea can liquefy (x1.05)."""
    f = 1.05 if (clay_pct is not None and clay_pct >= 40) else 1.0
    if sand_pct is not None and sand_pct >= 60 and coast_km is not None and coast_km <= 5 and elevation_m is not None and elevation_m <= 10:
        f *= 1.05
    return f


SOIL_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"


def parse_soil(d: dict) -> dict | None:
    """SoilGrids answer -> clay and sand in % (0-5 cm). SoilGrids gives g/kg, so divide by 10."""
    out = {}
    try:
        for lay in d["properties"]["layers"]:
            if lay.get("name") in ("clay", "sand"):
                v = lay["depths"][0]["values"].get("mean")
                if v is not None:
                    out[lay["name"] + "_pct"] = round(float(v) / 10.0, 1)
    except (KeyError, TypeError, IndexError, ValueError):
        return None
    return out or None


def _poly_area_m2(pts: list, lat0: float) -> float:
    k = 111320.0
    xs = [(p["lon"]) * k * math.cos(math.radians(lat0)) for p in pts]
    ys = [(p["lat"]) * k for p in pts]
    return abs(sum(xs[i] * ys[(i + 1) % len(pts)] - xs[(i + 1) % len(pts)] * ys[i] for i in range(len(pts)))) / 2.0


ROAD_WIDTH_M = {"motorway": 20, "trunk": 16, "primary": 14, "secondary": 12, "tertiary": 10}
BUILTUP_RADIUS_M = 200


def parse_builtup(d: dict, lat0: float) -> float | None:
    """OpenStreetMap buildings and roads within 200 m -> % of the circle that is roofs or road. A PROXY for imperviousness: road widths are assumed
    (20 m motorway ... 7 m small streets) and OpenStreetMap may be incomplete."""
    els = d.get("elements")
    if els is None:
        return None
    area = 0.0
    for e in els:
        geom = e.get("geometry") or []
        tags = e.get("tags") or {}
        if "building" in tags and len(geom) >= 3:
            area += _poly_area_m2(geom, lat0)
        elif "highway" in tags and len(geom) >= 2:
            length = sum(math.hypot((geom[i + 1]["lon"] - geom[i]["lon"]) * 111320.0 * math.cos(math.radians(lat0)), (geom[i + 1]["lat"] - geom[i]["lat"]) * 111320.0)
                         for i in range(len(geom) - 1))
            area += length * ROAD_WIDTH_M.get(tags["highway"], 7)
    return round(min(100.0, 100.0 * area / (math.pi * BUILTUP_RADIUS_M ** 2)), 1)


def attach_terrain(assets: list, internet: bool, notes: list) -> int:
    """Adds geo['terrain'], recomputes geo['factor'] (flood geo factor) and sets geo['site_quake']. Returns how many assets got terrain."""
    pts = {a["name"]: (round(a["lat"], 3), round(a["lon"], 3)) for a in assets}
    need = sorted({p for a in assets for p in [pts[a["name"]]] + ring_points(a["lat"], a["lon"])})
    elev = elevations(need, internet, notes) if need else {}
    got = 0
    for a in assets:
        g = a.setdefault("geo", {})
        base = g.setdefault("factor_base", g.get("factor", 1.0) if g.get("factor") is not None else 1.0)
        c = elev.get(pts[a["name"]])
        tm = terrain_metrics(g.get("elevation_m") if g.get("elevation_m") is not None else c, [elev.get(p) for p in ring_points(a["lat"], a["lon"])])
        if tm is None:
            continue
        got += 1
        g["terrain"] = tm
        g["slope_deg"] = round(slope_degrees(tm["slope"]), 2)
        g["low_spot_m"] = round(tm["low_spot_m"], 1)
        g["flood_terrain"] = terrain_flood_adjust(tm["low_spot_m"])
        g["slope_quake"] = site_quake_factor(tm["slope"])
    return got


def combine_factors(assets: list) -> None:
    """Turns the fetched features of every asset into its three geo factors (all weights are RASD assumptions, see the About tab)."""
    for a in assets:
        g = a.setdefault("geo", {})
        base = g.get("factor_base", g.get("factor", 1.0) if g.get("factor") is not None else 1.0)
        g["factor_base"] = base
        g["flood_soil"] = flood_soil_adjust(g.get("clay_pct"))
        g["flood_built"] = flood_builtup_adjust(g.get("builtup_pct"))
        g["factor"] = _clamp(base * g.get("flood_terrain", 1.0) * g["flood_soil"] * g["flood_built"], 0.5, 1.5)
        g["quake_soil"] = quake_soil_adjust(g.get("clay_pct"), g.get("sand_pct"), g.get("coast_km"), g.get("elevation_m"))
        g["site_quake"] = g.get("slope_quake", 1.0) * g["quake_soil"]
        g["heat_geo"] = heat_geo_factor({"humid_excess_c": g.get("humid_excess_c"), "hot_night_share": g["hot_night_pct"] / 100.0} if g.get("hot_night_pct") is not None else None,
                                        g.get("builtup_pct"))
