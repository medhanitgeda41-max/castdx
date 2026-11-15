"""Solar radiation and energy: plain-word alerts for sunlight, heat from the sky, light for plants, ultraviolet and cloud, and the combined risk
assessment for land (soil + sunlight) and for the sea (coast + sunlight).

All numbers come from NASA POWER monthly series (CERES satellite and MERRA-2 model) at the grid cell of the place. The levels below are rough rules of
thumb for how fast a series changes per decade. They are guidance, not a forecast, and a local expert has the last word.
"""
from __future__ import annotations

import coast
import soil
import trends

# id: (watch, warning, danger, how the rate is measured per decade: "pct" = percent of the average, "abs" = in the unit of the series)
RATE = {
    "ALLSKY_SFC_SW_DWN": (0.5, 1.5, 3.0, "pct"), "CLRSKY_SFC_SW_DWN": (0.3, 0.8, 1.5, "pct"), "ALLSKY_SFC_LW_DWN": (0.3, 0.8, 1.5, "pct"),
    "ALLSKY_SFC_PAR_TOT": (0.5, 1.5, 3.0, "pct"), "ALLSKY_SFC_UV_INDEX": (1.0, 3.0, 6.0, "pct"), "CLOUD_AMT": (1.0, 3.0, 5.0, "abs"), "CLOUD_LOSS": (1.0, 3.0, 6.0, "pct"),
    "EVLAND": (0.5, 1.5, 3.0, "pct"), "EVPTRNS": (0.5, 1.5, 3.0, "pct"), "TSOIL1": (0.1, 0.3, 0.6, "abs"),
}
# id: {direction: (short title, what it means, general cause)}
WORDS = {
    "ALLSKY_SFC_SW_DWN": {"rising": ("more sunlight reaching the ground", "More of the sun's energy reaches the surface.", "Fewer clouds, or cleaner air that lets more light through."),
                          "falling": ("less sunlight reaching the ground", "Less of the sun's energy reaches the surface.", "More cloud, or more haze and dust in the air (called global dimming).")},
    "CLRSKY_SFC_SW_DWN": {"rising": ("clearer air", "On a cloudless day more sunlight comes through than before.", "Cleaner air, less haze and dust."),
                          "falling": ("hazier air", "On a cloudless day less sunlight comes through than before.", "More haze, dust, smoke or water vapour in the air.")},
    "CLOUD_LOSS": {"rising": ("clouds taking more of the sun", "Clouds now block more sunlight than before.", "More or thicker cloud."),
                   "falling": ("clouds taking less of the sun", "Clouds now block less sunlight than before.", "Less or thinner cloud.")},
    "CLOUD_AMT": {"rising": ("more cloud", "The sky is cloudier than before.", "Changes in moisture and air circulation."),
                  "falling": ("less cloud", "The sky is clearer than before. More sun and heat reach the ground.", "Changes in moisture and air circulation.")},
    "ALLSKY_SFC_LW_DWN": {"rising": ("more heat sent back down by the air", "The air and clouds send more heat back to the ground. This is the greenhouse effect at work.", "More greenhouse gases, more water vapour, warmer air."),
                          "falling": ("less heat sent back down by the air", "The air and clouds send less heat back to the ground.", "Drier or clearer air.")},
    "ALLSKY_SFC_PAR_TOT": {"rising": ("more light for plants", "More of the light that plants use for growth reaches the ground.", "Fewer clouds or less haze."),
                           "falling": ("less light for plants", "Less of the light that plants use for growth reaches the ground.", "More cloud, or more haze and dust.")},
    "ALLSKY_SFC_UV_INDEX": {"rising": ("stronger ultraviolet light", "More ultraviolet light reaches the ground. It burns skin and eyes and stresses plants and sea life.", "A thinner ozone layer or fewer clouds."),
                            "falling": ("weaker ultraviolet light", "Less ultraviolet light reaches the ground.", "A thicker ozone layer, more cloud or more haze.")},
    "EVLAND": {"rising": ("more evaporation from the ground", "More water is leaving the ground as vapour.", "Hotter air, more sun, stronger wind.")},
    "EVPTRNS": {"rising": ("plants giving off more water", "Plants are pulling more water out of the soil and giving it off through their leaves.", "Hotter air, more plant growth, more sun.")},
    "TSOIL1": {"rising": ("warmer soil", "The top layer of soil is getting warmer. It loses water faster and roots get stressed.", "Hotter air and more sun on the ground.")},
}
# which direction hurts, by use: land (soil and crops), sea (coast), solar (sunlight and solar power on their own). None means no clear harm either way.
HARM = {
    "ALLSKY_SFC_SW_DWN": {"land": "rising", "sea": "rising", "solar": None}, "CLRSKY_SFC_SW_DWN": {"land": "falling", "sea": None, "solar": "falling"},
    "CLOUD_LOSS": {"land": None, "sea": None, "solar": None}, "CLOUD_AMT": {"land": "falling", "sea": "falling", "solar": None},
    "ALLSKY_SFC_LW_DWN": {"land": "rising", "sea": "rising", "solar": "rising"}, "ALLSKY_SFC_PAR_TOT": {"land": "falling", "sea": None, "solar": "falling"},
    "ALLSKY_SFC_UV_INDEX": {"land": "rising", "sea": "rising", "solar": "rising"},
    "EVLAND": {"land": "rising"}, "EVPTRNS": {"land": "rising"}, "TSOIL1": {"land": "rising"},
}
REC = {
    ("ALLSKY_SFC_SW_DWN", "land"): ["Shade and cover the soil (mulch, cover crops, tree belts) so extra sun does not dry it out.", "Water early in the morning or in the evening so less is lost to evaporation.", "Choose crops and seed that cope with strong sun and heat."],
    ("ALLSKY_SFC_SW_DWN", "sea"): ["Watch for marine heat waves and coral bleaching in sunny, calm weeks.", "Keep other stress low on reefs and nurseries (pollution, silt, over-fishing) so they cope better with more sun."],
    ("CLRSKY_SFC_SW_DWN", "land"): ["Check the air for smoke, dust and haze. Cutting burning and dust sources gives plants more light.", "Plan crops with enough light, and avoid shading by close planting."],
    ("CLRSKY_SFC_SW_DWN", "solar"): ["Size solar power on recent years, not on old records.", "Clean panels often. Dust and haze lower their output.", "Look for local haze sources (burning, dust, traffic) that can be reduced."],
    ("CLOUD_AMT", "land"): ["Plan shade and extra water for hot, clear months.", "Use mulch and windbreaks to hold soil water when clouds no longer shade the ground."],
    ("CLOUD_AMT", "sea"): ["Watch for sea heat stress in clear, calm weeks and warn fishers, divers and reef managers."],
    ("ALLSKY_SFC_LW_DWN", "land"): ["Plan for hotter nights and hotter soil: mulch, shade and water saving.", "Use heat-tolerant crops and varieties."],
    ("ALLSKY_SFC_LW_DWN", "sea"): ["Plan for a warmer sea surface: watch reefs, fish stocks and algae blooms."],
    ("ALLSKY_SFC_LW_DWN", "solar"): ["This shows more greenhouse warming over this place. Plan for more heat: shade, cooling and water saving."],
    ("ALLSKY_SFC_PAR_TOT", "land"): ["Choose crops and spacing that suit less light.", "Look for haze and smoke sources that cut the light plants need."],
    ("ALLSKY_SFC_PAR_TOT", "solar"): ["Less usable light slows crop growth and solar power. Check for haze, smoke and dust and plan crops and panels for it."],
    ("ALLSKY_SFC_UV_INDEX", "land"): ["Shade workers and use protective clothing and hats in the strongest hours.", "Choose crops and plastic covers that cope with strong ultraviolet light."],
    ("ALLSKY_SFC_UV_INDEX", "sea"): ["Warn beach users and fishers about sunburn. Keep shallow reefs and seagrass free of other stress."],
    ("ALLSKY_SFC_UV_INDEX", "solar"): ["Warn the public about sunburn and eye damage, mainly in the strongest hours, and give shade."],
    ("EVLAND", "land"): ["Cut evaporation: keep soil covered, mulch, and irrigate at night or early morning.", "Use drip irrigation instead of spraying."],
    ("EVPTRNS", "land"): ["Match crops and planting density to the water that is there. Thin or change crops that use a lot of water."],
    ("TSOIL1", "land"): ["Keep the soil cool and covered: mulch, shade trees and cover crops.", "Add organic matter so the soil holds water."],
}
MIXED_REC = {
    ("ALLSKY_SFC_SW_DWN", "rising"): ["Good for solar power: keep panels clean and check them for overheating.", "Plan shade and water for people and crops in the sunniest months."],
    ("ALLSKY_SFC_SW_DWN", "falling"): ["Expect less solar power than in past years: size solar systems on recent years, not on old records.", "Check the air for dust and haze. Cutting them gives back some light."],
    ("CLOUD_LOSS", "rising"): ["Solar power follows the clouds: plan storage or backup for cloudier months."],
    ("CLOUD_LOSS", "falling"): ["Clouds are blocking less sun. More sun means more solar power and more heat: plan shade and cooling."],
    ("CLOUD_AMT", "rising"): ["Expect less solar power and cooler days when it is cloudier. Plan storage or backup."],
}


