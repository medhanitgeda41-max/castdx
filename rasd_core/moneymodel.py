"""Damage recovery money model: how much money it takes to repair an asset, month by month, if it stood at a place.

An asset is described by what it is made of (percent steel, concrete, masonry, timber, other), its value, and how far it is protected
against flood, heat and earthquakes (percent). The place gives the chance of each hazard in each month, how big its events usually are
(severity) and the ground factors (see prob_data.Engine.probs).

Model: gradient-boosted regression trees (pure Python, no numpy or scikit-learn) that predict the SHARE of the asset value lost in a month.
They are trained on an AUGMENTED dataset: many made-up asset-months whose damage comes from the RASD loss rules (risk_rules.py, damage.py)
plus random noise. HONEST LIMIT: it learns those rules back. It has not seen real repair bills. Retrain it with your own records.
"""
from __future__ import annotations

import bisect
import csv
import json
import math
import random
import struct
from pathlib import Path

import damage
import risk_rules as rr

MATERIALS = ["steel", "concrete", "masonry", "timber", "other"]
MAT_NAMES = {"steel": "Steel", "concrete": "Concrete", "masonry": "Masonry or brick", "timber": "Timber or wood", "other": "Other (prefab, tent, ...)"}
FLOOD_F = {"steel": 1.0, "concrete": 0.7, "masonry": 0.8, "timber": 1.3, "other": 1.3}      # from risk_rules.FLOOD_FACTOR (heat uses the same factor)
QUAKE_F = {"steel": 0.7, "concrete": 1.0, "masonry": 1.3, "timber": 0.8, "other": 0.9}      # from risk_rules.QUAKE_FACTOR
QUAKE_CUT = 0.5               # ASSUMPTION: 100% earthquake protection halves the earthquake damage (the old RASD rules had no earthquake cut)
HAZ = ("flood", "heat", "quake")
FEATURES = (["pct_" + m for m in MATERIALS] + ["asset_value"] +
            [f"{h}_{k}" for h in HAZ for k in ("probability", "severity", "factor", "material_factor", "protection_left", "exposure_score")] +
            ["flood_adaptation", "heat_adaptation", "quake_adaptation"])
CUTS = {"flood": rr.ADAPTATION_CUTS_FLOOD, "heat": rr.ADAPTATION_CUTS_HEAT, "quake": QUAKE_CUT}
VALUE_INDEX = len(MATERIALS)
PROB_INDEXES = [FEATURES.index(f"{h}_{k}") for h in HAZ for k in ("probability", "exposure_score")]
DATA_DIR = Path(__file__).resolve().parent / "data"
MIN_ROWS = 200
MAX_TRAIN_ROWS = 12000
N_TREES, LR, DEPTH, MIN_LEAF, BINS = 400, 0.05, 5, 10, 32
_MODEL = None


def _f32(x: float) -> float:
    return struct.unpack("f", struct.pack("f", float(x)))[0]


def normalise_mix(mix: dict) -> dict:
    """{steel, concrete, masonry, timber, other} in percent -> fractions that add up to 1."""
    vals = {m: max(0.0, float(mix.get(m) or 0.0)) for m in MATERIALS}
    tot = sum(vals.values())
    if tot <= 0:
        raise ValueError("Enter what the asset is made of: the percentages must add up to more than 0.")
    return {m: v / tot for m, v in vals.items()}


def factors_of(mix: dict) -> tuple:
    return sum(mix[m] * FLOOD_F[m] for m in MATERIALS), sum(mix[m] * QUAKE_F[m] for m in MATERIALS)


def rule_parts(mix, p, sev, geo, adapt) -> dict:
    """THE RULES: share of value lost in a month per hazard = chance x loss-if-it-happens. mix: fractions; p/sev/geo/adapt: dicts by hazard."""
    ff, qf = factors_of(mix)
    loss = {"flood": geo["flood"] * ff * rr.MAX_FLOOD * sev["flood"] ** rr.FLOOD_EXPONENT * (1 - rr.ADAPTATION_CUTS_FLOOD * adapt["flood"]),
            "heat": geo["heat"] * ff * rr.MAX_HEAT * sev["heat"] ** rr.HEAT_EXPONENT * (1 - rr.ADAPTATION_CUTS_HEAT * adapt["heat"]),
            "quake": geo["quake"] * qf * rr.MAX_QUAKE * sev["quake"] ** rr.QUAKE_EXPONENT * (1 - QUAKE_CUT * adapt["quake"])}
    return {h: (p[h] or 0.0) * loss[h] for h in HAZ}


