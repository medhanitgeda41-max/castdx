"""Forecasting station: climate data, monthly chance of heavy rain, heat wave and earthquake, and the money damage, for one place and any
period up to 2050. This wraps the RASD Climate Map engine (folder rasd_core/): weather history and climate models from Open-Meteo, earthquakes
from USGS, the RASD damage rules and the trained money model. No API key is used.

Honest limits: the chances and damages are model results, not forecasts. The built-in money model was trained on AUGMENTED data made from the
RASD rules, not on real repair bills; train it again with your own csv (see train()). The live services were not tested from the build machine."""
from __future__ import annotations

import base64
import csv
import io
import math
import re
import sys
import tempfile
import threading
import time
import uuid
from datetime import date
from pathlib import Path

CORE = Path(__file__).resolve().parent / "rasd_core"
if str(CORE) not in sys.path:
    sys.path.append(str(CORE))           # appended, so the CASTD module names always win

import geo                      # noqa: E402
import hazard_prob as hp        # noqa: E402
import moneymodel as mm         # noqa: E402
import prob_data as pdm         # noqa: E402

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
LAST_YEAR = 2050
MAX_MONTHS = 300                 # 25 years
geo.LOW_GROUND_M = 30.0
geo.COAST_NEAR_KM = 20.0
LOCK = threading.Lock()          # the thresholds are shared settings, so one lookup at a time
_PLACES: dict = {}
NO_PROTECTION = {"flood": 0.0, "heat": 0.0, "quake": 0.0}
RATING_STEPS = (1.5, 4.0)


def scrub(text) -> str:
    return re.sub(r"(key|appid)=[^&\s]+", r"\1=<hidden>", str(text))[:300]


def _num(q, name, lo, hi, default=None):
    v = q.get(name)
    if v in (None, "") and default is not None:
        v = default
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"Choose a place first ({name} is missing).")
    if not lo <= v <= hi:
        raise ValueError(f"{name} must be between {lo} and {hi}.")
    return v


def months_between(y0, m0, y1, m1) -> list:
    out, y, m = [], y0, m0
    while (y, m) <= (y1, m1):
        out.append((y, m))
        y, m = (y, m + 1) if m < 12 else (y + 1, 1)
    return out


def make_asset(lat, lon) -> dict:
    return {"id": "", "name": f"{lat:.3f}, {lon:.3f}", "lat": lat, "lon": lon, "cell": (round(lat, 1), round(lon, 1)), "material": None,
            "adaptation": 0.0, "adapt": dict(NO_PROTECTION), "value_aed": 1.0, "row": {}}


def prepare(lat, lon, years, rain_mm, heat_c, progress=lambda t: None):
    """Everything downloaded for one place (kept for the next request on the same place). Call with LOCK held."""
    key = (round(lat, 3), round(lon, 3), rain_mm, heat_c)
    notes: list = []
    if key not in _PLACES:
        if len(_PLACES) >= 12:
            _PLACES.pop(next(iter(_PLACES)))
        hp.RAIN_MM, hp.HEAT_C = rain_mm, heat_c
        asset = make_asset(lat, lon)
        engine = pdm.Engine()
        engine.set_assets([asset])
        progress("Reading 30 years of daily weather (Open-Meteo)...")
        engine.fetch_history("internet", None, lambda t: None)
        progress("Reading ground height and distance to the coast...")
        geo_notes = geo.attach([asset], internet=True, progress=lambda t: None, use_wadi=False)
        rows, qnote = [], None
        progress("Reading earthquake records (USGS)...")
        try:
            rows = pdm.tile_rows(pdm.quake_tiles([asset])[0])
            pdm.refresh_quakes([asset])
        except Exception as e:      # noqa: BLE001
            pdm.load_quakes(force=True)
            qnote = f"Earthquakes could not be downloaded ({scrub(e)[:70]}), so the earthquake chance is not known."
        _PLACES[key] = {"asset": asset, "engine": engine, "quake_rows": rows, "notes": [scrub(n) for n in geo_notes][:2] + ([qnote] if qnote else []),
                        "quakes": dict(pdm.load_quakes())}
    pl = _PLACES[key]
    hp.RAIN_MM, hp.HEAT_C = rain_mm, heat_c
    pdm._QUAKES.clear()
    pdm._QUAKES.update(pl["quakes"])
    notes += pl["notes"]
    if pl["engine"].needs_model(years):
        progress("Reading the climate models for the future (Open-Meteo, five models)...")
        try:
            pl["engine"].fetch_model()
        except Exception as e:      # noqa: BLE001
            notes.append(f"Climate models could not be read ({scrub(e)[:70]}), so the future is shown as today's climate.")
    return pl, notes