def _dir(t):
    if not t or not t["significant"]:
        return "flat"
    return t["direction"]


def _rate(vid: str, t: dict) -> float:
    w, g, d, how = RATE[vid]
    per = t["sen"]["slope_decade"] * 10.0 / (t.get("rate_years") or 10.0)
    return abs(per) / abs(t["mean"]) * 100.0 if how == "pct" and t.get("mean") else abs(per)


def _level(vid: str, t: dict) -> str:
    w, g, d, _ = RATE[vid]
    r = _rate(vid, t)
    return "danger" if r >= d else "warning" if r >= g else "watch" if r >= w else "none"


def alert_for(vid: str, t: dict | None, ctx: str = "solar") -> dict | None:
    """Alert for a sunlight or water-loss series. ctx is "solar" (sunlight on its own), "land" (soil and crops) or "sea" (coast)."""
    if t is None or vid not in RATE:
        return None
    d = _dir(t)
    A = lambda level, text, verdict, means, cause, rec: {"level": level, "text": text, "verdict": verdict, "means": means, "cause": cause, "recommend": list(rec), "policy": []}  # noqa: E731
    if d == "flat" or d not in WORDS[vid]:
        return A("none", "No alert", "No change", "No real change.", "", [])
    title, means, cause = WORDS[vid][d]
    lv = _level(vid, t)
    harm = (HARM.get(vid) or {}).get(ctx)
    if lv == "none":
        return A("none", "Small change", "No change", "It is changing very slowly. Keep watching.", "", [])
    big = {"watch": "Early warning: ", "warning": "Alert: ", "danger": "Alert: "}[lv]
    if harm == d:
        return A(lv, big + title + ("" if lv == "watch" else " fast"), "Not good", means, cause, REC.get((vid, ctx), []))
    if harm is None:
        return A("mixed", "Mixed: " + title, "Mixed", means + (" Good for solar power and crops that lack light, but it adds heat." if vid == "ALLSKY_SFC_SW_DWN" and d == "rising" else " Less solar power and slower growth where light is short, but less heat." if vid == "ALLSKY_SFC_SW_DWN" else ""), cause,
                 MIXED_REC.get((vid, d), []))
    return A("good", "Good: " + title, "Good", means, cause, [])


