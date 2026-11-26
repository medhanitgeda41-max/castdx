"""Monthly probability of a flood-triggering rainfall, a heat wave and an earthquake at a place.

This replaces models trained on made-up labels with three things that can be checked against real records:

  Flood    probability that the month has at least one day with RAIN_MM or more of rain (a flood-triggering rainfall).
  Heat     probability that the month has a heat wave: HEAT_RUN_DAYS days in a row at HEAT_C or hotter.
  Quake    probability that at least one main earthquake of magnitude MIN_MAG or more happens within QUAKE_RADIUS_KM.
           (The earthquake's SIZE, depth and distance for the damage score use the main shocks of magnitude 6 or more only.)

Weather events are counted in the real daily record (Open-Meteo / ERA5) month by month. The rate of events in each calendar
month is pulled a little towards the rate of the whole UAE (PRIOR_STRENGTH pseudo-years), because a place has only a few years
of record. The chance of at least one event is then 1 - exp(-rate) (a Poisson count).
For a future year, the observed daily weather is shifted by the change the climate model shows between its reference years
and a window around that year (the "delta change" method), and the events are counted again.
Earthquakes use the USGS catalogue after removing aftershocks (Gardner and Knopoff 1974 windows) and a Poisson rate, so the
chance is the same in every month and every year.

validate_probabilities.py tests each of these on years the model did not see.
"""
from __future__ import annotations

import calendar
import math
from collections import defaultdict
from datetime import date

RAIN_MM = 20.0          # a flood-triggering rainfall: one day with at least this much rain
HEAT_C = 40.0           # a hot day: daily maximum at or above this
HEAT_RUN_DAYS = 3       # a heat wave: this many hot days in a row
QUAKE_RADIUS_KM = 150.0
QUAKE_MIN_MAG = 4.5
QUAKE_LOSS_MIN_MAG = 6.0     # the size of an earthquake for the DAMAGE score uses only main shocks of this magnitude or more (the chance still counts QUAKE_MIN_MAG and up)
QUAKE_START = date(1975, 1, 1)
FLOOD_SCALE_K = 3.0     # pseudo-events that pull a place's wetness factor towards 1 (chosen with validate_probabilities.py)
HEAT_PRIOR_STRENGTH = 0.0   # heat waves depend strongly on the place, so no pull towards the UAE-wide rate (see validate_probabilities.py)
MODEL_FIRST, MODEL_LAST = 2015, 2050     # the climate model's years
MODEL_REF = (2015, 2024)                 # model years that stand for "now"
MODEL_BEFORE, MODEL_AFTER = 4, 5         # a year's climate is the window Y-4 .. Y+5 (one year is noisy)
RAIN_FACTOR_LIMITS = (0.5, 2.0)          # the change in rain is never allowed to be bigger than this

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ------------------------------------------------------------------ weather events

def _runs(flags):
    """Lengths of the runs of True in a list of booleans."""
    run = 0
    for f in flags:
        if f:
            run += 1
        elif run:
            yield run
            run = 0
    if run:
        yield run


def month_counts(times, tmax, rain, rain_mm=None, heat_c=None, run_days=HEAT_RUN_DAYS) -> dict:
    rain_mm = RAIN_MM if rain_mm is None else rain_mm
    heat_c = HEAT_C if heat_c is None else heat_c
    """{(year, month): (rain_episodes, heat_waves)} for the complete months with no missing day.
    A rain episode is a run of days with at least rain_mm; a heat wave is a run of at least run_days days at heat_c or more."""
    by_month = defaultdict(list)
    for t, x, r in zip(times, tmax, rain):
        by_month[(int(t[:4]), int(t[5:7]))].append((t, x, r))
    out = {}
    for (y, m), days in by_month.items():
        if len(days) < calendar.monthrange(y, m)[1] or any(x is None or r is None for _, x, r in days):
            continue
        days.sort()
        rain_episodes = sum(1 for _ in _runs([r >= rain_mm for _, _, r in days]))
        heat_waves = sum(1 for n in _runs([x >= heat_c for _, x, _ in days]) if n >= run_days)
        out[(y, m)] = (rain_episodes, heat_waves)
    return out