def month_inputs(asset, pr, m0):
    g = asset.get("geo") or {}
    p = {h: (pr[h][m0] if pr.get(h) else 0.0) or 0.0 for h in ("flood", "heat", "quake")}
    sev = {h: pr["severity"][h][m0] for h in mm.HAZ}
    geof = {"flood": g.get("factor") or 1.0, "heat": g.get("heat_geo") or 1.0, "quake": pr["quake_info"]["factor"][m0]}
    return p, sev, geof


def inputs_for(pl, months):
    out = []
    for (y, m) in months:
        pr = pl["engine"].probs(pl["asset"], y)
        if pr["flood"] is None:
            raise ValueError("Weather history could not be downloaded for this place, so the result cannot be worked out.")
        p, sev, geof = month_inputs(pl["asset"], pr, m - 1)
        if pl["quakes"].get("empty"):
            p["quake"] = 0.0
        out.append((p, sev, geof, pr))
    return out


def total_damage(ins, mix, adapt, value, model):
    return sum(mm.share_of_value(mm.vector(mix, value, p, sev, geof, adapt), model) for p, sev, geof, _ in ins) * value


def rating_of(pct):
    return "Lower risk" if pct < RATING_STEPS[0] else "Medium risk" if pct < RATING_STEPS[1] else "Higher risk"


def recommend(ins, mix, adapt, value, model, total, n, money_fmt):
    names = {"flood": "flood", "heat": "heat", "quake": "earthquake"}
    ideas = []
    for h in mm.HAZ:
        if adapt[h] < 0.9:
            better = dict(adapt, **{h: 1.0})
            ideas.append((total - total_damage(ins, mix, better, value, model), f"Raise {names[h]} protection from {adapt[h] * 100:.0f}% to 100%"))
    top = max(mm.MATERIALS, key=lambda k: mix[k])
    alts = [(total - total_damage(ins, {k: (1.0 if k == m else 0.0) for k in mm.MATERIALS}, adapt, value, model), m)
            for m in mm.MATERIALS if mix[m] < 0.999 and m != "other"]
    if alts:
        sv, m = max(alts)
        if m != top:
            ideas.append((sv, f"Build mostly with {mm.MAT_NAMES[m].lower()} instead of {mm.MAT_NAMES[top].lower()}"))
    ideas = sorted([i for i in ideas if i[0] >= 0.03 * max(total, 1.0)], reverse=True)[:3]
    full = {h: 1.0 for h in mm.HAZ}
    best_mix = max(({k: (1.0 if k == m else 0.0) for k in mm.MATERIALS} for m in mm.MATERIALS if m != "other"),
                   key=lambda mx: -total_damage(ins, mx, full, value, model))
    best_total = total_damage(ins, best_mix, full, value, model)
    yearly = total / value * 12.0 / max(1, n) * 100.0
    best_yearly = best_total / value * 12.0 / max(1, n) * 100.0
    rating = rating_of(yearly)
    lines = [f"{t}: saves about {money_fmt(sv)}" for sv, t in ideas] or ["No big change needed: the protection and materials already suit this place and period."]
    if rating == "Higher risk" and rating_of(best_yearly) == "Higher risk":
        lines.append("Even with full protection and the best material the risk stays high here. Consider a different place.")
    return {"rating": rating, "yearly_pct": yearly, "recommendations": lines}


def rain_facts(series, rain_mm, heat_c):
    times, rain, tmax = series["time"], series["rain"], series["tmax"]
    years = sorted({int(t[:4]) for t in times}) or [0]
    n = max(1, len(years))
    heavy = sum(1 for r in rain if r is not None and r >= rain_mm)
    hot = sum(1 for t in tmax if t is not None and t >= heat_c)
    best = max(((r, t) for r, t in zip(rain, times) if r is not None), default=(None, None))
    hottest = max(((v, t) for v, t in zip(tmax, times) if v is not None), default=(None, None))
    return {"years": f"{years[0]}-{years[-1]}", "heavy_days_year": heavy / n, "hot_days_year": hot / n, "max_rain_mm": best[0], "max_rain_date": best[1],
            "hottest_c": hottest[0], "hottest_date": hottest[1]}


