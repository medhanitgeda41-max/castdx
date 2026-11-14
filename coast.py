"""Coastal and ocean safety: alerts, long-term outlook and public recommendations for each trend, plus a sea calendar.
The wording is for the public, not for statisticians. The outlook is a straight line from the measured trend, not a physical forecast."""
from __future__ import annotations

import statistics

import trends

LEVELS = ["none", "watch", "warning", "danger"]
OUTLOOK_YEARS = [2030, 2050, 2100]
LIMIT_NOTE = "Alert levels use the speed of change. They are rough guides for the public, not official warnings."
MON = trends.MON

# rising is bad: (watch, warning, danger) in units per decade
_RISE = {
    "TS": (0.1, 0.25, 0.5), "SST_OI": (0.1, 0.25, 0.5),
    "SEALEVEL": (10, 37, 60),
    "WS10M": (0.05, 0.15, 0.3), "WS50M": (0.05, 0.15, 0.3),
    "WAVE": (0.03, 0.08, 0.15),
    "T2M": (0.1, 0.3, 0.5), "T2M_MAX": (0.1, 0.3, 0.5),
}

_SEA_WARM = ("Global warming heats the ocean. Calm, sunny weather and weaker wind add to it.",
             "The sea is getting warmer. Warm seas feed stronger storms, bleach coral and push fish away.",
             ["Swimmers: warm water brings jellyfish and harmful algae. Read beach notices and do not swim in scummy or discoloured water.", "Follow local heatwave and coral warnings.", "Protect coral, seagrass and mangroves, and respect fishing limits.", "Fishers: expect fish to move. Watch for new species and new seasons."])
_SEA_LEVEL = ("Warming expands sea water and melts ice. Land that sinks near the gauge adds to it.",
              "The sea is rising here, compared with the land. High tides and storms reach further inland.",
              ["Know your flood zone and your evacuation route.", "Keep homes, stores and power equipment above flood level.", "Protect mangroves, dunes and reefs. They slow waves.", "Build new homes and roads above the future sea level.", "Check wells and farms near the coast for salt water."])
_WIND = ("Changes in weather patterns and warmer seas can make winds stronger.",
         "Winds are getting stronger. Storms and rough seas are more likely.",
         ["Do not swim or paddle when the wind is strong. It pushes swimmers away from shore.", "Tie down boats, roofs and loose objects before storms.", "Check the marine forecast before going out. Boats and ferries: set clear wind limits.", "Keep a storm kit and a family plan."])
_WAVE = ("Stronger winds and storms build bigger waves.",
         "Waves are getting bigger. Beaches erode faster and harbours get rough.",
         ["Swim only where lifeguards are on duty, and obey the flags.", "Stay off rocks, piers and beaches in high surf.", "Caught in a rip current? Stay calm, float, and swim sideways along the shore, not against it.", "Check harbours and sea walls for bigger waves. Keep dunes and mangroves as natural wave barriers."])
_HEAT = ("A warming climate.", "Hot days are getting hotter. Heat is risky on beaches and boats.",
         ["Drink water and rest in the shade in the middle of the day.", "Put up shade at beaches and ports.", "Check on older people and children."])
_RAIN_UP = ("Warmer air holds more water, so rain gets heavier.", "Rain is getting heavier. Streets and shores flood more, and runoff pollutes the water.",
            ["Do not swim for a day or two after heavy rain. Runoff carries sewage and germs to the shore.", "Keep drains and canals clear.", "Know your flood route."])
_RAIN_DOWN = ("Rain follows big weather patterns such as El Niño and La Niña.", "Rain is getting lighter. Less fresh water, and salt water can creep into wells.",
              ["Save fresh water.", "Test wells near the coast for salt.", "Plan water supply for dry months."])