def outlook_alert(vid, t, ctx):
    a = alert_for(vid, t, ctx)
    if a is not None:
        a["outlook"] = coast.outlook(t)
    return a


# ---------------------------------------------------------------- combined risk
LAND_VARS = [("GWETROOT", 3.0, "Root-zone soil wetness"), ("GWETTOP", 2.0, "Surface soil wetness"), ("GWETPROF", 2.0, "Whole soil profile wetness"),
             ("PRECTOTCORR", 2.0, "Rain"), ("EVLAND", 1.0, "Evaporation from land"), ("EVPTRNS", 1.0, "Water given off by plants"), ("TSOIL1", 1.0, "Soil temperature"),
             ("T2M", 1.0, "Air temperature"), ("ALLSKY_SFC_SW_DWN", 1.0, "Sunlight"), ("ALLSKY_SFC_LW_DWN", 1.0, "Heat sent back down by the air"),
             ("ALLSKY_SFC_PAR_TOT", 1.0, "Light for plants"), ("CLOUD_AMT", 1.0, "Cloud cover"), ("ALLSKY_SFC_UV_INDEX", 0.5, "Ultraviolet light"), ("CLRSKY_SFC_SW_DWN", 0.5, "Clear-sky sunlight")]
SEA_VARS = [("TS", 3.0, "Sea surface temperature"), ("SEALEVEL", 3.0, "Sea level"), ("WAVE", 2.0, "Wave height"), ("WS10M", 2.0, "Wind speed"), ("T2M", 1.0, "Air temperature"),
            ("ALLSKY_SFC_SW_DWN", 1.0, "Sunlight"), ("ALLSKY_SFC_LW_DWN", 1.0, "Heat sent back down by the air"), ("CLOUD_AMT", 1.0, "Cloud cover"), ("ALLSKY_SFC_UV_INDEX", 1.0, "Ultraviolet light")]