def monthly_base(series):
    """Average daily maximum temperature and total rain of each calendar month over the history."""
    t_sum, t_n, r_sum = [0.0] * 12, [0] * 12, [0.0] * 12
    years = set()
    for t, x, r in zip(series["time"], series["tmax"], series["rain"]):
        m = int(t[5:7]) - 1
        years.add(t[:4])
        if x is not None:
            t_sum[m] += x; t_n[m] += 1
        if r is not None:
            r_sum[m] += r
    ny = max(1, len(years))
    return [t_sum[m] / t_n[m] if t_n[m] else None for m in range(12)], [r_sum[m] / ny for m in range(12)]


def climate_change_for(entry, year):
    if year <= pdm.current_year() or not entry.get("model"):
        return None
    ms = entry["model"]
    if "time" in ms:
        ms = {"model": ms}
    return hp.ensemble_change([hp.climate_change(m["time"], m["tmax"], m["rain"], year) for m in ms.values()])


def forecast(body: dict, progress=lambda t: None) -> dict:
    lat, lon = _num(body, "lat", -90, 90), _num(body, "lon", -180, 180)
    cy = date.today().year
    y0, m0 = int(_num(body, "from_y", cy, LAST_YEAR)), int(_num(body, "from_m", 1, 12))
    y1, m1 = int(_num(body, "to_y", cy, LAST_YEAR)), int(_num(body, "to_m", 1, 12))
    months = months_between(y0, m0, y1, m1)
    if not months:
        raise ValueError("The period must end in the same month it starts, or later.")
    if len(months) > MAX_MONTHS:
        raise ValueError("The period can be at most 25 years (300 months).")
    value = _num(body, "value", 1, 1e15, 1_000_000)
    pct = body.get("mix") or {}
    mix = mm.normalise_mix({m: _num({"v": pct.get(m) or 0}, "v", 0, 100) for m in mm.MATERIALS})
    ad = body.get("adapt") or {}
    adapt = {h: _num({"v": ad.get(h) or 0}, "v", 0, 100) / 100.0 for h in mm.HAZ}
    rain_mm = _num(body, "rain_mm", 1, 500, 20)
    heat_c = _num(body, "heat_c", 10, 60, 40)
    cur = re.sub(r"[^A-Za-z$€£¥₹ ]", "", str(body.get("currency") or ""))[:6].strip()
    model = mm.load_model()
    if not model:
        raise ValueError("The damage money model file (rasd_core/data/money_model.json) is missing.")
    with LOCK:
        pl, notes = prepare(lat, lon, sorted({y for y, _ in months}), rain_mm, heat_c, progress)
        progress("Working out the chances and the damage...")
        unknown = bool(pl["quakes"].get("empty"))
        ins = inputs_for(pl, months)
        entry = pl["engine"].cells.get(pl["asset"]["cell"]) or {}
        series = entry.get("series")
        base_t, base_r = monthly_base(series)
        changes = {}
        out = []
        for (y, m), (p, sev, geof, pr) in zip(months, ins):
            if y not in changes:
                changes[y] = climate_change_for(entry, y)
            ch = changes[y]
            i = m - 1
            tmax = None if base_t[i] is None else base_t[i] + (ch["dT"][i] if ch else 0.0)
            rain = base_r[i] * (ch["rain_factor"][i] if ch else 1.0)

            def money(a):
                share = mm.share_of_value(mm.vector(mix, value, p, sev, geof, a), model)
                parts = mm.rule_parts(mix, p, sev, geof, a)
                tot = sum(parts.values())
                return {h: (share * value * parts[h] / tot if tot > 0 else 0.0) for h in mm.HAZ}
            d = money(adapt)
            size = pr["size"]
            out.append({"label": f"{MONTHS[i][:3]} {y}", "y": y, "m": m, "p": p, "dmg": d, "total": sum(d.values()), "none": sum(money(NO_PROTECTION).values()),
                        "clim": {"tmax": tmax, "rain": rain, "rain_peak": size["rain_mm"][i], "heat_peak": size["heat_c"][i], "quake_mag": size["quake_mag"]}})
        total_now = sum(x["total"] for x in out)
        advice = recommend(ins, mix, adapt, value, model, total_now, len(months), lambda v: (cur + " " if cur else "") + f"{v:,.0f}")
        chances = {h: 1.0 - math.prod(1.0 - x["p"][h] for x in out) for h in mm.HAZ}
        g = pl["asset"].get("geo") or {}
        facts = rain_facts(series, rain_mm, heat_c)
        qrows = pl["quake_rows"]
        near = sorted(((r[2], r[3], hp.km_between(lat, lon, r[0], r[1])) for r in qrows if hp.km_between(lat, lon, r[0], r[1]) <= hp.QUAKE_RADIUS_KM), reverse=True)
        facts.update({"elevation_m": g.get("elevation_m"),
                      "coast_km": None if g.get("coast_km") is None or g["coast_km"] >= geo.SEARCH_KM else g["coast_km"],
                      "quakes_near": None if unknown else len(near), "biggest_quake": None if unknown or not near else near[0][0],
                      "quake_radius_km": hp.QUAKE_RADIUS_KM, "quake_since": hp.QUAKE_START.year})
        future_note = next((pl["engine"].probs(pl["asset"], y).get("note") for y, _ in months if y > pdm.current_year()), "")
    if unknown:
        notes.append("Earthquakes could not be downloaded, so earthquake chance and damage are shown as 0.")
    pct_in = sum(max(0.0, float(pct.get(m) or 0)) for m in mm.MATERIALS)
    if abs(pct_in - 100) > 0.5:
        notes.append(f"The material percentages added up to {pct_in:.0f}%, so they were scaled to 100%.")
    tot = {h: sum(x["dmg"][h] for x in out) for h in mm.HAZ}
    total = sum(tot.values())
    none = sum(x["none"] for x in out)
    return {"lat": lat, "lon": lon, "months": out, "total": total, "by_hazard": tot, "value": value, "no_protection": none, "saved": max(0.0, none - total),
            "n": len(out), "worst": max(out, key=lambda x: x["total"])["label"], "mix": {m: mix[m] * 100 for m in mm.MATERIALS},
            "adapt": {h: adapt[h] * 100 for h in mm.HAZ}, "notes": notes, "future_note": future_note, "model": mm.info(),
            "synthetic": bool(model.get("training_is_synthetic")), "rating": advice["rating"], "yearly_pct": advice["yearly_pct"],
            "recommendations": advice["recommendations"], "chances": chances, "facts": facts, "rain_mm": rain_mm, "heat_c": heat_c, "currency": cur}