def month_sums(counts: dict, years=None) -> dict:
    """Totals per calendar month: {"rain": [12], "heat": [12], "n": [12]} (n = number of months of record)."""
    s = {"rain": [0.0] * 12, "heat": [0.0] * 12, "n": [0] * 12}
    for (y, m), (r, h) in counts.items():
        if years is not None and y not in years:
            continue
        s["rain"][m - 1] += r
        s["heat"][m - 1] += h
        s["n"][m - 1] += 1
    return s


def pooled_prior(per_place_sums: list[dict]) -> dict:
    """UAE-wide mean number of episodes per month: {"rain": [12], "heat": [12]} from many places' month_sums."""
    prior = {"rain": [0.0] * 12, "heat": [0.0] * 12}
    for key in prior:
        for i in range(12):
            total = sum(s[key][i] for s in per_place_sums)
            n = sum(s["n"][i] for s in per_place_sums)
            prior[key][i] = total / n if n else 0.0
    return prior


def shrunk_rates(sums: dict, prior: dict | None, c: float = 2.0) -> dict:
    """Episodes per month for each calendar month: (episodes + c x UAE rate) / (months of record + c)."""
    out = {}
    for key in ("rain", "heat"):
        rates = []
        for i in range(12):
            p = prior[key][i] if prior else 0.0
            denom = sums["n"][i] + (c if prior else 0.0)
            rates.append((sums[key][i] + (c * p if prior else 0.0)) / denom if denom else 0.0)
        out[key] = rates
    return out


def prob_from_rate(rate: float) -> float:
    return 1.0 - math.exp(-max(0.0, rate))


def scaled_rates(sums: dict, prior: dict | None, k: float, key: str = "rain") -> list[float]:
    """Episodes per month = the UAE-wide rate of that calendar month x a factor for this place.
    The factor is the place's total episodes over the episodes the UAE-wide rate expects for the same months,
    pulled towards 1 by k pseudo-events. It uses all months of the record, so it is much steadier than 12 separate monthly rates."""
    if not prior:
        return [sums[key][i] / sums["n"][i] if sums["n"][i] else 0.0 for i in range(12)]
    expected = sum(sums["n"][i] * prior[key][i] for i in range(12))
    factor = (sum(sums[key]) + k) / (expected + k) if (expected + k) > 0 else 1.0
    return [prior[key][i] * factor for i in range(12)]


def weather_probabilities(sums: dict, prior: dict | None, sums_heat: dict | None = None) -> dict:
    """{"flood": [12], "heat": [12]}: chance that each calendar month has at least one event.
    sums_heat lets the heat waves use a shorter, more recent record than the rain (the climate is warming)."""
    flood = scaled_rates(sums, prior, FLOOD_SCALE_K, "rain")
    heat = shrunk_rates(sums_heat or sums, prior, HEAT_PRIOR_STRENGTH)["heat"]
    return {"flood": [prob_from_rate(x) for x in flood], "heat": [prob_from_rate(x) for x in heat]}


# ------------------------------------------------------------------ future years (delta change)

def projection_window(year: int) -> tuple[int, int]:
    """The ten model years around a year: Y-4 .. Y+5, kept inside the model's years."""
    first = max(MODEL_FIRST, year - MODEL_BEFORE)
    last = min(MODEL_LAST, first + MODEL_BEFORE + MODEL_AFTER)
    first = max(MODEL_FIRST, last - (MODEL_BEFORE + MODEL_AFTER))
    return first, last


