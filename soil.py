"""Soil and farm report for one place.

Purpose: see soil-related trends early, say how soon they could become harmful, look at what is behind them (statistical cause tests and natural events),
and turn that into plain actions to take before the trend turns dangerous.

All numbers come from NASA POWER monthly data (MERRA-2 soil wetness, rain, temperature, humidity, wind, sunlight) at the grid cell of the place.
Soil wetness here is a model value from 0 (dry) to 1 (saturated), not a probe reading. The danger levels are rough rules of thumb that depend on the
soil type, so they can be changed by the user. This is guidance, not an agronomic prescription: a soil test and a local agronomist have the last word.
"""
from __future__ import annotations

import statistics
from concurrent.futures import ThreadPoolExecutor

import causes
import events
import stats
import trends

LIMITS = {"dry_danger": 0.25, "dry_watch": 0.35, "wet_watch": 0.75, "wet_danger": 0.85}
LIMIT_NOTE = ("Rough levels for root-zone wetness (0 dry to 1 saturated): below 0.25 most crops are short of water, below 0.35 they are close to it, "
              "above 0.75 roots start to lack air, above 0.85 the soil is waterlogged. Sandy and clay soils differ, so change them to suit your soil.")
HEAT_WATCH, HEAT_DANGER, FROST_BELOW, GROW_MIN_T = 35.0, 40.0, 2.0, 8.0
NEED = ["GWETROOT", "GWETTOP", "GWETPROF", "TS", "PRECTOTCORR", "T2M", "T2M_MAX", "T2M_MIN", "RH2M", "WS10M", "ALLSKY_SFC_SW_DWN"]
LEVELS = ["ok", "watch", "warning", "danger"]
LEVEL_TEXT = {"ok": "Fine", "watch": "Watch", "warning": "Warning", "danger": "Danger", "nodata": "No data"}
SEASONS = [("Dec to Feb", [12, 1, 2]), ("Mar to May", [3, 4, 5]), ("Jun to Aug", [6, 7, 8]), ("Sep to Nov", [9, 10, 11])]
DAYS = [0, 31, 28.25, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
MAX_AHEAD = 60.0
EVENTS_ON = False      # the page does not show events any more, so they are not fetched
EVENT_CATS = ["drought", "floods", "wildfires", "dustHaze", "severeStorms", "tempExtremes", "landslides", "volcanoes"]

GLOSSARY = [
    ("Soil wetness (0 to 1)", "How full of water the soil is compared with completely saturated. 0 is bone dry, 1 is soaked. It is a NASA model value, not a probe reading."),
    ("Root zone", "The top metre of soil, where most crop roots take up water. Its dry-season level is the main warning light in this report."),
    ("Surface layer", "The top 5 cm. It dries and wets fast, and decides crusting, dust and seed sprouting."),
    ("Waterlogging", "Soil so full of water that roots cannot get air. Many crops die or stunt within days."),
    ("Wilting stress", "When the soil holds too little water for roots to pull in. Yields fall long before plants visibly wilt."),
    ("Organic matter", "Rotted plant and animal material in the soil. It holds water like a sponge, feeds soil life and glues soil into crumbs that resist erosion."),
    ("Erosion", "Soil carried away by wind or running water. The fertile fine part goes first, and new soil forms only over centuries."),
    ("Mulch and cover crops", "A layer of straw, leaves or living plants on the ground. They shade the soil, slow evaporation and stop erosion."),
    ("Compaction", "Soil pressed so tight by machines or animals that water and roots cannot enter. Worst when the soil is wet."),
    ("Salinity", "Salts building up in the soil, often from irrigating hot dry land. It cannot be seen in this data, so test the soil."),
]


def _series(monthly: dict, y0: int, y1: int, months: list) -> dict:
    return trends.build_series(monthly, y0, 1, y1, 12, months, "year")


def _tr(s: dict, name: str, unit: str, pct: bool = False):
    return stats.analyse(s["x"], s["values"], name, unit, pct, min_n=8, rate=(10.0, "decade"))


def _slim(t: dict | None) -> dict | None:
    if not t:
        return None
    return {k: t[k] for k in ("direction", "significant", "slope_decade", "p", "sentence", "verdict", "n", "mean", "first", "last", "pct", "total")} | {
        "ci_low": t["sen"]["ci_low"], "ci_high": t["sen"]["ci_high"], "line": t["sen"]["line"], "now": t["sen"]["line"][1][1]}


def time_to_limit(t: dict | None, limit: float, going: str) -> dict:
    """Straight-line extrapolation of the Theil-Sen trend to a limit. going is "down" or "up". A rough guide, not a forecast."""
    if not t:
        return {"state": "none"}
    now, slope = t["sen"]["line"][1][1], t["sen"]["slope_decade"] / 10.0
    sg = -1.0 if going == "down" else 1.0
    gap = (limit - now) * sg
    if gap <= 0:
        return {"state": "reached", "now": now}
    if not t["significant"] or slope * sg <= 0:
        return {"state": "none", "now": now}
    lo, hi = t["sen"]["ci_low"] / 10.0 * sg, t["sen"]["ci_high"] / 10.0 * sg
    fast, slow = max(lo, hi), min(lo, hi)
    yrs = gap / (slope * sg)
    out = {"state": "approaching", "years": yrs, "earliest": gap / fast if fast > 0 else None, "latest": gap / slow if slow > 0 else None, "now": now}
    if yrs > MAX_AHEAD:
        out["state"] = "far"
    return out


def _ttl_text(tt: dict, last_year: int, limit_name: str) -> str:
    s = tt["state"]
    if s == "reached":
        return f"Already past the {limit_name} level."
    if s == "approaching":
        y = tt["years"]
        txt = f"If the trend keeps going in a straight line, the {limit_name} level is reached in about {y:.0f} years (around {last_year + round(y)})."
        e, l = tt.get("earliest"), tt.get("latest")
        if e is not None:
            txt += f" The uncertainty range is {e:.0f} years to " + (f"{l:.0f} years." if l is not None and l <= 200 else "never, because the slope could be flatter.")
        return txt + " This is a straight-line guide, not a forecast."
    if s == "far":
        return f"At the current pace the {limit_name} level is more than {MAX_AHEAD:.0f} years away."
    return f"The trend is not heading toward the {limit_name} level."


def _worse(a: str, b: str) -> str:
    return a if LEVELS.index(a) >= LEVELS.index(b) else b


def _floor(level: str, to: str) -> str:
    return _worse(level, to)


def build_issues(S: dict, lim: dict, last_year: int) -> list:
    """S holds the slim trend results. Returns one record per soil issue."""
    out = []
    # 1 drying in the dry season
    t, ds = S.get("root_dry"), S.get("root_dry")
    if t:
        now = t["now"]
        tt = time_to_limit(S["_raw"]["root_dry"], lim["dry_danger"], "down")
        lvl = "ok"
        if now <= lim["dry_danger"]:
            lvl = "danger"
        elif now <= lim["dry_watch"]:
            lvl = "warning"
        if t["significant"] and t["direction"] == "falling":
            lvl = _floor(lvl, "watch")
            if tt["state"] == "approaching" and tt["years"] <= 15:
                lvl = _floor(lvl, "warning")
        out.append({"id": "dry", "title": "Soil drying out in the dry season", "level": lvl, "var": "root_dry", "limit": lim["dry_danger"], "limit_name": "crop water stress", "going": "down",
                    "what": f"In the driest months the root zone holds about {now:.2f} of its capacity (0 dry, 1 full). " + t["sentence"],
                    "ttl": _ttl_text(tt, last_year, "crop water stress"), "ttl_raw": tt})
    # 2 waterlogging in the wet season
    t = S.get("root_wet")
    if t:
        now = t["now"]
        tt = time_to_limit(S["_raw"]["root_wet"], lim["wet_danger"], "up")
        lvl = "ok"
        if now >= lim["wet_danger"]:
            lvl = "danger"
        elif now >= lim["wet_watch"]:
            lvl = "warning"
        if t["significant"] and t["direction"] == "rising":
            lvl = _floor(lvl, "watch")
            if tt["state"] == "approaching" and tt["years"] <= 15:
                lvl = _floor(lvl, "warning")
        out.append({"id": "wet", "title": "Waterlogging in the wet season", "level": lvl, "var": "root_wet", "limit": lim["wet_danger"], "limit_name": "waterlogging", "going": "up",
                    "what": f"In the wettest months the root zone holds about {now:.2f} of its capacity. " + t["sentence"],
                    "ttl": _ttl_text(tt, last_year, "waterlogging"), "ttl_raw": tt})
    # 3 dry surface and wind: crusting, dust, erosion
    top, wind = S.get("top_dry"), S.get("wind")
    if top or wind:
        lvl, bits = "ok", []
        dry_top = bool(top and top["now"] <= lim["dry_watch"])
        wind_up = bool(wind and wind["significant"] and wind["direction"] == "rising")
        if top:
            bits.append(f"The top 5 cm holds about {top['now']:.2f} of its capacity in the driest months. " + top["sentence"])
        if wind:
            bits.append("Wind: " + wind["sentence"])
        if dry_top:
            lvl = "watch"
        if wind_up:
            lvl = _floor(lvl, "watch")
        if dry_top and wind_up:
            lvl = "warning"
        if top and top["now"] <= lim["dry_danger"] and wind_up:
            lvl = "danger"
        out.append({"id": "erosion", "title": "Dry, loose surface soil and wind erosion", "level": lvl, "var": "top_dry", "what": " ".join(bits), "ttl": "A dry top layer blows away or crusts over. Both lose fertile soil and seedlings.", "ttl_raw": None})
    # 4 less rain
    t = S.get("rain")
    if t:
        lvl = "ok"
        if t["significant"] and t["direction"] == "falling":
            lvl = "watch"
            if t["pct"] is not None and t["pct"] <= -15:
                lvl = "warning"
        out.append({"id": "rain", "title": "Rain supply", "level": lvl, "var": "rain", "what": t["sentence"] + (" Heavier bursts of rain on a drying soil can run off instead of soaking in." if t["significant"] and t["direction"] == "rising" else ""),
                    "ttl": "Rain is the main water input to the soil.", "ttl_raw": None})
    # 5 heat
    t = S.get("heat")
    if t:
        now, lvl = t["now"], "ok"
        if t["significant"] and t["direction"] == "rising":
            lvl = "watch"
        if now >= HEAT_WATCH:
            lvl = _floor(lvl, "warning")
        if now >= HEAT_DANGER:
            lvl = "danger"
        tt = time_to_limit(S["_raw"]["heat"], HEAT_DANGER, "up")
        out.append({"id": "heat", "title": "Heat in the hottest month", "level": lvl, "var": "heat", "limit": HEAT_DANGER, "limit_name": "extreme heat", "going": "up",
                    "what": f"The average daily high of the hottest month is about {now:.1f} °C. " + t["sentence"], "ttl": _ttl_text(tt, last_year, f"{HEAT_DANGER:.0f} °C"), "ttl_raw": tt})
    return out


# ---------------------------------------------------------------- what a trend means, and for whom
def _dir(t):
    if not t or not t["significant"]:
        return "flat"
    return t["direction"]


_MEANING = {
    # id: {direction: (tone, soil meaning, public meaning, positive actions)}
    "dry": {
        "falling": ("bad", "The root zone holds less water in the driest months each decade. Roots reach for water that is not there, soil life slows and organic matter is lost faster.",
                    "Less water in farm soil means smaller harvests, higher food prices and more dust near towns. Past the danger level, farms need much more irrigation water.",
                    ["Cover the soil (mulch, residue, cover crops) so it keeps what water it gets.", "Add compost or manure to raise organic matter, which holds water.", "Store rain in swales, ponds or tanks for the dry months."]),
        "rising": ("good", "More water stays in the root zone in the driest months. That helps roots, soil life and yields.",
                   "A steadier water supply for farms, steadier food production and less dust.",
                   ["Protect the gain: keep the soil covered and keep organic matter high.", "Do not over-irrigate: extra water can bring waterlogging or wash nutrients away.", "Use the extra moisture to lengthen the growing season, but check the wet-season level first."]),
        "flat": ("neutral", "No clear change in dry-season soil water.", "No change for farms or food supply from this measure.", ["Keep covering the soil and keep a yearly soil test going so a change is caught early."]),
    },
    "wet": {
        "rising": ("bad", "Soil in the wet season is getting wetter. Roots lack air when it is saturated, machines crush wet soil and nutrients wash away.",
                   "More waterlogged fields and more flood and mud risk, with delays in planting and harvest.",
                   ["Open and clear drains before the rains.", "Use raised beds or ridges on low ground.", "Plant deep-rooted cover crops to open the soil and take up water."]),
        "falling": ("mixed", "Soil in the wet season is getting less wet. That lowers waterlogging, but it can also mean less water stored for the dry months.",
                    "Fewer flooded fields, but possibly less water banked for the dry season.",
                    ["Use the drier wet season to work the land earlier.", "Keep a store of water (ponds, tanks) so the dry months stay covered."]),
        "flat": ("neutral", "No clear change in wet-season soil water.", "No change for farms or flood risk from this measure.", ["Keep drains clear and watch the wet-season level."]),
    },
    "erosion": {
        "bad": ("bad", "The top layer is dry and loose, or the wind is getting stronger. Bare dry soil loses its fine, fertile part first.",
                "More dust in the air and fewer nutrients in farmland. Over years this lowers yields.",
                ["Plant windbreaks across the main wind direction.", "Never leave soil bare in dry, windy months.", "Plough along the contour and keep vegetation strips on slopes."]),
        "good": ("good", "The surface soil is moist enough and the wind is not growing, so little soil is being lost.", "Cleaner air and more stable farmland.", ["Keep the soil covered to keep it this way."]),
    },
    "rain": {
        "falling": ("bad", "Less rain over the years means less water going into the soil.", "Less water for farms, wells and rivers, and a higher risk of drought years.",
                    ["Catch and store rain (swales, ponds, tanks).", "Choose crops and varieties that need less water.", "Shift planting dates to the rainiest months."]),
        "rising": ("mixed", "More rain brings more water to the soil. It is good for crops, but heavy bursts can run off the surface and wash soil away.", "More water for farms and wells, but also more flood and erosion risk in heavy storms.",
                   ["Keep ground covered so heavy rain soaks in instead of running off.", "Build bunds, contour ridges or grass strips on slopes.", "Make sure drains can handle bigger flows."]),
        "flat": ("neutral", "No clear change in rain.", "No change for water supply from this measure.", ["Keep watching: a few dry years can still matter even if the long-term trend is flat."]),
    },
    "heat": {
        "rising": ("bad", "The hottest month is getting hotter. Heat dries the soil faster, breaks down organic matter and stresses crops at flowering.", "More heat stress for crops, farm workers and animals, and a higher water demand.",
                   ["Choose heat-tolerant varieties and sow so flowering misses the hottest weeks.", "Mulch and shade the soil.", "Water in the cool of the day."]),
        "falling": ("good", "The hottest month is getting cooler, which eases heat stress and evaporation.", "Less heat stress for crops and people.", ["Keep the soil covered so it stays cool and moist."]),
        "flat": ("neutral", "No clear change in the heat of the hottest month.", "No change from this measure.", ["Keep watching."]),
    },
}


def interpret(i: dict, S: dict) -> dict:
    """Add verdict, tone and plain meaning to an issue. Verdict words: Alert, Early warning, Good news, Mixed news, No clear change."""
    t = S.get(i["var"])
    id_, lv = i["id"], i["level"]
    if id_ == "erosion":
        bad = lv != "ok"
        tone, soil_m, pub, pos = _MEANING["erosion"]["bad" if bad else "good"]
    else:
        d = _dir(t)
        tone, soil_m, pub, pos = _MEANING[id_][d]
        if lv in ("danger", "warning") and tone in ("neutral", "good"):
            tone = "bad"
            soil_m = "The level is not improving and it already sits in the danger range. " + soil_m
    if lv == "danger":
        verdict = "Alert: act now"
    elif lv == "warning":
        verdict = "Alert: act soon"
    elif lv == "watch":
        verdict = "Early warning"
    elif tone == "good":
        verdict = "Good news"
    elif tone == "mixed":
        verdict = "Mixed news"
    else:
        verdict = "No clear change"
    i["meaning"] = {"verdict": verdict, "tone": tone, "soil": soil_m, "public": pub, "positive": pos}
    return i


_OTHER = {
    "rain": {"falling": ("bad", "Less water reaches the soil."), "rising": ("mixed", "More water for the soil, but heavy bursts can wash soil away.")},
    "temp": {"rising": ("bad", "Warmer air dries the soil faster and speeds the loss of organic matter."), "falling": ("good", "Cooler air slows evaporation.")},
    "rh": {"falling": ("bad", "Drier air pulls more water out of the soil and plants."), "rising": ("good", "Moister air slows the drying of soil.")},
    "wind": {"rising": ("bad", "Stronger wind dries the surface and lifts dust, so erosion risk rises."), "falling": ("good", "Calmer air means less drying and less dust.")},
    "sun": {"rising": ("mixed", "More sunlight helps plants grow but also evaporates more soil water."), "falling": ("mixed", "Less sunlight means less evaporation but slower growth.")},
    "ts": {"rising": ("bad", "A hotter ground surface speeds evaporation, burns off organic matter and can scorch seedlings."), "falling": ("good", "A cooler ground surface slows evaporation and organic matter loss.")},
    "cold": {"rising": ("mixed", "Warmer cold nights mean fewer frosts, which helps crops, but some pests survive the winter."), "falling": ("mixed", "Colder nights bring more frost risk to sensitive crops.")},
}


def interpret_other(k: str, t: dict) -> dict:
    d = _dir(t)
    tone, txt = _OTHER.get(k, {}).get(d, ("neutral", "No clear change, so nothing to act on from this measure."))
    return {"tone": tone, "text": txt}


SIMPLE = {
    "dry": {"falling": "The soil is getting drier in the dry months. Plants may not find enough water.", "rising": "The soil keeps more water in the dry months. Good for plants.", "flat": "No real change in the dry months."},
    "wet": {"rising": "The soil is getting wetter in the rainy months. Roots may not get air.", "falling": "The soil is less wet in the rainy months. Fewer floods, but less water saved.", "flat": "No real change in the rainy months."},
    "erosion": {"bad": "The top soil is dry and loose. Wind can blow it away.", "good": "The top soil is holding together."},
    "rain": {"falling": "Less rain each year.", "rising": "More rain each year. Good for water, but heavy storms can wash soil away.", "flat": "No real change in rain."},
    "heat": {"rising": "The hottest month keeps getting hotter. Hot soil dries fast and plants get stressed.", "falling": "The hottest month is cooling. Less stress on soil and plants.", "flat": "No real change in the heat."},
}
VERDICT_HELP = {"Alert: act now": "Act now. It is already in the danger zone.", "Alert: act soon": "Act this season. It is close to danger.", "Early warning": "Not dangerous yet, but it is moving the wrong way.",
                "Good news": "It is moving the right way. Keep it up.", "Mixed news": "Some good, some bad.", "No clear change": "Nothing to do now."}


def simple_text(i: dict, S: dict) -> str:
    if i["id"] == "erosion":
        return SIMPLE["erosion"]["bad" if i["level"] != "ok" else "good"]
    d = _dir(S.get(i["var"]))
    t = SIMPLE[i["id"]][d]
    if i["level"] in ("danger", "warning") and d == "flat":
        t = "Already in the danger range. " + t
    return t


def when_text(i: dict) -> str:
    tt = i.get("ttl_raw")
    if not tt:
        return ""
    if tt["state"] == "approaching":
        return f"About {tt['years']:.0f} years to the danger level if it keeps going."
    return ""


def cause_text(i: dict, cause: dict | None, S: dict) -> tuple:
    """Short possible cause and whether it came from the data tests."""
    id_ = i["id"]
    if id_ in ("dry", "wet") and cause:
        if cause.get("best"):
            return "Likely " + " and ".join(d["name"].lower() for d in cause["best"]) + ".", "tested"
        strong = [d for d in cause["drivers"] if d["score"] >= 3]
        if strong:
            return "Likely " + strong[0]["name"].lower() + ".", "tested"
    if id_ == "dry":
        return "Usual causes: less rain, hotter air, drier air or stronger wind. The tests could not pick one.", "general"
    if id_ == "wet":
        return "Usual cause: more or heavier rain. The tests could not pick one.", "general"
    if id_ == "erosion":
        bits = []
        if S.get("top_dry") and S["top_dry"]["now"] <= LIMITS["dry_watch"]:
            bits.append("a dry top layer")
        if S.get("wind") and S["wind"]["significant"] and S["wind"]["direction"] == "rising":
            bits.append("stronger wind")
        return ("Caused by " + " and ".join(bits) + ".", "tested") if bits else ("Usual causes: bare soil, little rain and wind.", "general")
    if id_ == "rain":
        return "Rain follows big weather patterns such as El Niño and La Niña. The data cannot say more.", "general"
    return "A warming climate. Bare, dry soil also heats up faster.", "general"


PARAMS = [("top_year", "GWETTOP", "Surface soil wetness (top 5 cm)", "0 to 1", "The top 5 cm"),
          ("root_year", "GWETROOT", "Root-zone soil wetness (top 1 m)", "0 to 1", "The root zone"),
          ("prof_year", "GWETPROF", "Whole-profile soil wetness (deep store)", "0 to 1", "The whole soil depth"),
          ("ts", "TS", "Ground surface temperature", "°C", "The ground surface")]


def param_view(vid: str, short: str, t: dict | None) -> dict:
    """Verdict, plain meaning and actions for one soil parameter, from its yearly trend alone."""
    d = _dir(t)
    if vid == "TS":
        if d == "rising":
            return {"verdict": "Early warning", "tone": "bad", "simple": "The ground keeps getting hotter. Hot ground dries the soil and stresses roots.", "positive": _MEANING["heat"]["rising"][3]}
        if d == "falling":
            return {"verdict": "Good news", "tone": "good", "simple": "The ground is cooling. Less drying and less heat stress.", "positive": ["Keep the soil covered so it stays cool and moist."]}
        return {"verdict": "No clear change", "tone": "neutral", "simple": "No real change in ground temperature.", "positive": []}
    if d == "falling":
        return {"verdict": "Early warning", "tone": "bad", "simple": f"{short} is getting drier.", "positive": _MEANING["dry"]["falling"][3]}
    if d == "rising":
        return {"verdict": "Good news", "tone": "good", "simple": f"{short} is getting wetter. More water for plants. Watch for waterlogging in the rainy months.", "positive": _MEANING["dry"]["rising"][3]}
    return {"verdict": "No clear change", "tone": "neutral", "simple": f"No real change in {short.lower()}.", "positive": []}


def cause_rows(C: dict | None) -> list:
    """Every other variable tested against one soil parameter: is it changing, and is it linked to the soil parameter."""
    rows = []
    for d in (C or {}).get("drivers", []):
        r = {"name": d["name"], "expected": d.get("expected", ""), "error": d.get("error")}
        if not d.get("error") and d.get("trend"):
            tr = d["trend"]
            r["trend"] = {"direction": tr["direction"], "significant": tr["significant"], "slope_decade": tr["slope_decade"]}
            p = d.get("p_adj")
            r["link"] = {"label": d["label"], "score": d["score"], "r": d.get("r_det"), "p": p, "significant": bool(p is not None and p < 0.05)}
            r["note"] = "may also be a result of the soil" if d.get("may_be_feedback") or d.get("may_be_effect") else ""
        rows.append(r)
    return rows


# ---------------------------------------------------------------- one short alert per variable, for the results table
WET_LAYERS = {"GWETTOP": "The top 5 cm of soil", "GWETROOT": "The root zone", "GWETPROF": "The whole soil depth"}
_DRY_CAUSE = "Usual causes: less rain, hotter air, drier air or stronger wind."
_WET_CAUSE = "Usual cause: more or heavier rain."
_HEAT_CAUSE = "A warming climate. Bare, dry soil also heats up faster."
_RAIN_CAUSE = "Rain follows big weather patterns such as El Niño and La Niña."
_OTHER_ALERT = {
    "PRECTOTCORR": {"falling": ("watch", "Early warning: less rain", "Not good", "Less rain each year. Less water reaches the soil.", _RAIN_CAUSE, _MEANING["rain"]["falling"][3]),
                    "rising": ("mixed", "Mixed: more rain", "Mixed", "More rain each year. Good for water, but heavy storms can wash soil away.", _RAIN_CAUSE, [])},
    "RH2M": {"falling": ("watch", "Early warning: drier air", "Not good", "The air is getting drier. It pulls more water out of the soil.", "Changes in rain, temperature and wind patterns.",
                         ["Mulch and cover the soil to keep moisture in.", "Water in the cool of the day.", "Plant windbreaks to keep moist air near crops."]),
             "rising": ("good", "Good: moister air", "Good", "Moister air slows the drying of soil.", "", [])},
    "WS10M": {"rising": ("watch", "Early warning: windier", "Not good", "Stronger wind dries the surface and lifts dust.", "Changes in pressure and temperature patterns.", _MEANING["erosion"]["bad"][3]),
              "falling": ("good", "Good: calmer air", "Good", "Calmer air means less drying and less dust.", "", [])},
    "ALLSKY_SFC_SW_DWN": {"rising": ("mixed", "Mixed: more sunlight", "Mixed", "More sunlight helps plants but dries the soil.", "Less cloud or less haze.", []),
                          "falling": ("mixed", "Mixed: less sunlight", "Mixed", "Less sunlight means less drying but slower growth.", "More cloud or haze.", [])},
    "CLOUD_AMT": {"rising": ("mixed", "Mixed: more cloud", "Mixed", "More cloud means less sun on the soil, and often more rain.", "", []), "falling": ("mixed", "Mixed: less cloud", "Mixed", "Less cloud means more sun on the soil.", "", [])},
    "T2M_MIN": {"rising": ("mixed", "Mixed: warmer nights", "Mixed", "Warmer nights mean fewer frosts, which helps crops. Some pests also survive winter.", _HEAT_CAUSE, []),
                "falling": ("mixed", "Mixed: colder nights", "Mixed", "Colder nights bring more frost risk to crops.", "", [])},
}
for _v in ("T2M", "T2M_MAX", "TS"):
    _OTHER_ALERT[_v] = {"rising": ("watch", "Early warning: getting hotter" if _v != "TS" else "Early warning: hotter ground", "Not good", "It keeps getting hotter. Hot soil dries fast and plants get stressed.", _HEAT_CAUSE, _MEANING["heat"]["rising"][3]),
                        "falling": ("good", "Good: cooling", "Good", "It is cooling. Less drying and less heat stress.", "", [])}
for _v in ("SM010", "SM1040", "SM40100", "SM100200"):
    _L = {"SM010": "The top 10 cm of soil", "SM1040": "The soil at 10 to 40 cm", "SM40100": "The root zone (40 to 100 cm)", "SM100200": "The deep soil (100 to 200 cm)"}[_v]
    _OTHER_ALERT[_v] = {"falling": ("watch", "Early warning: drying", "Not good", f"{_L} holds less water each year.", _DRY_CAUSE, _MEANING["dry"]["falling"][3]),
                        "rising": ("good", "Good: more soil water", "Good", f"{_L} holds more water each year. Watch for waterlogging if it keeps rising.", _WET_CAUSE, [])}
_OTHER_ALERT["STMP"] = _OTHER_ALERT["TS"]
_OTHER_ALERT["PRECIP"] = _OTHER_ALERT["PRECTOTCORR"]
_OTHER_ALERT["ET"] = {"rising": ("mixed", "Mixed: more water leaves", "Mixed", "More water goes back to the air. Plants may be growing more, or the land is losing water faster.", "Hotter, sunnier or windier weather, or more plant cover.", []),
                      "falling": ("mixed", "Mixed: less water leaves", "Mixed", "Less water goes back to the air. The soil may keep more water, or plants may be growing less.", "Cooler, calmer weather, or less plant cover.", [])}


_OTHER_ALERT["TVEG"] = _OTHER_ALERT["ET"]
_OTHER_ALERT["EVAP"] = {"rising": ("watch", "Early warning: more evaporation", "Not good", "More water is evaporating straight from the soil.", "Hotter, sunnier or windier weather.", _MEANING["dry"]["falling"][3]),
                        "falling": ("good", "Good: less evaporation", "Good", "Less water is lost from the soil surface.", "", [])}
_OTHER_ALERT["RUNOFF"] = {"rising": ("watch", "Early warning: more runoff", "Not good", "More water runs off the surface instead of soaking in. Soil can wash away.", "Heavier rain, or a harder, bare or crusted surface.", ["Keep the soil covered so rain soaks in.", "Plough along the contour and keep strips of vegetation on slopes.", "Dig swales or bunds to slow the water."]),
                          "falling": ("mixed", "Mixed: less runoff", "Mixed", "Less water runs off. More may soak in, or there is less rain.", "", [])}


def alert_for(vid: str, t: dict | None, lim: dict | None = None) -> dict | None:
    """The alert, plus (for soil wetness only) one short line: when a danger level is reached if the straight-line trend continues. Not a forecast."""
    a = _alert_core(vid, t, lim)
    if a is not None and t and vid in WET_LAYERS:
        lim = lim or LIMITS
        d = _dir(t)
        if d in ("falling", "rising"):
            tt = time_to_limit(t, lim["dry_danger"] if d == "falling" else lim["wet_danger"], "down" if d == "falling" else "up")
            st = tt["state"]
            if st == "reached":
                a["ahead"] = "Danger level already reached."
            elif st == "approaching":
                e, l = tt.get("earliest"), tt.get("latest")
                rng = f" ({e:.0f} to {l:.0f})" if e is not None and l is not None and l <= 200 else ""
                a["ahead"] = f"Danger level in about {tt['years']:.0f} years{rng}. Straight line, not a forecast."
            elif st == "far":
                a["ahead"] = f"Danger level over {MAX_AHEAD:.0f} years away."
    return a


def _alert_core(vid: str, t: dict | None, lim: dict | None = None) -> dict | None:
    """Short alert, verdict, meaning, general cause and recommendation for one variable. The wording is for farmers, not statisticians."""
    if not t:
        return None
    lim = lim or LIMITS
    d, now = _dir(t), t["sen"]["line"][1][1]

    def A(level, text, verdict, means, cause, rec):
        return {"level": level, "text": text, "verdict": verdict, "means": means, "cause": cause, "recommend": list(rec)}
    if vid in WET_LAYERS:
        L = WET_LAYERS[vid]
        dry_rec, wet_rec = _MEANING["dry"]["falling"][3], _MEANING["wet"]["rising"][3]
        if d == "falling":
            tt = time_to_limit(t, lim["dry_danger"], "down")
            if tt["state"] == "reached" or now <= lim["dry_danger"]:
                return A("danger", "Alert: too dry and still drying", "Not good", f"{L} is very dry and is still getting drier.", _DRY_CAUSE, dry_rec)
            if now <= lim["dry_watch"]:
                return A("warning", "Alert: dry and drying", "Not good", f"{L} is dry and is getting drier.", _DRY_CAUSE, dry_rec)
            if tt["state"] == "approaching" and tt["years"] <= 15:
                return A("warning", f"Alert: drying, danger in about {tt['years']:.0f} years", "Not good", f"{L} is getting drier.", _DRY_CAUSE, dry_rec)
            return A("watch", "Early warning: drying", "Not good", f"{L} is getting drier.", _DRY_CAUSE, dry_rec)
        if d == "rising":
            if now >= lim["wet_danger"]:
                return A("danger", "Alert: waterlogged", "Not good", f"{L} is very wet and getting wetter. Roots may not get air.", _WET_CAUSE, wet_rec)
            if now >= lim["wet_watch"]:
                return A("warning", "Alert: getting too wet", "Not good", f"{L} is wet and getting wetter. Roots may not get air.", _WET_CAUSE, wet_rec)
            return A("good", "Good: getting wetter", "Good", f"{L} is getting wetter. More water for plants.", _WET_CAUSE, [])
        if now <= lim["dry_danger"]:
            return A("danger", "Alert: very dry", "Not good", f"{L} is very dry, though it is not changing.", _DRY_CAUSE, dry_rec)
        if now <= lim["dry_watch"]:
            return A("warning", "Alert: dry", "Not good", f"{L} is dry, though it is not changing.", _DRY_CAUSE, dry_rec)
        if now >= lim["wet_danger"]:
            return A("danger", "Alert: waterlogged", "Not good", f"{L} is very wet.", _WET_CAUSE, wet_rec)
        if now >= lim["wet_watch"]:
            return A("warning", "Alert: too wet", "Not good", f"{L} is wet.", _WET_CAUSE, wet_rec)
        return A("none", "No alert", "No change", f"No real change in {L.lower()}.", "", [])
    hit = _OTHER_ALERT.get(vid, {}).get(d)
    if hit:
        return A(*hit)
    return A("none", "No alert", "No change", "No clear change.", "", [])


def farm_calendar(place: dict, y0: int, m0: int, y1: int, m1: int, months: list | None, limits: dict | None, getter=trends.get_json) -> dict:
    lim = dict(LIMITS)
    for k, v in (limits or {}).items():
        if k in lim:
            lim[k] = float(v)
    sel = sorted({int(m) for m in (months or range(1, 13)) if 1 <= int(m) <= 12})
    if not sel:
        raise ValueError("Choose at least one month of the year.")
    cell = trends.cell_of(place["lat"], place["lon"])
    raw = trends.fetch_cell(cell, ["GWETROOT", "PRECTOTCORR", "T2M", "T2M_MAX", "T2M_MIN"], y0, y1, getter)
    if not raw.get("GWETROOT"):
        raise trends.DataError("NASA POWER did not return soil wetness for this place.")
    M = dict(raw)
    M["_y0"], M["_y1"], M["_p"], M["_sel"] = y0, y1, (y0, m0, y1, m1), sel
    rows = calendar(M, lim, y1 - y0 + 1)
    return {"calendar": rows, "summary": calendar_summary(rows), "limits": lim}


def _cause_group(did: str) -> str:
    return {"PRECTOTCORR": "rain", "T2M": "heat", "ALLSKY_SFC_SW_DWN": "heat", "RH2M": "heat", "CLOUD_AMT": "rain", "WS10M": "wind", "ONI": "cycle"}.get(did, "other")


def recommendations(issues: list, cause_groups: list, ev: dict, aerial: dict) -> list:
    """Plain actions, ordered by urgency. Each one says why it is on the list and what it is tied to."""
    lv = {i["id"]: i["level"] for i in issues}
    recs = []

    def add(pri, title, why, how, tied):
        recs.append({"priority": pri, "title": title, "why": why, "how": how, "tied": tied})

    def pri(i):
        return {"danger": "Do now", "warning": "Do now", "watch": "Plan this year", "ok": "Keep watching"}[lv.get(i, "ok")]
    cg = set(cause_groups)
    if lv.get("dry", "ok") != "ok":
        p = pri("dry")
        add(p, "Keep the soil covered and build organic matter",
            "Dry-season soil water is " + ("already low" if lv["dry"] in ("danger", "warning") else "falling") + ". Cover and organic matter slow evaporation and hold water like a sponge.",
            ["Leave crop residue or spread mulch (straw, leaves, wood chips) 5 to 10 cm deep.", "Add compost or manure each season to raise organic matter.", "Grow a cover crop in the gap between crops.", "Till less: each pass dries the soil and breaks its structure."], "Drying soil")
        add(p, "Water by need, not by habit",
            "When soil water is close to the stress level, a little water at the right time does more than a lot at the wrong time.",
            ["Check the soil by hand at root depth before irrigating (a probe or simple moisture meter helps).", "Irrigate early morning or evening, and use drip or low-pressure lines.", "Give the deepest watering at the crop stages most sensitive to dryness (flowering and grain or fruit filling)."], "Drying soil")
        if "rain" in cg:
            add(p, "Catch and store the rain you do get",
                "The tests link the drying mostly to less rain, so storing water is the main buffer.",
                ["Dig swales, bunds or contour ridges so runoff soaks in.", "Collect roof and field runoff in tanks or a farm pond.", "Choose crops and varieties that need less water or mature before the dry months."], "Cause: less rain")
        if "heat" in cg:
            add(p, "Cut the heat and evaporation load",
                "The tests link the drying mostly to warmer, sunnier or drier air, which pulls water out faster.",
                ["Mulch bare ground, and use shade nets or tree rows for sensitive crops.", "Shift planting dates so the thirstiest stage avoids the hottest weeks.", "Keep soil covered between rows."], "Cause: heat and evaporation")
    if lv.get("wet", "ok") != "ok":
        p = pri("wet")
        add(p, "Get excess water off the field",
            "Soil in the wet season is " + ("already near saturation" if lv["wet"] in ("danger", "warning") else "getting wetter") + ". Roots need air, and wet soil is easily crushed by machines.",
            ["Open or dig drains and keep ditches clear before the rains.", "Use raised beds or ridges on low ground.", "Stay off wet soil with heavy machines to avoid compaction.", "Plant deep-rooted cover crops to open the soil and take up water."], "Wetter soil in the wet season")
    if lv.get("erosion", "ok") != "ok":
        p = pri("erosion")
        add(p, "Protect the surface from wind and crusting",
            "The top layer is dry" + (" and the wind is getting stronger" if aerial.get("wind_up") else "") + ". Bare dry soil loses its fine, fertile part first.",
            ["Plant windbreaks (tree or shrub rows) across the main wind direction.", "Never leave soil bare in dry, windy months: keep residue, mulch or a cover crop.", "Plough along the contour and keep strips of vegetation on slopes."], "Dry, loose surface")
    if lv.get("rain", "ok") == "watch" or lv.get("rain", "ok") == "warning":
        add(pri("rain"), "Plan for a drier rain calendar", "Rain has been falling over the years, so past planting dates and crops may no longer fit.",
            ["Compare your planting dates with the farm calendar below and the season table.", "Test a more drought-tolerant crop or variety on a small plot first."], "Rain trend")
    if lv.get("heat", "ok") != "ok":
        add(pri("heat"), "Prepare for hotter peaks", "Very hot days hurt flowering and seed set, and dry the soil faster.",
            ["Choose heat-tolerant varieties and sow so flowering misses the hottest weeks.", "Use mulch and shade, and water in the cool of the day."], "Heat trend")
    goods = [i for i in issues if i["meaning"]["verdict"] == "Good news"]
    if goods:
        add("Keep watching", "Protect what is improving", "Improving: " + "; ".join(g["title"].lower() for g in goods) + ". Gains in soil can be lost again if the cover or the organic matter is let go.",
            ["Keep residue, mulch or cover crops on the ground.", "Keep adding organic matter.", "Do not use the extra water to over-irrigate."], "Good news")
    # events
    cnt = ev.get("counts") or {}
    if cnt.get("drought", 0) >= 2:
        add("Plan this year", "Write a simple drought plan", f"NASA recorded {cnt['drought']} drought events near this place in these years, so another one is likely.",
            ["Decide in advance which fields get water first and which crops you would sacrifice.", "Keep a feed and cash reserve for a bad season.", "Ask whether crop insurance or a local drought fund exists."], "Events: droughts")
    if cnt.get("floods", 0) >= 2:
        add("Plan this year", "Prepare for flood years", f"NASA recorded {cnt['floods']} flood events near this place in these years.",
            ["Keep a grass or tree buffer along streams to slow water and catch silt.", "Keep seed, fertiliser and tools above the likely flood line.", "After a flood, wait until the soil can be walked on without leaving prints before working it."], "Events: floods")
    if cnt.get("wildfires", 0) >= 1:
        add("Plan this year", "Guard against fire and the erosion after it", f"NASA recorded {cnt['wildfires']} wildfire event(s) near this place in these years.",
            ["Keep a cleared strip around fields and buildings.", "After a fire, cover bare ground with mulch or seed it quickly: burned soil washes away in the first heavy rain."], "Events: wildfires")
    if cnt.get("dustHaze", 0) >= 1:
        add("Plan this year", "Use windbreaks and ground cover against dust", f"NASA recorded {cnt['dustHaze']} dust or haze event(s) near this place in these years.",
            ["Dust storms strip topsoil in dry, bare, windy spells: keep the ground covered then."], "Events: dust and haze")
    if cnt.get("landslides", 0) >= 1:
        add("Plan this year", "Keep slopes held together", f"NASA recorded {cnt['landslides']} landslide event(s) near this place in these years. Wet soil on a slope is the usual trigger.",
            ["Keep deep-rooted plants and cover on slopes.", "Do not cut terraces or tracks across wet slopes.", "Lead water away from the top of slopes with drains."], "Events: landslides")
    if cnt.get("volcanoes", 0) >= 1:
        add("Keep watching", "Know what volcanic ash does to soil", f"NASA recorded {cnt['volcanoes']} volcano event(s) near this place in these years. Ash can feed soil with minerals, but a thick layer smothers plants and seals the surface.",
            ["After ash fall, rake or wash thick ash off crops and keep it out of irrigation channels.", "Mix thin ash into the soil. It often adds minerals over time.", "Test the soil afterwards for pH and salts."], "Events: volcanoes")
    recs.append({"priority": "Keep watching", "title": "Test the real soil once a year", "why": "These numbers are model values for a grid cell of about 50 km, not your field.",
                 "how": ["Take a soil test every year in the same month: organic matter, salinity (EC), pH and nutrients.", "Compare it with these trends. If your field disagrees, trust the field and tell your local agriculture office."], "tied": "Safety check"})
    order = {"Do now": 0, "Plan this year": 1, "Keep watching": 2}
    recs.sort(key=lambda r: order[r["priority"]])
    return recs


def calendar(M: dict, lim: dict, years: int) -> list:
    rows = []
    y0, m0, y1, m1 = M["_p"]
    for m in M["_sel"]:
        def clim(p):
            v = [M[p][f"{y}{m:02d}"] for y in range(M["_y0"], M["_y1"] + 1) if (y0, m0) <= (y, m) <= (y1, m1) and f"{y}{m:02d}" in M[p]]
            return statistics.fmean(v) if v else None
        rain, root, t, tmax, tmin = clim("PRECTOTCORR"), clim("GWETROOT"), clim("T2M"), clim("T2M_MAX"), clim("T2M_MIN")
        flags = []
        if root is not None and root <= lim["dry_watch"]:
            flags.append("Soil dry")
        if root is not None and root >= lim["wet_watch"]:
            flags.append("Soil very wet")
        if tmin is not None and tmin < FROST_BELOW:
            flags.append("Frost risk")
        if tmax is not None and tmax >= HEAT_WATCH:
            flags.append("Heat stress")
        good = t is not None and root is not None and t >= GROW_MIN_T and lim["dry_watch"] < root < lim["wet_watch"] and "Heat stress" not in flags and "Frost risk" not in flags
        rows.append({"month": trends.MON[m], "rain_mm": None if rain is None else rain * DAYS[m], "root": root, "t": t, "tmax": tmax, "tmin": tmin, "flags": flags, "good": bool(good)})
    return rows


def calendar_summary(rows: list) -> list:
    def names(f):
        return [r["month"] for r in rows if f(r)]
    out = []
    good, dry, wet = names(lambda r: r["good"]), names(lambda r: "Soil dry" in r["flags"]), names(lambda r: "Soil very wet" in r["flags"])
    hot, frost = names(lambda r: "Heat stress" in r["flags"]), names(lambda r: "Frost risk" in r["flags"])
    out.append(("Good to grow", ", ".join(good) if good else "none"))
    if dry:
        out.append(("Dry soil", ", ".join(dry)))
    if wet:
        out.append(("Very wet soil", ", ".join(wet)))
    if hot:
        out.append(("Heat stress", ", ".join(hot)))
    if frost:
        out.append(("Frost risk", ", ".join(frost)))
    return out


def analyse_soil(place: dict, y0: int, y1: int, limits: dict | None, getter=trends.get_json, getter_events=None, text_getter=None, m0: int = 1, m1: int = 12, months: list | None = None, mode: str = "year") -> dict:
    lim = dict(LIMITS)
    for k, v in (limits or {}).items():
        if k in lim:
            lim[k] = float(v)
    if not (0 < lim["dry_danger"] < lim["dry_watch"] < lim["wet_watch"] < lim["wet_danger"] < 1):
        raise ValueError("The four soil wetness levels must go in order: dry danger < dry watch < wet watch < wet danger, all between 0 and 1.")
    sel = sorted({int(m) for m in (months or range(1, 13)) if 1 <= int(m) <= 12})
    if not sel:
        raise ValueError("Choose at least one month of the year.")
    if mode not in ("year", "month"):
        raise ValueError("Choose one value per year or every month.")
    need = 8 if mode == "year" else 24
    if (y1 * 12 + m1) - (y0 * 12 + m0) + 1 < (96 if mode == "year" else 24):
        raise ValueError("The soil report needs at least 8 years (one value per year) or 24 months (every month). Choose a longer period.")
    inside = lambda y, m: (y0, m0) <= (y, m) <= (y1, m1)  # noqa: E731
    per = trends.period_text(y0, m0, y1, m1, sel, mode)
    cell = trends.cell_of(place["lat"], place["lon"])
    raw = trends.fetch_cell(cell, NEED, y0, y1, getter)
    if not raw.get("GWETROOT"):
        raise trends.DataError("NASA POWER did not return soil wetness for this place. Try another place or period.")
    M = dict(raw)
    M["_y0"], M["_y1"], M["_p"], M["_sel"] = y0, y1, (y0, m0, y1, m1), sel
    ALL = sel
    clim = {m: [raw["GWETROOT"][f"{y}{m:02d}"] for y in range(y0, y1 + 1) if inside(y, m) and f"{y}{m:02d}" in raw["GWETROOT"]] for m in sel}
    avg = {m: statistics.fmean(v) for m, v in clim.items() if v}
    if len(avg) < len(sel):
        raise trends.DataError("Some of the chosen months have no soil data at this place.")
    ordered = sorted(avg, key=lambda m: avg[m])
    k = min(3, max(1, len(sel) // 2))
    dry3, wet3 = sorted(ordered[:k]), sorted(ordered[-k:])
    raw_t = {}
    named = {
        "root_year": ("GWETROOT", ALL, "Root-zone soil wetness", "0 to 1", False),
        "root_dry": ("GWETROOT", dry3, "Root-zone soil wetness in the driest months", "0 to 1", False),
        "root_wet": ("GWETROOT", wet3, "Root-zone soil wetness in the wettest months", "0 to 1", False),
        "top_dry": ("GWETTOP", dry3, "Surface soil wetness in the driest months", "0 to 1", False),
        "top_year": ("GWETTOP", ALL, "Surface soil wetness", "0 to 1", False),
        "prof_year": ("GWETPROF", ALL, "Whole soil profile wetness", "0 to 1", False),
        "ts": ("TS", ALL, "Ground surface temperature", "°C", False),
        "rain": ("PRECTOTCORR", ALL, "Rain", "mm/day", True),
        "temp": ("T2M", ALL, "Air temperature", "°C", False),
        "rh": ("RH2M", ALL, "Air humidity", "%", False),
        "wind": ("WS10M", ALL, "Wind speed", "m/s", True),
        "sun": ("ALLSKY_SFC_SW_DWN", ALL, "Sunlight reaching the ground", "kWh/m²/day", True),
    }
    series = {}

    def TR(sr, nm, un, pc=False, n=need):
        return stats.analyse(sr["x"], sr["values"], nm, un, pc, seasons=sr["seasons"] if mode == "month" else None, min_n=n, rate=(10.0, "decade")) if len(sr["x"]) >= n else None
    for key, (v, ms, nm, un, pc) in named.items():
        sr = trends.build_series(raw.get(v) or {}, y0, m0, y1, m1, ms, mode)
        series[key] = sr
        raw_t[key] = TR(sr, nm, un, pc, 8 if key in ("root_dry", "root_wet", "top_dry") else need)
    # hottest month of each year (average daily high), coldest month's average night low
    if mode == "year":
        hx, hv, cx, cv = [], [], [], []
        for y in range(y0, y1 + 1):
            if not all(inside(y, m) for m in sel):
                continue
            a = [(raw.get("T2M_MAX") or {}).get(f"{y}{m:02d}") for m in sel]
            if a and all(v is not None for v in a):
                hx.append(y)
                hv.append(max(a))
            b = [(raw.get("T2M_MIN") or {}).get(f"{y}{m:02d}") for m in sel]
            if b and all(v is not None for v in b):
                cx.append(y)
                cv.append(min(b))
        series["heat"], series["cold"] = {"x": hx, "values": hv, "seasons": []}, {"x": cx, "values": cv, "seasons": []}
    else:
        series["heat"] = trends.build_series(raw.get("T2M_MAX") or {}, y0, m0, y1, m1, sel, "month")
        series["cold"] = trends.build_series(raw.get("T2M_MIN") or {}, y0, m0, y1, m1, sel, "month")
    raw_t["heat"] = TR(series["heat"], "Average daily high of the hottest month" if mode == "year" else "Average daily high", "°C")
    raw_t["cold"] = TR(series["cold"], "Average night low of the coldest month" if mode == "year" else "Average night low", "°C")
    S = {k: _slim(v) for k, v in raw_t.items()}
    S["_mode"] = mode
    S["_raw"] = raw_t
    last_year = max((s["x"][-1] for s in series.values() if s["x"]), default=y1)
    issues = [interpret(i, S) for i in build_issues(S, lim, int(last_year))]
    seas = []
    layers = []
    for k, nm, what in (("top_year", "Surface (top 5 cm)", "Reacts within hours or days to rain and sun. It decides whether seeds sprout and whether the top crusts or blows away."),
                        ("root_year", "Root zone (top 1 m)", "The water store most crops live on. This is the layer the danger levels are based on."),
                        ("prof_year", "Whole profile (deep store)", "Moves slowly. A fall here means a long dry spell is draining the reserve that carries crops through bad seasons.")):
        t = S.get(k)
        if t:
            layers.append({"name": nm, "what": what, "now": t["now"], "trend": {x: t[x] for x in ("direction", "significant", "slope_decade", "sentence", "verdict")}})
    # other trends in plain words
    other = [{"id": k, "name": nm, "unit": un, "trend": {x: S[k][x] for x in ("direction", "significant", "slope_decade", "sentence", "verdict")}, "meaning": interpret_other(k, S[k])} for k, nm, un in
             (("rain", "Rain", "mm/day"), ("temp", "Air temperature", "°C"), ("rh", "Air humidity", "%"), ("wind", "Wind speed", "m/s"), ("sun", "Sunlight at the ground", "kWh/m²/day"), ("cold", "Coldest month night low", "°C"), ("ts", "Ground surface temperature", "°C")) if S.get(k)]
    # causes and events in parallel; each is allowed to fail on its own
    notes = []

    def get_causes_for(vid):
        try:
            return causes.analyse_causes(place, vid, y0, m0, y1, m1, sel, mode, getter, text_getter)
        except Exception as e:      # noqa: BLE001
            notes.append(f"Cause tests for {vid} could not run: " + str(e)[:140])
            return None

    def get_events():
        if not EVENTS_ON:
            return None
        try:
            return events.explore(place, max(1990, y0), y1, EVENT_CATS, 10.0, getter_events)
        except Exception as e:      # noqa: BLE001
            notes.append("NASA EONET events could not be loaded: " + str(e)[:140])
            return None
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {p[1]: ex.submit(get_causes_for, p[1]) for p in PARAMS}
        fe = ex.submit(get_events)
        CS = {k: f.result() for k, f in futs.items()}
        E = fe.result()
    C = CS.get("GWETROOT")
    cause = None
    groups = []
    if C:
        top = [d for d in C["drivers"] if d.get("score") is not None and d.get("score", 0) >= 2 and d.get("kind") == "normal"][:4]
        picked = [d["id"] for d in (C["best"].get("drivers") or [])] or [d["id"] for d in top[:2] if d.get("score", 0) >= 3]
        groups = [_cause_group(i) for i in picked]
        cause = {"text": C["best"]["text"], "target_trend": C["target"]["trend"]["sentence"], "drivers": [{"id": d["id"], "name": d["name"], "label": d["label"], "score": d["score"], "expected": d["expected"], "mechanism": d["mechanism"],
                                                                                                   "may_be_feedback": d.get("may_be_feedback", False), "may_be_effect": d.get("may_be_effect", False), "lead_text": d.get("lead_text", "")} for d in top],
                 "all": [{"name": d["name"], "label": d.get("label") or "No data"} for d in C["drivers"]], "picked": picked, "best": C["best"].get("drivers") or [], "fits_direction": C["best"].get("fits_direction") or []}
    ev = {"count": 0, "counts": {}, "titles": {}, "driest_years": [], "in_driest": []}
    if E:
        ev["count"] = E["count"]
        for e in E["events"]:
            ev["counts"][e["category"]] = ev["counts"].get(e["category"], 0) + 1
            ev["titles"][e["category"]] = e["category_title"]
        s = series["root_dry"]
        if len(s["x"]) >= 8:
            low = sorted(zip(s["values"], s["x"]))[:3]
            ev["driest_years"] = sorted(int(x) for _, x in low)
            ev["in_driest"] = [{"title": e["title"], "category": e["category_title"], "start": e["start"][:10], "near_km": e.get("near_km")} for e in E["events"]
                               if e["start"][:4].isdigit() and int(e["start"][:4]) in ev["driest_years"]][:12]
        ev["note"] = E["note"]
    if not any(i["id"] == "dry" for i in issues):
        notes.append("This period has too few values for the dry-season and wet-season checks. Choose a longer period or more months.")
    for i in issues:
        i["simple"] = simple_text(i, S)
        i["help"] = VERDICT_HELP[i["meaning"]["verdict"]]
        i["when"] = when_text(i)
        i["cause"], i["cause_src"] = cause_text(i, cause, S)
    aerial = {"wind_up": bool(S.get("wind") and S["wind"]["significant"] and S["wind"]["direction"] == "rising")}
    recs = recommendations(issues, groups, ev, aerial)
    worst = "ok"
    for i in issues:
        worst = _worse(worst, i["level"])
    soil_issues = [i for i in issues if i["id"] in ("dry", "wet", "erosion") and i["level"] != "ok"]
    head = {"ok": "No soil issue stands out in this data.", "watch": "Nothing is dangerous yet, but something is moving the wrong way.", "warning": "At least one soil trend needs action soon.", "danger": "At least one soil measure is already in the danger range."}[worst]
    tally = {"alerts": sum(1 for i in issues if i["level"] in ("danger", "warning")), "early": sum(1 for i in issues if i["level"] == "watch"),
             "good": sum(1 for i in issues if i["meaning"]["verdict"] == "Good news"), "mixed": sum(1 for i in issues if i["meaning"]["verdict"] == "Mixed news")}
    parts = []
    if tally["alerts"]:
        parts.append(f"{tally['alerts']} alert" + ("s" if tally["alerts"] != 1 else ""))
    if tally["early"]:
        parts.append(f"{tally['early']} early warning" + ("s" if tally["early"] != 1 else ""))
    if tally["good"]:
        parts.append(f"{tally['good']} piece of good news" if tally["good"] == 1 else f"{tally['good']} pieces of good news")
    if tally["mixed"]:
        parts.append(f"{tally['mixed']} mixed")
    tally["text"] = ", ".join(parts) if parts else "No clear change in any soil measure."
    cal = calendar(M, lim, y1 - y0 + 1)
    yr = per
    dm, wm = ", ".join(trends.MON[m] for m in dry3), ", ".join(trends.MON[m] for m in wet3)
    LOOK = {"root_dry": f"Root-zone soil wetness (NASA GWETROOT), average of the driest months ({dm}), {yr}",
            "root_wet": f"Root-zone soil wetness (NASA GWETROOT), average of the wettest months ({wm}), {yr}",
            "top_dry": f"Surface soil wetness (NASA GWETTOP) in the driest months ({dm}) and wind speed (NASA WS10M), {yr}",
            "rain": f"Rain (NASA PRECTOTCORR), {yr}", "heat": f"Daily high temperature (NASA T2M_MAX)" + (" of the hottest month" if mode == "year" else "") + f", {yr}"}
    for i in issues:
        i["looking"] = LOOK[i["var"]]
        if mode == "month" and i["id"] == "heat":
            i["title"] = "Heat (daily highs)"
            i["what"] = i["what"].replace("of the hottest month ", "")
    params = []
    for key, vid, name, unit, short in PARAMS:
        t = S.get(key)
        if not t:
            continue
        pv = param_view(vid, short, t)
        params.append({"id": vid, "name": name, "unit": unit, "looking": f"{name} (NASA {vid}), {yr}", "now": t["now"], "trend": {x: t[x] for x in ("direction", "significant", "slope_decade", "p", "sentence", "verdict")},
                       "chart_key": key, "view": pv, "cause_text": (CS.get(vid) or {}).get("best", {}).get("text", ""), "causes": cause_rows(CS.get(vid)), "causes_ran": CS.get(vid) is not None})
    charts = {}
    for k in ("root_dry", "root_wet", "top_dry", "rain", "heat", "top_year", "root_year", "prof_year", "ts"):
        s = series[k]
        charts[k] = {"x": s["x"], "values": s["values"], "line": S[k]["line"] if S.get(k) else None}
    return {"place": place, "years": [y0, y1], "limits": lim, "limit_note": LIMIT_NOTE, "dry_months": [trends.MON[m] for m in dry3], "wet_months": [trends.MON[m] for m in wet3],
            "worst": worst, "headline": head, "tally": tally, "params": params, "layers": layers, "glossary": GLOSSARY, "issues": [{k: v for k, v in i.items() if k != "ttl_raw"} | {"charts_key": i["var"], "level_text": LEVEL_TEXT[i["level"]]} for i in issues], "charts": charts, "seasons": seas, "other": other, "cause": cause, "events": ev,
            "recommendations": recs, "calendar": cal, "calendar_summary": calendar_summary(cal), "notes": notes + ["Soil wetness is a NASA model value for a grid cell of about 50 km (0 dry, 1 saturated), not a measurement in your field.",
                                                                                                           "This report is guidance from trends and statistical association, not proof of cause and not a prescription. A soil test and your local agriculture office have the last word.",
                                                                                                           "Salinity, soil acidity, nutrients and compaction cannot be seen in this data."],
            "soil_issue_count": len(soil_issues)}