# ------------------------------------------------------------------ jobs (a lookup can take a few minutes, so the page asks again and again)
JOBS: dict = {}


def start(kind: str, fn, *args) -> str:
    for k in [k for k, j in JOBS.items() if time.time() - j["t"] > 3600]:
        JOBS.pop(k, None)
    jid = uuid.uuid4().hex[:12]
    job = {"t": time.time(), "status": "Starting...", "done": False, "error": None, "result": None, "kind": kind}
    JOBS[jid] = job

    def work():
        try:
            job["result"] = fn(*args, lambda t: job.__setitem__("status", t))
        except Exception as e:      # noqa: BLE001
            job["error"] = scrub(e) if not isinstance(e, ValueError) else str(e)
        finally:
            job["done"] = True
    threading.Thread(target=work, daemon=True).start()
    return jid


def job_state(jid: str) -> dict:
    j = JOBS.get(jid)
    if not j:
        raise ValueError("That lookup is not known any more. Run it again.")
    return {"done": j["done"], "status": j["status"], "error": j["error"], "result": j["result"] if j["done"] else None}


def run_forecast(body: dict) -> dict:
    # check the inputs before starting, so a typing mistake answers at once
    _num(body, "lat", -90, 90); _num(body, "lon", -180, 180)
    return {"job": start("forecast", lambda b, prog: forecast(b, prog), body)}


# ------------------------------------------------------------------ training the money model
TRAIN = {"busy": False, "status": "", "error": None, "text": ""}


