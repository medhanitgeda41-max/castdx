"""Find real-world events that could have changed a driver (a wildfire for haze, a volcano for ozone, El Nino for water vapour ...).

Sources, all free and without a key:
  NASA EONET     natural events with dates and places (wildfires, volcanoes, storms, drought, floods, dust and haze, temperature extremes)
  Wikipedia      articles on the event type and place
  OpenAlex       the most cited scientific papers on the driver and place
  GDELT          recent news (GDELT only keeps about the last 3 months of articles)

Each source is tried on its own. If one is down or blocked, the others still answer and the failure is shown, never hidden.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

HEADERS = {"User-Agent": "RASD-Earth-Trends/1.0 (education; contact: local app)"}

EONET = "https://eonet.gsfc.nasa.gov/api/v3/events"
WIKI = "https://en.wikipedia.org/w/api.php"
OPENALEX = "https://api.openalex.org/works"
GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"

# driver -> EONET category ids, search words for the event/news sources, search words for scientific papers, whether the place name should be added
TOPICS = {
    "CO2": {"eonet": ["wildfires", "volcanoes"], "events": "wildfire OR volcanic eruption OR drought", "science": "carbon dioxide concentration increase", "global": True},
    "AOD_55": {"eonet": ["wildfires", "volcanoes", "dustHaze"], "events": "dust storm OR wildfire smoke OR air pollution haze", "science": "aerosol optical depth trend"},
    "TO3": {"eonet": ["volcanoes", "wildfires"], "events": "ozone depletion OR volcanic eruption OR Montreal Protocol", "science": "total column ozone trend"},
    "ALLSKY_SFC_LW_DWN": {"eonet": ["tempExtremes", "wildfires"], "events": "heatwave OR drought", "science": "downward longwave radiation trend"},
    "TQV": {"eonet": ["severeStorms", "floods"], "events": "atmospheric river OR heavy rain OR marine heatwave", "science": "atmospheric water vapour trend"},
    "QV2M": {"eonet": ["severeStorms", "floods"], "events": "heavy rain OR humid heat", "science": "specific humidity trend"},
    "ONI": {"eonet": [], "events": "El Niño OR La Niña", "science": "ENSO El Niño impact", "global": True},
    "T2M": {"eonet": ["tempExtremes", "wildfires", "drought"], "events": "heatwave OR drought", "science": "surface air temperature trend"},
    "PRECTOTCORR": {"eonet": ["floods", "severeStorms", "drought"], "events": "flood OR drought OR monsoon", "science": "precipitation trend"},
    "RH2M": {"eonet": ["drought", "floods"], "events": "drought OR humid weather", "science": "relative humidity trend"},
    "CLOUD_AMT": {"eonet": ["severeStorms", "dustHaze"], "events": "storm OR cyclone OR dust storm", "science": "cloud cover trend"},
    "ALLSKY_SFC_SW_DWN": {"eonet": ["dustHaze", "wildfires", "volcanoes"], "events": "dust storm OR smoke haze OR volcanic eruption", "science": "surface solar radiation dimming brightening"},
    "WS10M": {"eonet": ["severeStorms"], "events": "windstorm OR cyclone", "science": "surface wind speed trend stilling"},
    "PS": {"eonet": ["severeStorms", "tempExtremes"], "events": "high pressure heatwave OR cyclone", "science": "surface pressure trend"},
}


def fetch_json(url: str, params: dict, timeout: int = 25):
    import requests
    r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"answered {r.status_code}")
    return r.json()


GETTER = fetch_json             # tests replace this


def _short(place: str) -> str:
    return (place or "").split(",")[0].strip()[:40]


def eonet(topic: dict, lat: float, lon: float, y0: int, y1: int, getter) -> list:
    if not topic["eonet"]:
        return []
    box = 12.0 if not topic.get("global") else 25.0
    params = {"status": "all", "category": ",".join(topic["eonet"]), "limit": 40, "start": f"{y0}-01-01", "end": f"{y1}-12-31",
              "bbox": f"{max(-180.0, lon - box):.2f},{min(90.0, lat + box):.2f},{min(180.0, lon + box):.2f},{max(-90.0, lat - box):.2f}"}
    js = getter(EONET, params)
    out = []
    for ev in (js.get("events") or []):
        geo = ev.get("geometry") or []
        when = (geo[0].get("date") if geo else "") or ""
        cats = ", ".join(c.get("title", "") for c in (ev.get("categories") or []) if isinstance(c, dict))
        link = ev.get("link") or (((ev.get("sources") or [{}])[0]).get("url") if ev.get("sources") else "") or ""
        out.append({"title": str(ev.get("title") or "Event"), "date": when[:10], "note": cats, "url": link})
    out.sort(key=lambda e: (e["date"] == "", e["date"]))
    return out[:12]


def wikipedia(topic: dict, place: str, getter) -> list:
    q = topic["events"].split(" OR ")[0] + ((" " + _short(place)) if place and not topic.get("global") else "")
    js = getter(WIKI, {"action": "query", "list": "search", "srsearch": q, "format": "json", "srlimit": 8})
    out = []
    for h in (((js.get("query") or {}).get("search")) or []):
        t = h.get("title")
        if t:
            out.append({"title": t, "date": (h.get("timestamp") or "")[:10], "note": "Wikipedia article", "url": "https://en.wikipedia.org/wiki/" + quote(t.replace(" ", "_"))})
    return out


def openalex(topic: dict, place: str, y0: int, y1: int, getter) -> list:
    q = topic["science"] + ((" " + _short(place)) if place and not topic.get("global") else "")
    js = getter(OPENALEX, {"search": q, "per-page": 8, "sort": "cited_by_count:desc", "filter": f"from_publication_date:{y0}-01-01"})
    out = []
    for w in (js.get("results") or []):
        title = w.get("display_name") or w.get("title")
        if not title:
            continue
        url = w.get("doi") or w.get("id") or ""
        out.append({"title": title, "date": str(w.get("publication_year") or ""), "note": f"Scientific paper, cited {w.get('cited_by_count', 0)} times", "url": url})
    return out


def gdelt(topic: dict, place: str, getter) -> list:
    q = f"({topic['events']})" + (f" {_short(place)}" if place and not topic.get("global") else "")
    js = getter(GDELT, {"query": q, "mode": "artlist", "format": "json", "maxrecords": 8, "sort": "datedesc"})
    out = []
    for a in (js.get("articles") or []):
        if a.get("title") and a.get("url"):
            sd = str(a.get("seendate") or "")
            out.append({"title": a["title"], "date": f"{sd[:4]}-{sd[4:6]}-{sd[6:8]}" if len(sd) >= 8 else "", "note": a.get("domain", "News"), "url": a["url"]})
    return out


def find_events(driver: str, place: dict, y0: int, y1: int, getter=None) -> dict:
    getter = getter or GETTER
    topic = TOPICS.get(driver)
    if not topic:
        raise ValueError("No event search is set up for this driver.")
    name = place.get("name", "")
    jobs = {"NASA EONET (natural events)": lambda: eonet(topic, place["lat"], place["lon"], y0, y1, getter),
            "Wikipedia": lambda: wikipedia(topic, name, getter),
            "OpenAlex (scientific papers)": lambda: openalex(topic, name, y0, y1, getter),
            "GDELT (recent news)": lambda: gdelt(topic, name, getter)}

    def one(item):
        label, fn = item
        try:
            return {"source": label, "items": fn(), "error": None}
        except Exception as e:      # noqa: BLE001
            return {"source": label, "items": [], "error": f"Could not reach this source ({str(e)[:100]})."}
    with ThreadPoolExecutor(max_workers=4) as ex:
        res = list(ex.map(one, jobs.items()))
    return {"driver": driver, "sources": res,
            "note": "These are events and papers that match the driver and the place. They are leads to read, not proof that an event caused the change. Check the dates against the years when the driver changed."}


# ======================================================================================================================
# Event explorer: every EONET event near a place, with its track and a plain explanation of what it really is
# ======================================================================================================================
import math

CATEGORIES = {
    "severeStorms": {"title": "Severe storms", "what": "A storm strong enough to be named or tracked: a tropical cyclone (also called a hurricane or typhoon), a thunderstorm system or a windstorm. A cyclone is a huge spinning storm with low pressure at the centre, fed by warm sea water. It brings strong wind, heavy rain and storm surge.",
                     "measured": "Agencies track the storm centre every few hours from satellites, aircraft and radar. EONET keeps only these centre positions, not the size of the storm.",
                     "links": "Heavy rain and cloud, a drop in sunlight and daytime temperature, a fall in air pressure, and a short burst of strong wind."},
    "wildfires": {"title": "Wildfires", "what": "An uncontrolled fire in forest, scrub or grass. Satellites see the heat and the burned ground.", "measured": "Heat sensors on satellites (MODIS, VIIRS) detect active fire spots. EONET gives a position or a burned-area outline.",
                  "links": "Smoke and ash add haze (aerosols), which blocks sunlight and cools the surface for days, while the fire adds carbon dioxide and local heat."},
    "volcanoes": {"title": "Volcanoes", "what": "An eruption of lava, ash and gas from a volcano. Big eruptions throw ash and sulfur dioxide high into the air.", "measured": "Reported by volcano observatories (such as the Smithsonian Global Volcanism Program) and seen from satellites. EONET gives the volcano position and the dates.",
                  "links": "Sulfur gas turns into tiny droplets that reflect sunlight, so very large eruptions can cool the whole world for a year or two. Small ones only change haze and ozone locally for days."},
    "floods": {"title": "Floods", "what": "Water covering land that is normally dry, from heavy rain, rivers overflowing, storm surge or melting snow.", "measured": "Mapped from satellite radar and optical images and from news and agency reports (for example the Dartmouth Flood Observatory).", "links": "Very high rain totals, more water in the air (humidity, water vapour) and more cloud."},
    "drought": {"title": "Drought", "what": "A long spell with much less rain than normal, so soil and rivers dry out.", "measured": "Reported by national agencies and drought monitors from rainfall, soil moisture and plant health seen from satellites.", "links": "Low rain and humidity, higher daytime temperature (no evaporative cooling), and more dust."},
    "dustHaze": {"title": "Dust and haze", "what": "Clouds of desert dust, smoke or smog hanging in the air.", "measured": "Seen on satellite pictures and from aerosol sensors. EONET gives positions and dates.", "links": "Less sunlight reaches the ground, so days are cooler and nights slightly warmer. Dust also seeds clouds and lowers visibility."},
    "tempExtremes": {"title": "Temperature extremes", "what": "A heatwave or cold spell: days far hotter or colder than normal for that place and season.", "measured": "Weather stations and models. EONET records the area and the dates.", "links": "Directly changes the air temperature, humidity and often the surface pressure (heat waves sit under high pressure)."},
    "earthquakes": {"title": "Earthquakes", "what": "Ground shaking from a sudden slip in the earth's crust. The magnitude measures its energy.", "measured": "Seismographs (USGS). EONET gives the position, date and magnitude.", "links": "Almost none on climate variables. A quake can trigger landslides, tsunamis or volcanic activity, which can."},
    "landslides": {"title": "Landslides", "what": "Soil, mud or rock sliding down a slope, often after heavy rain or a quake.", "measured": "Reported by news and agencies (for example NASA's landslide catalogue).", "links": "Usually follows heavy rain. It is a result of weather, not a cause of climate change."},
    "snow": {"title": "Snow", "what": "Unusual or heavy snowfall, for example a blizzard or an early or late snowstorm.", "measured": "Weather agencies and satellite snow-cover maps.", "links": "Cold air temperature, more cloud, and a brighter surface that reflects sunlight."},
    "seaLakeIce": {"title": "Sea and lake ice", "what": "Floating ice on the sea or a lake, including big icebergs that break off glaciers.", "measured": "Tracked by the National Ice Center from satellite images.", "links": "Ice reflects sunlight, so less ice means a darker, warmer surface nearby."},
    "waterColor": {"title": "Water colour", "what": "A change in the colour of sea or lake water, such as a bloom of algae or sediment.", "measured": "Ocean-colour satellite sensors.", "links": "Algae blooms follow warm, calm water. They hardly change land climate variables."},
    "manmade": {"title": "Human-caused events", "what": "A disaster caused or driven by people, such as an industrial fire or a spill.", "measured": "Agency and news reports.", "links": "Local smoke and haze for a short time."},
}

STORM_WORDS = (
    ("cyclone", "Cyclone, hurricane and typhoon are the same kind of storm. The name depends on the ocean: cyclone in the Indian Ocean and South Pacific, hurricane in the Atlantic and east Pacific, typhoon in the northwest Pacific."),
    ("hurricane", "Hurricane, typhoon and cyclone are the same kind of storm. Hurricane is the name used in the Atlantic and east Pacific."),
    ("typhoon", "Typhoon, hurricane and cyclone are the same kind of storm. Typhoon is the name used in the northwest Pacific."),
    ("tropical storm", "A tropical storm is a cyclone in its middle stage, with winds of roughly 63 to 118 km/h. Below that it is a depression, above that a full cyclone, hurricane or typhoon."),
    ("depression", "A tropical depression is the weakest stage of a cyclone, with winds below roughly 63 km/h."),
)


def _km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(h)))


def _compass(a, b):
    y = math.sin(math.radians(b[1] - a[1])) * math.cos(math.radians(b[0]))
    x = math.cos(math.radians(a[0])) * math.sin(math.radians(b[0])) - math.sin(math.radians(a[0])) * math.cos(math.radians(b[0])) * math.cos(math.radians(b[1] - a[1]))
    ang = (math.degrees(math.atan2(y, x)) + 360) % 360
    return ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"][int(((ang + 22.5) % 360) // 45)]


def _day(d: str) -> str:
    return (d or "")[:10]


def _days_between(a: str, b: str):
    from datetime import datetime
    try:
        f = "%Y-%m-%dT%H:%M:%SZ"
        return (datetime.strptime(b[:19] + "Z", f) - datetime.strptime(a[:19] + "Z", f)).total_seconds() / 86400
    except ValueError:
        return None


def parse_event(ev: dict, place: dict) -> dict | None:
    """Turn one raw EONET event into a map-ready record with a plain explanation. Never raises on odd shapes: returns None if there is no usable position."""
    pts, polys = [], []
    for g in (ev.get("geometry") or []):
        try:
            c, d = g.get("coordinates"), g.get("date") or ""
            if g.get("type") == "Point" and c and len(c) >= 2:
                pts.append({"date": d, "lat": float(c[1]), "lon": float(c[0]), "mag": g.get("magnitudeValue"), "unit": g.get("magnitudeUnit")})
            elif g.get("type") == "Polygon" and c and c[0]:
                polys.append({"date": d, "ring": [[float(q[1]), float(q[0])] for q in c[0] if len(q) >= 2]})
        except (TypeError, ValueError):
            continue
    if not pts and not polys:
        return None
    pts.sort(key=lambda p: p["date"])
    polys.sort(key=lambda p: p["date"])
    cat = ((ev.get("categories") or [{}])[0] or {})
    cid = cat.get("id") or ""
    info = CATEGORIES.get(cid, {"title": cat.get("title") or "Event", "what": "A natural event recorded by NASA EONET.", "measured": "See the source link.", "links": "It depends on the type of event."})
    title = " ".join(str(ev.get("title") or "Event").split())
    allp = pts + [{"date": q["date"], "lat": r[0], "lon": r[1]} for q in polys for r in q["ring"][:1]]
    dates = [p["date"] for p in allp if p["date"]]
    start, end = (min(dates), max(dates)) if dates else ("", "")
    span = _days_between(start, end) if dates else None
    dist = sum(_km((a["lat"], a["lon"]), (b["lat"], b["lon"])) for a, b in zip(pts, pts[1:])) if len(pts) > 1 else 0.0
    near = min(((_km((place["lat"], place["lon"]), (p["lat"], p["lon"])), p["date"]) for p in allp), default=None)
    parts = [info["what"]]
    low = title.lower()
    if cid == "severeStorms":
        for w, t in STORM_WORDS:
            if w in low:
                parts.append(t)
                break
    mine = f"This record is “{title}”."
    if len(pts) > 1:
        mine += f" EONET holds {len(pts)} recorded positions from {_day(start)} to {_day(end)}"
        mine += f" ({span:.1f} days)." if span is not None else "."
        if dist > 0:
            mine += f" The centre travelled about {dist:,.0f} km, heading {_compass((pts[0]['lat'], pts[0]['lon']), (pts[-1]['lat'], pts[-1]['lon']))}"
            if span:
                mine += f", at an average of about {dist / (span * 24):.0f} km/h."
            else:
                mine += "."
    elif pts:
        mine += f" It is recorded at one position on {_day(start)}."
    else:
        mine += f" It is recorded as an outline on {_day(start)}."
    mags = [p["mag"] for p in pts if isinstance(p.get("mag"), (int, float))]
    if mags:
        mine += f" Largest recorded size: {max(mags):g} {pts[0].get('unit') or ''}."
    elif cid == "severeStorms":
        mine += " EONET has no wind speed for it, so its strength is not in this file."
    mine += " It is closed (finished) as of " + _day(ev.get("closed")) + "." if ev.get("closed") else " It is still open in EONET."
    parts.append(mine)
    if near:
        parts.append(f"Closest to your place: about {near[0]:,.0f} km, on {_day(near[1])}.")
    srcs = [{"name": s.get("id", ""), "url": s.get("url", "")} for s in (ev.get("sources") or []) if isinstance(s, dict)]
    return {"id": ev.get("id", ""), "title": title, "category": cid, "category_title": info["title"], "start": _day(start), "end": _day(end), "days": span, "km": round(dist), "closed": _day(ev.get("closed")),
            "near_km": round(near[0]) if near else None, "link": ev.get("link", ""), "sources": srcs[:3], "points": pts, "polygons": polys[:3],
            "explain": parts, "measured": info["measured"], "links_to_climate": info["links"]}


def explore(place: dict, y0: int, y1: int, cats: list, radius: float = 10.0, getter=None) -> dict:
    """Every EONET event in a box around the place, in the chosen years and categories."""
    getter = getter or GETTER
    cats = [c for c in cats if c in CATEGORIES] or list(CATEGORIES)
    radius = max(2.0, min(30.0, float(radius)))
    lat, lon = place["lat"], place["lon"]
    params = {"status": "all", "category": ",".join(cats), "limit": 150, "start": f"{y0}-01-01", "end": f"{y1}-12-31",
              "bbox": f"{max(-180.0, lon - radius):.2f},{min(90.0, lat + radius):.2f},{min(180.0, lon + radius):.2f},{max(-90.0, lat - radius):.2f}"}
    js = getter(EONET, params)
    out = [e for e in (parse_event(ev, place) for ev in (js.get("events") or []) if isinstance(ev, dict)) if e]
    out.sort(key=lambda e: e["start"], reverse=True)
    return {"place": place, "radius": radius, "count": len(out), "events": out[:80], "truncated": len(out) > 80,
            "note": "EONET lists mostly events that were large enough to be reported. A quiet list does not mean nothing happened. Positions are storm or event centres, not the full area affected."}


def guide() -> list:
    return [{"id": k, "title": v["title"], "what": v["what"], "measured": v["measured"], "links": v["links"]} for k, v in CATEGORIES.items()]