def climate_change(model_times, model_tmax, model_rain, year: int) -> dict:
    """Change the climate model shows for each calendar month between its reference years and the window around `year`:
    {"dT": [12] (degrees C added to the daily maximum), "rain_factor": [12] (multiplies the daily rain)}."""
    first, last = projection_window(year)

    def stats(y0, y1):
        t_sum, t_n = [0.0] * 12, [0] * 12
        r_sum, years = [0.0] * 12, [set() for _ in range(12)]
        for t, x, r in zip(model_times, model_tmax, model_rain):
            y = int(t[:4])
            if y < y0 or y > y1 or x is None or r is None:
                continue
            m = int(t[5:7]) - 1
            t_sum[m] += x
            t_n[m] += 1
            r_sum[m] += r
            years[m].add(y)
        mean_t = [t_sum[i] / t_n[i] if t_n[i] else None for i in range(12)]
        mean_total = [r_sum[i] / len(years[i]) if years[i] else None for i in range(12)]
        return mean_t, mean_total

    ref_t, ref_r = stats(*MODEL_REF)
    win_t, win_r = stats(first, last)
    low, high = RAIN_FACTOR_LIMITS
    dT, factor = [], []
    for i in range(12):
        dT.append(win_t[i] - ref_t[i] if win_t[i] is not None and ref_t[i] is not None else 0.0)
        if win_r[i] is not None and ref_r[i] is not None:
            factor.append(min(high, max(low, (win_r[i] + 1.0) / (ref_r[i] + 1.0))))   # +1 mm so a dry month does not give 0/0
        else:
            factor.append(1.0)
    return {"dT": dT, "rain_factor": factor, "window": (first, last)}


def ensemble_change(changes: list[dict]) -> dict:
    """Average of the changes shown by several climate models (multi-model mean). Also keeps the spread between the models:
    dT_range = (lowest, highest) yearly-average warming among the models, rain_range likewise for the average rain factor."""
    n = len(changes)
    dT = [sum(c["dT"][i] for c in changes) / n for i in range(12)]
    rf = [sum(c["rain_factor"][i] for c in changes) / n for i in range(12)]
    ann = [sum(c["dT"]) / 12 for c in changes]
    rann = [sum(c["rain_factor"]) / 12 for c in changes]
    return {"dT": dT, "rain_factor": rf, "window": changes[0]["window"], "n_models": n,
            "dT_range": (min(ann), max(ann)), "rain_range": (min(rann), max(rann))}


def adjusted_counts(times, tmax, rain, change: dict) -> dict:
    """month_counts of the observed daily weather after applying the climate change (a shift in the daily maximum and a
    multiplier on the daily rain, both different for each calendar month)."""
    t2, r2 = [], []
    for t, x, r in zip(times, tmax, rain):
        m = int(t[5:7]) - 1
        t2.append(None if x is None else x + change["dT"][m])
        r2.append(None if r is None else r * change["rain_factor"][m])
    return month_counts(times, t2, r2)


# ------------------------------------------------------------------ earthquakes