def vectors_from_locations(rows, progress):
    parsed, bad = [], 0
    for r in rows:
        try:
            lat, lon = mm._f(r["latitude"]), mm._f(r["longitude"])
            month, year = int(mm._f(r["month"])), int(mm._f(r["year"]))
            mix = mm.normalise_mix({m: mm._f(r[m + "_pct"]) for m in mm.MATERIALS})
            value, money = mm._f(r["asset_value"]), mm._f(r["damage_recovery_money"])
            ad = {h: max(0.0, min(1.0, mm._f(r[n + "_adaptation_pct"]) / 100.0)) for h, n in zip(mm.HAZ, ("flood", "heat", "earthquake"))}
            if not (-90 <= lat <= 90 and -180 <= lon <= 180 and 1 <= month <= 12 and value > 0 and money >= 0):
                raise ValueError
            parsed.append(((round(lat, 3), round(lon, 3)), month, year, mix, value, ad, money))
        except (ValueError, KeyError, TypeError):
            bad += 1
    places = sorted({p[0] for p in parsed})
    if len(parsed) < mm.MIN_ROWS:
        raise ValueError(f"Only {len(parsed)} usable rows (at least {mm.MIN_ROWS} are needed). {bad} row(s) had a missing or invalid number.")
    if len(places) > 150:
        raise ValueError(f"The file has {len(places)} different places; at most 150 are supported.")
    X, y, val, skipped, got = [], [], [], 0, {}
    for i, (lat, lon) in enumerate(places, 1):
        progress(f"Looking up place {i} of {len(places)} ...")
        try:
            with LOCK:
                got[(lat, lon)] = prepare(lat, lon, sorted({p[2] for p in parsed if p[0] == (lat, lon)}), hp.RAIN_MM, hp.HEAT_C)[0]
        except Exception:      # noqa: BLE001
            got[(lat, lon)] = None
    for pl_key, month, year, mix, value, ad, money in parsed:
        pl = got.get(pl_key)
        if pl is None:
            skipped += 1
            continue
        with LOCK:
            pdm._QUAKES.clear(); pdm._QUAKES.update(pl["quakes"])
            pr = pl["engine"].probs(pl["asset"], year)
        if pr["flood"] is None:
            skipped += 1
            continue
        p, sev, geof = month_inputs(pl["asset"], pr, month - 1)
        X.append(mm.vector(mix, value, p, sev, geof, ad)); y.append(money); val.append(value)
    return X, y, val, bad + skipped


def train(body: dict) -> dict:
    if TRAIN["busy"]:
        raise ValueError("Please wait: a training is still running.")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", str(body.get("name") or "training.csv"))[:80]
    raw = base64.b64decode(body.get("b64") or "")
    if not raw:
        raise ValueError("The file is empty.")
    if len(raw) > 30_000_000:
        raise ValueError("The file is too big (30 MB at most).")
    path = Path(tempfile.gettempdir()) / ("castd_train_" + uuid.uuid4().hex[:8] + "_" + name)
    path.write_bytes(raw)
    rows = mm.read_dicts(str(path))
    have = set(rows[0].keys()) if rows else set()
    ready = all(c in have for c in mm.CSV_COLUMNS[1:])
    if not ready and not all(c in have for c in mm.LOCATION_COLUMNS):
        missing = [c for c in mm.LOCATION_COLUMNS if c not in have]
        raise ValueError("These columns are missing: " + ", ".join(missing) + ". Download the training template to see the columns.")

    def work():
        TRAIN.update(busy=True, error=None, status="Reading the file ...")
        try:
            prog = lambda t: TRAIN.__setitem__("status", t)      # noqa: E731
            if ready:
                X, y, val, bad = mm.read_ready(rows)
            else:
                X, y, val, bad = vectors_from_locations(rows, prog)
            model = mm.train_vectors(X, y, val, name, any("AUGMENTED" in str(r.get("data_source", "")).upper() for r in rows[:50]), bad, prog)
            model["trained_in_app"] = True
            mm.save_user_model(model)
            me = model["metrics"]
            TRAIN["text"] = (f"Trained from {name}: {me['rows']:,} usable rows ({bad} skipped). On the 20% kept aside: R-squared {me['r2_money']:.2f}, "
                             f"average error {me['mae']:,.0f} per asset-month, predicted total / actual total {me['total_predicted_over_total_actual']:.2f}.")
            TRAIN["status"] = "Model trained and saved."
        except Exception as e:      # noqa: BLE001
            TRAIN["error"] = str(e) if isinstance(e, ValueError) else scrub(e)
        finally:
            TRAIN["busy"] = False
            try:
                path.unlink()
            except OSError:
                pass
    threading.Thread(target=work, daemon=True).start()
    return {"started": True}


def model_state() -> dict:
    return {"busy": TRAIN["busy"], "status": TRAIN["status"], "error": TRAIN["error"], "text": TRAIN["text"], "model": mm.info(),
            "ready_columns": mm.CSV_COLUMNS[1:], "location_columns": mm.LOCATION_COLUMNS, "min_rows": mm.MIN_ROWS}


def reset_model() -> dict:
    mm.reset_to_builtin()
    TRAIN.update(text="Back to the built-in model (trained on augmented data).", error=None, status="")
    return {"ok": True}


def template_csv() -> bytes:
    out = io.StringIO()
    csv.writer(out).writerows(mm.template_rows())
    return ("﻿" + out.getvalue()).encode("utf-8")


def sample_csv() -> bytes:
    return (CORE / "data" / "AUGMENTED_training_data.csv").read_bytes()