def vector(mix, value, p, sev, geo, adapt) -> list:
    v = [mix[m] * 100.0 for m in MATERIALS] + [float(value)]
    ff, qf = factors_of(mix)
    for h in HAZ:
        mat = qf if h == "quake" else ff
        left = 1.0 - CUTS[h] * adapt[h]                       # how much of the damage is NOT stopped by the protection
        v += [p[h], sev[h], geo[h], mat, left, p[h] * sev[h] * geo[h] * mat * left]
    return v + [adapt["flood"], adapt["heat"], adapt["quake"]]


# ------------------------------------------------------------------ the augmented dataset
CSV_COLUMNS = (["data_source"] + ["steel_pct", "concrete_pct", "masonry_pct", "timber_pct", "other_pct", "asset_value"] +
               [f"{h}_{k}" for h in ("flood", "heat", "earthquake") for k in ("probability", "severity", "factor")] +
               ["flood_adaptation_pct", "heat_adaptation_pct", "earthquake_adaptation_pct", "damage_recovery_money"])


def augmented_rows(n: int = 9000, seed: int = 2027) -> list:
    """Made-up asset-months. Materials: 1 to 3 materials with random percentages. Protection: 0 to 100 percent. Place values follow the ranges
    the app really produces (event sizes, chances, ground factors). Damage = the RASD rules x random noise. NOT REAL DATA."""
    rnd = random.Random(seed)
    rows = []
    for i in range(n):
        k = rnd.choice([1, 1, 2, 2, 3])
        picks = rnd.sample(MATERIALS, k)
        w = [rnd.random() + 0.15 for _ in picks]
        mix = {m: 0.0 for m in MATERIALS}
        for m, x in zip(picks, w):
            mix[m] = round(x / sum(w) * 20) * 5.0            # steps of 5 percent
        if sum(mix.values()) == 0:
            mix[picks[0]] = 100.0
        mix = normalise_mix(mix)
        value = round(rnd.choice([5e5, 1e6, 2e6, 4e6, 8e6]) * rnd.uniform(0.6, 1.6), -3)
        p = {"flood": rnd.betavariate(1, 8) * (rnd.random() > 0.3), "heat": rnd.betavariate(1, 3) * rnd.choice([0, 0.2, 1]),
             "quake": rnd.uniform(0.0005, 0.10) * (rnd.random() > 0.3)}
        geo = {"flood": rnd.uniform(0.5, 1.5), "heat": rnd.uniform(0.8, 1.2), "quake": rnd.uniform(0.5, 1.5)}
        rain = min(120.0, 20.0 + rnd.lognormvariate(2.7, 0.7))
        temp = min(53.0, 40.0 + rnd.expovariate(1 / 2.2))
        mag = min(7.5, 6.0 + rnd.expovariate(1 / 0.35))
        sev = {"flood": damage._unit(rain, *damage.RAIN_RANGE), "heat": damage._unit(temp, *damage.HEAT_RANGE), "quake": damage._unit(mag, *damage.QUAKE_RANGE)}
        ad = {h: rnd.choice([0.0, 0.0, 0.25, 0.5, 0.75, 1.0, rnd.random()]) for h in HAZ}
        share = min(rr.MAX_SHARE, sum(rule_parts(mix, p, sev, geo, ad).values()))
        money = round(value * share * rnd.lognormvariate(0, 0.3))
        rows.append(["AUGMENTED (made up from the RASD rules)"] + [round(mix[m] * 100, 1) for m in MATERIALS] + [value] +
                    [round(x, 5) for h in HAZ for x in (p[h], sev[h], geo[h])] + [round(ad[h] * 100, 1) for h in HAZ] + [money])
    return rows


def write_augmented(path, n: int = 9000):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        w.writerows(augmented_rows(n))


# ------------------------------------------------------------------ reading a training file
def _key(h: str) -> str:
    k = h.strip().lower().replace(" ", "_").replace("-", "_").lstrip("﻿")
    return {"damage": "damage_recovery_money", "asset_valuation": "asset_value", "earthquake_adaptation": "earthquake_adaptation_pct"}.get(k, k)


LOCATION_COLUMNS = ["latitude", "longitude", "month", "year", "steel_pct", "concrete_pct", "masonry_pct", "timber_pct", "other_pct", "asset_value",
                    "flood_adaptation_pct", "heat_adaptation_pct", "earthquake_adaptation_pct", "damage_recovery_money"]