# precaution policy for governments and planners, by kind of harmful trend
POLICY = {
    "sea": ["Set up marine heatwave and coral watch, with early warnings for fishers and tourism.",
            "Protect and restore reefs, seagrass and mangroves.",
            "Plan fishing limits that can change as the sea warms.",
            "Test beach water for algae and germs, and publish the results."],
    "level": ["Set building limits and setback lines for new development near the shore.",
              "Update flood maps and zoning every few years.",
              "Raise or move ports, roads, power and water plants that sit low.",
              "Use natural defences (mangroves, dunes, reefs) with sea walls where needed.",
              "Plan managed retreat for the areas that cannot be protected."],
    "wind": ["Use stronger building codes for coastal roofs, signs and power lines.",
             "Run storm early warning and evacuation drills.",
             "Set clear wind limits for ports, ferries and beaches."],
    "wave": ["Fund lifeguards, flags and beach safety rules.",
             "Monitor beach erosion and repair breakwaters and sea walls.",
             "Keep people and buildings back from eroding shores."],
    "heat": ["Make a heat action plan: shade, drinking water and rest rules for outdoor work.",
             "Plan cooling places for older people and children."],
    "rain_up": ["Upgrade drains and stormwater systems near the coast.",
                "Watch sewage overflows and close beaches after heavy rain."],
    "rain_down": ["Protect fresh water supplies and test coastal wells for salt.",
                  "Plan water saving for dry months."],
}
POLICY["soil_dry"] = ["Make a drought plan with early warnings for farmers and water managers.",
                      "Build and protect water storage: ponds, small dams and groundwater recharge.",
                      "Support water-saving irrigation, drought-tolerant crops and seed.",
                      "Keep soil covered (mulch, cover crops, tree belts) and add organic matter so it holds water.",
                      "Monitor soil moisture and publish dry-spell alerts every season.",
                      "Plan land use and grazing limits for the driest areas."]
POLICY["soil_wet"] = ["Improve drainage and flood storage in low fields.", "Keep waterways clear and protect slopes against erosion.",
                      "Warn farmers early about waterlogging and delay heavy machines."]
_SOIL_HARM = {"T2M": ("rising", "hotter air (dries the soil)"), "T2M_MAX": ("rising", "hotter days (dries the soil)"), "TS": ("rising", "hotter ground"),
              "WS10M": ("rising", "stronger wind (dries the soil)"), "PRECTOTCORR": ("falling", "less rain"), "RH2M": ("falling", "drier air"),
              "ALLSKY_SFC_SW_DWN": ("rising", "more sunlight (more evaporation)"), "CLOUD_AMT": ("falling", "less cloud"),
              "CO2": ("rising", "more carbon dioxide (more warming)"), "QV2M": ("falling", "less water vapour in the air")}


def soil_policy(vid: str, alert: dict | None, trend: dict | None) -> list:
    if not alert or alert.get("verdict") != "Not good":
        return []
    return list(POLICY["soil_wet" if (trend or {}).get("direction") == "rising" else "soil_dry"])


_POLICY_KEY = {"TS": "sea", "SST_OI": "sea", "SEALEVEL": "level", "WS10M": "wind", "WS50M": "wind", "WAVE": "wave", "T2M": "heat", "T2M_MAX": "heat"}


# which driver trends are harmful for the coast: id -> (direction that is harmful, plain words, policy key)
_HARM = {"TS": ("rising", "warmer sea", "sea"), "T2M": ("rising", "hotter air", "heat"), "T2M_MAX": ("rising", "hotter days", "heat"),
         "WS10M": ("rising", "stronger wind", "wind"), "WS50M": ("rising", "stronger wind", "wind"), "PRECTOTCORR": ("rising", "heavier rain", "rain_up"),
         "PS": ("falling", "lower air pressure (more storms)", "wind"), "CO2": ("rising", "more carbon dioxide (more warming)", "sea")}


def driver_harm(vid: str, trend: dict | None, domain: str = "coast") -> dict | None:
    """Is this possible cause itself trending in a way that is bad for the coast (or for the soil)?"""
    if domain == "soil":
        h = _SOIL_HARM.get(vid)
        key = "soil_dry"
    else:
        h = _HARM.get(vid)
        key = h[2] if h else None
    if not h or not trend or not trend.get("significant") or trend.get("direction") != h[0]:
        return None
    return {"text": h[1], "policy": list(POLICY[key])}


def _dir(t):
    if not t or not t["significant"]:
        return "flat"
    return t["direction"]


def per_decade(t: dict) -> float:
    return t["sen"]["slope_decade"] * 10.0 / (t.get("rate_years") or 10.0)


def A(level, text, verdict, means, cause, rec):
    return {"level": level, "text": text, "verdict": verdict, "means": means, "cause": cause, "recommend": list(rec)}


def _level(vid: str, rate: float) -> str:
    w, g, d = _RISE[vid]
    return "danger" if rate >= d else "warning" if rate >= g else "watch" if rate >= w else "none"


_TEXT = {"TS": "Early warning: warming sea", "SST_OI": "Early warning: warming sea", "SEALEVEL": "Early warning: rising sea",
         "WS10M": "Early warning: stronger wind", "WS50M": "Early warning: stronger wind", "WAVE": "Early warning: bigger waves",
         "T2M": "Early warning: hotter air", "T2M_MAX": "Early warning: hotter days"}
_KIND = {"TS": _SEA_WARM, "SST_OI": _SEA_WARM, "SEALEVEL": _SEA_LEVEL, "WS10M": _WIND, "WS50M": _WIND, "WAVE": _WAVE, "T2M": _HEAT, "T2M_MAX": _HEAT}