SOLAR_IDS = {"ALLSKY_SFC_SW_DWN", "ALLSKY_SFC_LW_DWN", "ALLSKY_SFC_PAR_TOT", "CLOUD_AMT", "ALLSKY_SFC_UV_INDEX", "CLRSKY_SFC_SW_DWN"}
POINTS = {"danger": 3, "warning": 2, "watch": 1}
LEVEL_NAMES = [(10, "low", "Low"), (25, "moderate", "Moderate"), (45, "high", "High"), (101, "danger", "Very high")]


def _alert(vid: str, t: dict | None, kind: str):
    if t is None:
        return None
    if vid in RATE:
        return alert_for(vid, t, kind)
    if kind == "land":
        return soil.alert_for(vid, t)
    return coast.alert_for(vid, t)


def _d(res, vid):
    r = res.get(vid)
    return (r or {}).get("trend")


def _interactions(kind: str, res: dict) -> list:
    D = lambda v: _dir(_d(res, v))  # noqa: E731
    out = []
    if kind == "land":
        drying = D("GWETROOT") == "falling" or D("GWETTOP") == "falling"
        if drying and (D("ALLSKY_SFC_SW_DWN") == "rising" or D("T2M") == "rising" or D("EVLAND") == "rising"):
            out.append(("The soil is drying while more energy arrives (sunlight, heat or evaporation). The two push the same way, so the drying can speed up.",
                        ["Cover the soil and cut evaporation first: it is the cheapest way to hold water.", "Move watering to early morning or evening."]))
        if drying and D("PRECTOTCORR") == "falling":
            out.append(("Less rain is coming in while the soil dries. Water supply is shrinking on both sides.", ["Build and protect water storage (ponds, small dams, groundwater recharge).", "Plan crops and herd sizes for a drier future."]))
        if D("ALLSKY_SFC_PAR_TOT") == "falling" and D("CLRSKY_SFC_SW_DWN") == "falling":
            out.append(("Hazier air is cutting the light plants use. This lowers growth even when water is fine.", ["Find and cut local smoke and dust sources.", "Plant crops that grow well in lower light."]))
    else:
        if D("TS") == "rising" and D("ALLSKY_SFC_SW_DWN") == "rising":
            out.append(("The sea is warming while more sunlight arrives. Together they raise the chance of heat stress for reefs and fish.", ["Set up a marine heat watch with sea temperature alerts.", "Protect shallow reefs and nurseries from other stress."]))
        if D("TS") == "rising" and D("WS10M") == "falling":
            out.append(("The sea is warming and the wind is weakening. Less wind means less mixing, so the surface can warm even faster.", ["Watch algae blooms and low-oxygen water in calm weeks."]))
        if D("SEALEVEL") == "rising" and (D("WAVE") == "rising" or D("WS10M") == "rising"):
            out.append(("The sea is rising while waves or wind get stronger. Storms reach further inland and erode more.", ["Raise or move critical buildings and roads.", "Protect dunes, mangroves and reefs that break waves."]))
    return out


