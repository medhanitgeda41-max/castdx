"""Damage score: how much of an asset's value is expected to be lost in a month.

    damage (share of value) = sum over the three hazards of  chance(month) x loss-if-it-happens

  chance            the monthly probability from hazard_prob.py
  loss-if-it-happens  max loss share x severity^exponent x material factor x (1 - adaptation cut)
  severity (0-1)    from the AVERAGE size of the events at that place:
                      flood  average heaviest-day rain of a flood episode (mm)    20 mm -> 0.17 ... 120 mm -> 1
                      heat   average hottest day of a heat wave (deg C)            40 -> 0.07 ... 53 -> 1
                      quake  average magnitude of the main shocks of magnitude 6 or more within 150 km, month by month   6.0 -> 0.57 ... 7.5 -> 1
  geography         flood loss x (0.5 + geographic exposure): coast, ground height, wadi, flood zone (geo.py)
  earthquake        quake loss x (0.5 + exposure): the average distance and depth of those main shocks (quake_multiplier below)
  material          factors and loss caps are the ones in risk_rules.py (the RASD loss formula)
  adaptation        a completed measure cuts flood loss by 50% and heat loss by 30% (partly done = half of that)

The loss caps, exponents, material factors and adaptation cuts are ASSUMPTIONS taken from the RASD rules; they are not
measured. The damage score is therefore a ranking aid, not an insurance or engineering loss estimate.
"""
from __future__ import annotations

import risk_rules as rr

RAIN_RANGE = (0.0, 120.0)
HEAT_RANGE = (39.0, 53.0)          # a heat wave counts from 40 C; 39 keeps the same one-degree gap as the old 44-45 rule
QUAKE_RANGE = (4.0, 7.5)
QUAKE_NEAR_KM = 150.0               # earthquake multiplier: distance scores 1 at the site, 0 at this distance
QUAKE_SHALLOW_KM, QUAKE_DEEP_KM = 10.0, 70.0     # depth scores 1 at 10 km or less, 0 at 70 km or more
QUAKE_BASE = 0.5


def _unit(x, lo, hi):
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def severities(rain_mm: float, heat_c: float, quake_mag: float) -> dict:
    return {"flood": _unit(rain_mm, *RAIN_RANGE), "heat": _unit(heat_c, *HEAT_RANGE), "quake": _unit(quake_mag, *QUAKE_RANGE)}


def adaptation_level(text) -> float:
    """0 none, 0.5 partly, 1 completed, from the words in the file's adaptation column (many spellings are accepted)."""
    t = str(text if text is not None else "").strip().lower()
    if not t or t in ("nan", "n/a", "na", "-", "null"):
        return 0.0
    try:
        return max(0.0, min(1.0, float(t.rstrip("%")) / (100.0 if t.endswith("%") or float(t.rstrip("%")) > 1 else 1.0)))
    except ValueError:
        pass
    if t.startswith("not ") or "n't" in t:
        return 0.0
    for words, v in ((("incomplete", "in progress", "in-progress", "ongoing", "on-going", "underway", "under way", "under construction", "partial", "partly",
                       "part ", "half", "implementing", "being installed", "phase"), 0.5),
                     (("complete", "operational", "installed", "done", "implemented", "finished", "in place", "fully", "full ", "protected",
                       "mitigated", "yes", "true", "active", "built"), 1.0),
                     (("not started", "not yet", "planned", "proposed", "none", "no ", "false", "absent", "nil", "unprotected", "missing"), 0.0)):
        if any(w in t + " " for w in words):
            return v
    return 0.0


def _factors(material):
    text = (material or "").strip().lower()
    key = next((k for k in sorted(rr.FLOOD_FACTOR, key=len, reverse=True) if k in text), None)
    return rr.FLOOD_FACTOR.get(key, 1.0), rr.QUAKE_FACTOR.get(key, 1.0), key


def quake_multiplier(dist_km, depth_km):
    """(exposure 0-1 or None, multiplier 0.5-1.5): closer and shallower earthquakes shake a site harder.
    A fact that is not known (the catalogue has no depth, or no big earthquake is on record) is left out; with none the multiplier is 1.0."""
    parts = []
    if dist_km is not None:
        parts.append(_unit(QUAKE_NEAR_KM - dist_km, 0.0, QUAKE_NEAR_KM))
    if depth_km is not None:
        parts.append(_unit(QUAKE_DEEP_KM - depth_km, 0.0, QUAKE_DEEP_KM - QUAKE_SHALLOW_KM))
    if not parts:
        return None, 1.0
    e = sum(parts) / len(parts)
    return e, QUAKE_BASE + e


def adaptation_for(adaptation, hazard: str) -> float:
    """The adaptation level (0, 0.5 or 1) for one hazard. `adaptation` is one number for all hazards, or a dict {"flood", "heat", "quake"}.
    There is no earthquake cut in the RASD loss rules, so an earthquake adaptation status is recorded but does not change the loss."""
    if isinstance(adaptation, dict):
        return float(adaptation.get(hazard, 0.0) or 0.0)
    return float(adaptation or 0.0)


def loss_if_event(sev: dict, material, adaptation, geo_factor: float = 1.0, quake_factor: float = 1.0, heat_factor: float = 1.0) -> dict:
    """Share of the asset's value lost when a typical event of each hazard happens.
    geo_factor (geo.py, 0.5 to 1.5: coast, ground height, wadi, flood zone) multiplies the flood loss only."""
    ff, qf, _ = _factors(material)
    af, ah = adaptation_for(adaptation, "flood"), adaptation_for(adaptation, "heat")
    return {"flood": geo_factor * ff * rr.MAX_FLOOD * sev["flood"] ** rr.FLOOD_EXPONENT * (1 - rr.ADAPTATION_CUTS_FLOOD * af),
            "heat": heat_factor * ff * rr.MAX_HEAT * sev["heat"] ** rr.HEAT_EXPONENT * (1 - rr.ADAPTATION_CUTS_HEAT * ah),
            "quake": quake_factor * qf * rr.MAX_QUAKE * sev["quake"] ** rr.QUAKE_EXPONENT}


def _at(v, m):
    return v[m] if isinstance(v, (list, tuple)) else v


def monthly_losses(sev: dict, material, adaptation, geo_factor: float = 1.0, quake_factor=1.0, heat_factor: float = 1.0) -> dict:
    """loss_if_event for each of the 12 months: sev = {"flood": [12], "heat": [12], "quake": [12] or one number}."""
    out = {"flood": [], "heat": [], "quake": []}
    for m in range(12):
        one = loss_if_event({"flood": sev["flood"][m], "heat": sev["heat"][m], "quake": _at(sev["quake"], m)}, material, adaptation, geo_factor,
                            _at(quake_factor, m), heat_factor)
        for h in out:
            out[h].append(one[h])
    return out


def monthly_damage(probs: dict, loss: dict) -> dict:
    """{"flood": [12], "heat": [12], "quake": [12], "total": [12]} share of value expected to be lost each month."""
    out = {}
    for h in ("flood", "heat", "quake"):
        p = probs.get(h)
        out[h] = None if p is None else [x * (loss[h][m] if isinstance(loss[h], list) else loss[h]) for m, x in enumerate(p)]
    out["total"] = [sum((out[h][m] if out[h] else 0.0) for h in ("flood", "heat", "quake")) for m in range(12)]
    return out