def template_rows() -> list:
    return [LOCATION_COLUMNS, [25.7617, -80.1918, 9, 2022, 60, 40, 0, 0, 0, 4000000, 80, 20, 50, 85000],
            [51.9244, 4.4777, 3, 2023, 0, 30, 50, 20, 0, 1500000, 0, 40, 0, 9000]]


def read_dicts(path: str) -> list:
    with open(path, newline="", encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        if not rd.fieldnames:
            raise ValueError("The file is empty.")
        return [{_key(k): v for k, v in r.items() if k} for r in rd]


def _f(x):
    return float(str(x).replace(",", "").replace("%", "").strip())


def read_ready(rows: list) -> tuple:
    """A file that already has the chances (like the augmented dataset)."""
    X, y, val, bad = [], [], [], 0
    for r in rows:
        try:
            mix = normalise_mix({m: _f(r[m + "_pct"]) for m in MATERIALS})
            value = _f(r["asset_value"])
            money = _f(r["damage_recovery_money"])
            if value <= 0 or money < 0:
                raise ValueError
            p = {h: _f(r[n + "_probability"]) for h, n in zip(HAZ, ("flood", "heat", "earthquake"))}
            sv = {h: _f(r[n + "_severity"]) for h, n in zip(HAZ, ("flood", "heat", "earthquake"))}
            gf = {h: _f(r[n + "_factor"]) for h, n in zip(HAZ, ("flood", "heat", "earthquake"))}
            ad = {h: max(0.0, min(1.0, _f(r[n + "_adaptation_pct"]) / 100.0)) for h, n in zip(HAZ, ("flood", "heat", "earthquake"))}
            X.append(vector(mix, value, p, sv, gf, ad)); y.append(money); val.append(value)
        except (ValueError, KeyError, TypeError):
            bad += 1
    return X, y, val, bad


# ------------------------------------------------------------------ predicting
def _share(vec, model) -> float:
    total, lr = model["init"], model["learning_rate"]
    v = [_f32(x) for x in vec]
    for t in model["trees"]:
        i = 0
        left, right, feat, thr = t["left"], t["right"], t["feature"], t["threshold"]
        while left[i] != -1:
            i = left[i] if v[feat[i]] <= thr[i] else right[i]
        total += lr * t["value"][i]
    return total


def share_of_value(vec, model: dict) -> float:
    """Share of the value lost in the month. What the trees give when every chance is zero is taken off (no hazard, no damage)."""
    zero = list(vec)
    for i in PROB_INDEXES:
        zero[i] = 0.0
    return max(0.0, _share(vec, model) - _share(zero, model)) * model.get("scale", 1.0)


def user_model_path() -> Path:
    return Path.home() / ".rasd_prob_cache" / "money_model.json"


def load_model(force: bool = False):
    global _MODEL
    if _MODEL is None or force:
        _MODEL = {}
        for path in (user_model_path(), DATA_DIR / "money_model.json"):
            try:
                if path.exists():
                    _MODEL = json.loads(path.read_text(encoding="utf-8"))
                    if _MODEL.get("features") == FEATURES:
                        break
                    _MODEL = {}
            except (OSError, ValueError):
                _MODEL = {}
    return _MODEL or None


def save_user_model(model: dict):
    global _MODEL
    p = user_model_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(model, separators=(",", ":")), encoding="utf-8")
    _MODEL = model


def reset_to_builtin():
    try:
        user_model_path().unlink()
    except OSError:
        pass
    load_model(force=True)


def info() -> str:
    m = load_model()
    if not m:
        return "No damage recovery money model found (data/money_model.json)."
    me = m["metrics"]
    src = "AUGMENTED data made from the RASD rules, not real repair bills" if m.get("training_is_synthetic") else "records supplied by you"
    return (f"Damage recovery money model ({'retrained in this app' if m.get('trained_in_app') else 'built-in'}): {len(m['trees'])} boosted trees, trained on "
            f"{me['rows']:,} rows of {m['training_file']} ({src}). On the 20% kept aside: R-squared {me['r2_money']:.2f}, average error {me['mae']:,.0f} per asset-month.")