def assess(kind: str, place: dict, y0: int, m0: int, y1: int, m1: int, months: list, mode: str, getter=trends.get_json) -> dict:
    """Combined risk for one place. kind "land": soil + sunlight. kind "sea": coast + sunlight."""
    if kind not in ("land", "sea"):
        raise ValueError("Choose land or sea.")
    spec = LAND_VARS if kind == "land" else SEA_VARS
    res = trends.run([place], [v[0] for v in spec], y0, m0, y1, m1, months, mode, getter)
    pv = res["places"][0]
    if pv.get("error"):
        raise trends.DataError(pv["error"])
    rows, got, miss = [], 0.0, []
    total = 0.0
    for vid, w, name in spec:
        r = pv["vars"].get(vid) or {}
        t = r.get("trend")
        a = _alert(vid, t, kind)
        if t is None or a is None:
            miss.append(name)
            continue
        pts = POINTS.get(a["level"], 0)
        total += w * 3
        got += w * pts
        rows.append({"id": vid, "name": name, "weight": w, "level": a["level"], "text": a["text"], "verdict": a.get("verdict"), "means": a.get("means"), "points": pts,
                     "part": "Sunlight" if vid in SOLAR_IDS else ("Soil" if kind == "land" else "Coast"), "recommend": a.get("recommend") or [], "direction": t["direction"], "significant": t["significant"]})
    if not rows:
        raise trends.DataError("NASA did not return enough data for this place to assess the risk. " + (res["errors"][0] if res.get("errors") else ""))
    score = 100.0 * got / total if total else 0.0
    forced = None
    if any(r["level"] == "danger" and r["weight"] >= 3 for r in rows):
        forced = 25
    elif any(r["level"] == "danger" for r in rows):
        forced = 10
    final = max(score, forced or 0)
    name = next(n for lim, _k, n in LEVEL_NAMES if final < lim)
    key = next(k for lim, k, _n in LEVEL_NAMES if final < lim)
    parts = {}
    for part in ("Soil" if kind == "land" else "Coast", "Sunlight"):
        pr = [r for r in rows if r["part"] == part]
        pt = sum(r["weight"] * 3 for r in pr)
        sc = 100.0 * sum(r["weight"] * r["points"] for r in pr) / pt if pt else 0.0
        parts[part] = {"score": round(sc, 1), "level": next(k for lim, k, _n in LEVEL_NAMES if sc < lim), "level_text": next(n for lim, _k, n in LEVEL_NAMES if sc < lim), "count": len(pr)}
    drivers = sorted([r for r in rows if r["points"]], key=lambda r: -r["weight"] * r["points"])
    good = [r for r in rows if r["level"] == "good"]
    inter = _interactions(kind, pv["vars"])
    recs, seen = [], set()
    for text, rr in inter:
        for x in rr:
            if x not in seen:
                seen.add(x)
                recs.append(x)
    for r in drivers:
        for x in r["recommend"]:
            if x not in seen and len(recs) < 10:
                seen.add(x)
                recs.append(x)
    what = "land (soil and sunlight)" if kind == "land" else "sea and coast (coast and sunlight)"
    head = (f"Combined risk for {what}: {name.lower()}. " if drivers else f"Combined risk for {what}: low. ") + (f"{len(drivers)} of {len(rows)} measures are moving the wrong way." if drivers else "No measure is moving clearly the wrong way.")
    return {"kind": kind, "place": place, "score": round(final, 1), "level": key, "level_text": name, "headline": head, "parts": parts, "drivers": drivers, "good": good, "interactions": [t for t, _r in inter],
            "recommendations": recs or ["Keep watching these trends every year and repeat this check.", "Compare the numbers with local measurements and ask your local experts."], "missing": miss, "period": res["period"]["text"],
            "notes": ["The score adds up how many measures move the wrong way and how fast. Soil wetness, sea temperature and sea level count the most.",
                      "Levels are rough rules of thumb from NASA grid values (about 25 to 50 km), not a measurement at one field or beach. They show where to look, not what will happen.",
                      "Sunlight data comes from satellites (CERES) and the NASA model (MERRA-2). Measures that were not available at this place are left out: " + (", ".join(miss) if miss else "none") + "."]}