def _core(vid: str, t: dict | None) -> dict | None:
    if not t:
        return None
    d = _dir(t)
    if vid in _RISE:
        cause, means, rec = _KIND[vid]
        if d == "rising":
            lv = _level(vid, per_decade(t))
            if lv == "none":
                return A("good", "Small rise", "No change", "It is rising very slowly. Keep watching.", "", [])
            txt = {"watch": _TEXT[vid], "warning": "Alert: " + _TEXT[vid].split(": ")[1] + " fast", "danger": "Alert: " + _TEXT[vid].split(": ")[1] + " very fast"}[lv]
            return A(lv, txt, "Not good", means, cause, rec)
        if d == "falling":
            if vid == "SEALEVEL":
                return A("mixed", "Mixed: sea level falling here", "Mixed", "Sea level is falling compared with the land here. The land may be rising.", "Land rising, or changes in currents and winds.", [])
            return A("good", "Good: easing", "Good", "It is easing. Less risk from this.", "", [])
        return A("none", "No alert", "No change", "No real change.", "", [])
    if vid == "PRECTOTCORR":
        if d == "rising":
            return A("watch", "Early warning: heavier rain", "Not good", _RAIN_UP[1], _RAIN_UP[0], _RAIN_UP[2])
        if d == "falling":
            return A("watch", "Early warning: less rain", "Not good", _RAIN_DOWN[1], _RAIN_DOWN[0], _RAIN_DOWN[2])
        return A("none", "No alert", "No change", "No real change.", "", [])
    if vid == "WAVEP":
        return A("mixed", "Mixed: wave period changing", "Mixed", "Longer waves carry more energy and can reach the shore from far away.", "Storms far out at sea.", []) if d != "flat" else A("none", "No alert", "No change", "No real change.", "", [])
    if vid == "PS":
        return A("mixed", "Mixed: pressure " + ("falling" if d == "falling" else "rising"), "Mixed",
                 "Lower pressure goes with more storms and a slightly higher sea. Higher pressure goes with calm, clear weather." if d != "flat" else "", "", []) if d != "flat" else A("none", "No alert", "No change", "No real change.", "", [])
    if d == "flat":
        return A("none", "No alert", "No change", "No real change.", "", [])
    return A("mixed", "Mixed: " + d, "Mixed", "This is changing. On its own it is not a danger, but it can add to other changes.", "", [])


def outlook(t: dict | None, unit: str = "") -> dict | None:
    """Straight-line values for 2030, 2050 and 2100 from the trend and its 95% range. Not a forecast."""
    if not t or not t["significant"]:
        return None
    ry = t.get("rate_years") or 10.0
    x_end, y_end = t["sen"]["line"][1]
    s, lo, hi = t["sen"]["slope_decade"] / ry, t["sen"]["ci_low"] / ry, t["sen"]["ci_high"] / ry
    rows = []
    for Y in OUTLOOK_YEARS:
        dt = Y - x_end
        if dt <= 0:
            continue
        a, b = y_end + lo * dt, y_end + hi * dt
        rows.append({"year": Y, "value": y_end + s * dt, "change": s * dt, "low": min(a, b), "high": max(a, b)})
    return {"rows": rows, "now": y_end, "note": "A straight line from the measured trend. Real change may speed up or slow down."} if rows else None


def alert_for(vid: str, t: dict | None) -> dict | None:
    a = _core(vid, t)
    if a is None:
        return None
    key = _POLICY_KEY.get(vid) or (("rain_up" if _dir(t) == "rising" else "rain_down") if vid == "PRECTOTCORR" else None)
    a["policy"] = list(POLICY[key]) if key and a.get("verdict") == "Not good" else []
    o = outlook(t)
    if o and a["level"] in ("watch", "warning", "danger", "mixed") and o["rows"]:
        r = next((x for x in o["rows"] if x["year"] == 2050), o["rows"][-1])
        u = trends.BY_ID.get(vid, {}).get("unit", "")
        a["ahead"] = f"By {r['year']}: {r['change']:+.2f} {u} from now (range {r['low'] - o['now']:+.2f} to {r['high'] - o['now']:+.2f}).".replace("  ", " ")
    return a