# ------------------------------------------------------------------ training (pure Python)
def _fit_tree(B, cuts, resid, idx, active):
    left, right, feat, thr, value = [], [], [], [], []

    def leaf(ix):
        value.append(sum(resid[i] for i in ix) / len(ix) if ix else 0.0)
        left.append(-1); right.append(-1); feat.append(-2); thr.append(-2.0)
        return len(value) - 1

    def build(ix, depth):
        node, n = len(value), len(ix)
        if depth >= DEPTH or n < 2 * MIN_LEAF:
            return leaf(ix)
        tot = sum(resid[i] for i in ix)
        best = (1e-12, None, None)
        for f in active:
            col, nb = B[f], len(cuts[f]) + 1
            s, c = [0.0] * nb, [0] * nb
            for i in ix:
                b = col[i]
                s[b] += resid[i]; c[b] += 1
            sl = cl = 0.0
            for b in range(nb - 1):
                sl += s[b]; cl += c[b]
                cr = n - cl
                if cl < MIN_LEAF or cr < MIN_LEAF:
                    continue
                g = sl * sl / cl + (tot - sl) ** 2 / cr - tot * tot / n
                if g > best[0]:
                    best = (g, f, b)
        if best[1] is None:
            return leaf(ix)
        _, f, b = best
        value.append(tot / n)
        left.append(0); right.append(0); feat.append(f); thr.append(_f32(cuts[f][b]))
        col = B[f]
        left[node] = build([i for i in ix if col[i] <= b], depth + 1)
        right[node] = build([i for i in ix if col[i] > b], depth + 1)
        return node

    build(idx, 0)
    return {"left": left, "right": right, "feature": feat, "threshold": thr, "value": value}


def train_vectors(X, y, val, name: str, synthetic: bool, skipped: int = 0, progress=None) -> dict:
    if len(X) < MIN_ROWS:
        raise ValueError(f"Only {len(X)} usable rows (at least {MIN_ROWS} are needed).")
    rnd = random.Random(7)
    order = list(range(len(X)))
    rnd.shuffle(order)
    ntest = max(1, len(order) // 5)
    test, train_ix = order[:ntest], order[ntest:]
    train_ix = train_ix[:MAX_TRAIN_ROWS]
    nf = len(FEATURES)
    cuts, B = [], []
    for f in range(nf):
        vals = sorted({X[i][f] for i in train_ix})
        cut = vals[:-1] if len(vals) <= BINS else sorted({vals[int(len(vals) * k / BINS)] for k in range(1, BINS)})
        cuts.append(cut)
        B.append([bisect.bisect_left(cut, X[i][f]) for i in range(len(X))])
    active = [f for f in range(nf) if cuts[f]]
    target = [y[i] / val[i] for i in range(len(X))]
    mean = sum(target[i] for i in train_ix) / len(train_ix)
    pred = [mean] * len(X)
    trees = []
    for t in range(N_TREES):
        sub = [i for i in train_ix if rnd.random() < 0.8]
        resid = {i: target[i] - pred[i] for i in sub}
        tree = _fit_tree(B, cuts, resid, sub, active)
        trees.append(tree)
        for i in train_ix:
            node = 0
            while tree["left"][node] != -1:
                node = tree["left"][node] if _f32(X[i][tree["feature"][node]]) <= tree["threshold"][node] else tree["right"][node]
            pred[i] += LR * tree["value"][node]
        if progress and t % 10 == 9:
            progress(f"Retraining the model: tree {t + 1} of {N_TREES} ...")
    model = {"kind": "gradient_boosted_trees_share_of_value", "features": FEATURES, "init": mean, "learning_rate": LR, "trees": trees, "scale": 1.0}
    got = [share_of_value(X[i], model) * val[i] for i in train_ix]
    model["scale"] = (sum(y[i] for i in train_ix) / sum(got)) if sum(got) > 0 else 1.0
    pt = [share_of_value(X[i], model) * val[i] for i in test]
    yt = [y[i] for i in test]
    my = sum(yt) / len(yt)
    sst = sum((v - my) ** 2 for v in yt)
    sse = sum((a - b) ** 2 for a, b in zip(yt, pt))
    model.update(training_file=name, training_is_synthetic=synthetic, skipped_rows=skipped,
                 metrics={"rows": len(X), "test_rows": len(test), "r2_money": (1 - sse / sst) if sst > 0 else 0.0,
                          "mae": sum(abs(a - b) for a, b in zip(yt, pt)) / len(yt), "median_money": sorted(yt)[len(yt) // 2],
                          "total_predicted_over_total_actual": (sum(pt) / sum(yt)) if sum(yt) > 0 else 0.0})
    return model