def km_between(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 12742.0 * math.asin(min(1.0, math.sqrt(a)))


def gk_windows(mag: float) -> tuple[float, float]:
    """Gardner and Knopoff (1974): (distance in km, time in days) around a main shock inside which smaller shocks are aftershocks."""
    dist = 10 ** (0.1238 * mag + 0.983)
    days = 10 ** (0.032 * mag + 2.7389) if mag >= 6.5 else 10 ** (0.5409 * mag - 0.547)
    return dist, days


def parse_events(raw) -> list[tuple]:
    """[(lat, lon, magnitude, day_number)] from the catalogue rows [lat, lon, magnitude, 'YYYY-MM-DD']."""
    out = []
    for e in raw:
        try:
            out.append((float(e[0]), float(e[1]), float(e[2]), date.fromisoformat(str(e[3])[:10]).toordinal()))
        except (ValueError, IndexError, TypeError):
            continue
    return out


def decluster(events: list[tuple]) -> list[tuple]:
    """Keep the main shocks: drop an event that follows a bigger or equal one inside the Gardner and Knopoff windows."""
    order = sorted(range(len(events)), key=lambda i: (-events[i][2], events[i][3]))
    gone = set()
    for i in order:
        if i in gone:
            continue
        lat, lon, mag, day = events[i]
        dist, days = gk_windows(mag)
        for j, (la, lo, mg, d) in enumerate(events):
            if j == i or j in gone or mg > mag or not (0 < d - day <= days):
                continue
            if abs(la - lat) < dist / 100.0 and km_between(lat, lon, la, lo) <= dist:
                gone.add(j)
    return [e for k, e in enumerate(events) if k not in gone]


def b_value(mainshocks: list[tuple], floor: float = QUAKE_MIN_MAG) -> float:
    """Gutenberg-Richter b of the catalogue (Aki 1965 / Utsu 1966 maximum likelihood, magnitudes given to 0.1)."""
    mags = [m for _, _, m, _ in mainshocks if m >= floor]
    if len(mags) < 30:
        return 1.0
    return math.log10(math.e) / (sum(mags) / len(mags) - (floor - 0.05))


def quake_rate(mainshocks: list[tuple], lat: float, lon: float, years: float, min_mag: float = QUAKE_MIN_MAG,
               radius_km: float = QUAKE_RADIUS_KM, b: float | None = None) -> tuple[float, int]:
    """(main shocks per year, number of main shocks of magnitude QUAKE_MIN_MAG or more) within radius_km.
    The rate at QUAKE_MIN_MAG counts the place's own events (+0.5, Jeffreys prior, so a quiet place is not exactly 0).
    A bigger magnitude is scaled down with the Gutenberg-Richter law, because counting the few big events directly is too noisy."""
    n = sum(1 for la, lo, mg, _ in mainshocks
            if mg >= QUAKE_MIN_MAG and abs(la - lat) < radius_km / 100.0 and km_between(lat, lon, la, lo) <= radius_km)
    rate = (n + 0.5) / max(years, 1.0)
    if min_mag > QUAKE_MIN_MAG:
        rate *= 10 ** (-(b if b is not None else b_value(mainshocks)) * (min_mag - QUAKE_MIN_MAG))
    return rate, n


def quake_monthly_prob(rate_per_year: float) -> float:
    return prob_from_rate(rate_per_year / 12.0)


def catalogue_years(events: list[tuple]) -> float:
    """Length of the catalogue in years: from QUAKE_START to the last event."""
    return max(1.0, (max(e[3] for e in events) - QUAKE_START.toordinal()) / 365.25) if events else 1.0


# ------------------------------------------------------------------ combining and ranking

def annual_prob(p12: list[float]) -> float:
    """Chance of at least one event in the year, from the 12 monthly chances (months taken as independent)."""
    q = 1.0
    for p in p12:
        q *= 1.0 - p
    return 1.0 - q


def any_hazard(*probs: float) -> float:
    q = 1.0
    for p in probs:
        q *= 1.0 - p
    return 1.0 - q


def rank_desc(values: list[float]) -> list[int]:
    """1 = highest. Equal values (to 4 decimals) share a rank: 1, 2, 2, 4."""
    keyed = [round(v, 4) for v in values]
    return [1 + sum(1 for w in keyed if w > k) for k in keyed]


# ------------------------------------------------------------------ how big the events are (for the damage score)

def weather_severity(times, tmax, rain, change: dict | None = None, rain_mm=None, heat_c=None, run_days=HEAT_RUN_DAYS) -> dict:
    rain_mm = RAIN_MM if rain_mm is None else rain_mm
    heat_c = HEAT_C if heat_c is None else heat_c
    """Average size of the events in the record.
    rain_mm / heat_c: all-year average of the heaviest day of each rain episode / the hottest day of each heat wave;
    rain_by_month / heat_by_month: the same average for the events whose peak fell in that calendar month (None when
    the record has no event in that month); rain_n / heat_n: number of events per month.
    With a climate change the daily values are shifted/scaled first, the same way adjusted_counts does."""
    days = []
    for t, x, r in zip(times, tmax, rain):
        m = int(t[5:7]) - 1
        if change:
            x = None if x is None else x + change["dT"][m]
            r = None if r is None else r * change["rain_factor"][m]
        days.append((t, x, r))
    days.sort()
    rain_peaks, heat_peaks = [], []            # (month 0-11, peak)
    run_r, run_h = [], []
    for t, x, r in days + [("", None, None)]:
        if r is not None and r >= rain_mm:
            run_r.append((t, r))
        elif run_r:
            p = max(run_r, key=lambda a: a[1]); rain_peaks.append((int(p[0][5:7]) - 1, p[1])); run_r = []
        if x is not None and x >= heat_c:
            run_h.append((t, x))
        elif run_h:
            if len(run_h) >= run_days:
                p = max(run_h, key=lambda a: a[1]); heat_peaks.append((int(p[0][5:7]) - 1, p[1]))
            run_h = []

    def summarize(peaks, default):
        allv = [v for _, v in peaks]
        by = [[v for mm, v in peaks if mm == m] for m in range(12)]
        return (sum(allv) / len(allv) if allv else default), [sum(b) / len(b) if b else None for b in by], [len(b) for b in by]
    rain_all, rain_by, rain_n = summarize(rain_peaks, rain_mm)
    heat_all, heat_by, heat_n = summarize(heat_peaks, heat_c)
    return {"rain_mm": rain_all, "heat_c": heat_all, "rain_by_month": rain_by, "heat_by_month": heat_by,
            "rain_n": rain_n, "heat_n": heat_n, "rain_events": len(rain_peaks), "heat_events": len(heat_peaks)}


def quake_mean_magnitude(mainshocks, lat, lon, min_mag=QUAKE_MIN_MAG, radius_km=QUAKE_RADIUS_KM) -> float:
    mags = [mg for la, lo, mg, _ in mainshocks
            if mg >= min_mag and abs(la - lat) < radius_km / 100.0 and km_between(lat, lon, la, lo) <= radius_km]
    return sum(mags) / len(mags) if mags else min_mag


def quake_loss_events(mainshocks, lat, lon, depths=None, min_mag=QUAKE_LOSS_MIN_MAG, radius_km=QUAKE_RADIUS_KM) -> list[tuple]:
    """[(month 0-11, magnitude, distance km, depth km or None)] of the main shocks of magnitude min_mag or more within radius_km.
    depths: {(lat, lon, day_number): depth_km} when the catalogue has depths."""
    out = []
    for la, lo, mg, day in mainshocks:
        if mg < min_mag or abs(la - lat) >= radius_km / 100.0:
            continue
        d = km_between(lat, lon, la, lo)
        if d <= radius_km:
            out.append((date.fromordinal(day).month - 1, mg, d, (depths or {}).get((la, lo, day))))
    return out


def quake_size_by_month(events: list[tuple], default_mag: float = QUAKE_LOSS_MIN_MAG) -> dict:
    """Average magnitude, distance and depth of the big earthquakes of each calendar month. A month with none uses the
    average of all months; a place with none uses default_mag and unknown (None) distance and depth."""
    def avg(vals):
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if vals else None
    res = {"n": [sum(1 for e in events if e[0] == m) for m in range(12)], "n_all": len(events)}
    for key, i, dflt in (("mag", 1, default_mag), ("dist", 2, None), ("depth", 3, None)):
        overall = avg([e[i] for e in events])
        by = [avg([e[i] for e in events if e[0] == m]) for m in range(12)]
        res[key] = [v if v is not None else (overall if overall is not None else dflt) for v in by]
        res[key + "_all"] = overall if overall is not None else dflt
    return res