# ---------------------------------------------------------------- sea calendar
CAL_NEED = ["TS", "WS10M", "PRECTOTCORR", "T2M"]
DAYS = [0, 31, 28.25, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def swim_check(sea, wind, rain_mm, wave) -> tuple:
    """Swimming rating for a typical month: ("Good" | "Caution" | "Avoid", [reasons]). Rough rules from monthly averages, not a beach forecast."""
    bad, warn = [], []
    if wave is not None:
        if wave >= 2.5:
            bad.append("Big waves and rip currents")
        elif wave >= 1.5:
            warn.append("Choppy, rip currents possible")
    if wind is not None:
        if wind >= 8:
            bad.append("Strong wind, rough sea")
        elif wind >= 6:
            warn.append("Windy")
    if rain_mm is not None and rain_mm >= 150:
        warn.append("Heavy rain: dirty water")
    if sea is not None:
        if sea < 16:
            bad.append("Cold water")
        elif sea < 20:
            warn.append("Cool water")
        elif sea >= 30.5:
            warn.append("Very warm water: jellyfish, algae")
    return ("Avoid" if bad else "Caution" if warn else "Good"), bad + warn


def calendar(M: dict, years: int) -> list:
    y0, m0, y1, m1 = M["_p"]
    rows = []
    for m in M["_sel"]:
        def clim(p):
            v = [M[p][f"{y}{m:02d}"] for y in range(M["_y0"], M["_y1"] + 1) if (y0, m0) <= (y, m) <= (y1, m1) and f"{y}{m:02d}" in M[p]]
            return statistics.fmean(v) if v else None
        rain, sea, wind, air = clim("PRECTOTCORR"), clim("TS"), clim("WS10M"), clim("T2M")
        wave = clim("WAVE") if M.get("WAVE") else None
        rain_mm = None if rain is None else rain * DAYS[m]
        swim, why = swim_check(sea, wind, rain_mm, wave)
        rows.append({"month": MON[m], "rain_mm": rain_mm, "sea": sea, "wind": wind, "air": air, "wave": wave, "swim": swim, "hazards": why, "flags": [], "good": False})

    def top(key, n, name, high=True):
        vals = sorted([r for r in rows if r[key] is not None], key=lambda r: r[key], reverse=high)
        for r in vals[:n]:
            r["flags"].append(name)
    n = 3 if len(rows) >= 6 else 1
    top("wind", n, "Windiest")
    top("rain_mm", n, "Wettest")
    top("sea", n, "Warmest sea")
    top("sea", n, "Coolest sea", high=False)
    for r in rows:
        r["good"] = r["swim"] == "Good"
    return rows


def calendar_summary(rows: list) -> list:
    def names(f):
        return [r["month"] for r in rows if f(r)]
    out = [("Good for swimming", ", ".join(names(lambda r: r["swim"] == "Good")) or "none")]
    av = names(lambda r: r["swim"] == "Avoid")
    if av:
        out.append(("Avoid swimming", ", ".join(av)))
    for lab in ("Windiest", "Wettest", "Warmest sea", "Coolest sea"):
        n = names(lambda r, lab=lab: lab in r["flags"])
        if n:
            out.append((lab, ", ".join(n)))
    return out


def sea_calendar(place: dict, y0: int, m0: int, y1: int, m1: int, months: list | None, getter=trends.get_json) -> dict:
    sel = sorted({int(m) for m in (months or range(1, 13)) if 1 <= int(m) <= 12})
    if not sel:
        raise ValueError("Choose at least one month of the year.")
    cell = trends.cell_of(place["lat"], place["lon"])
    raw = trends.fetch_cell(cell, CAL_NEED, y0, y1, getter)
    if not raw.get("TS") and not raw.get("WS10M"):
        raise trends.DataError("NASA POWER did not return sea data for this place.")
    M = {k: raw.get(k) or {} for k in CAL_NEED}
    try:
        M["WAVE"] = trends.fetch_cell(cell, ["WAVE"], y0, y1, getter).get("WAVE") or {}
    except Exception:      # noqa: BLE001
        M["WAVE"] = {}
    M["_y0"], M["_y1"], M["_p"], M["_sel"] = y0, y1, (y0, m0, y1, m1), sel
    rows = calendar(M, y1 - y0 + 1)
    return {"calendar": rows, "summary": calendar_summary(rows)}


SWIM_TIPS = [
    "Swim only at beaches with lifeguards, between the flags.",
    "Rip current? Stay calm, float, then swim sideways along the shore. Never swim straight against it.",
    "Do not swim for 1 to 2 days after heavy rain. Runoff carries sewage and germs.",
    "Skip scummy, green or red water (harmful algae) and beaches with jellyfish warnings.",
    "Strong or offshore wind pushes swimmers and floats away from shore. Stay out.",
    "Cold water causes shock and cramps. Warm water can cause heat stroke. Rest and drink water.",
    "Never swim alone, after alcohol, or at night. Watch children all the time.",
    "Thunder or lightning: leave the water and the beach at once.",
]
