/* RASD Climate-Resilient Asset Design Optimizer: engine.
   Plain rules, no hidden model. Every number below is an indicative rule of thumb, not a calibrated engineering result. */
(function (root) {
"use strict";

const clamp = (x, a, b) => Math.max(a, Math.min(b, x));

/* ---------- hazards ---------- */
const HAZARDS = [
  { id: "heat",  name: "Extreme heat",            maxLoss: 0.006 },
  { id: "flood", name: "Flood and heavy rain",    maxLoss: 0.020 },
  { id: "water", name: "Water stress and drought", maxLoss: 0.003 },
  { id: "wind",  name: "Strong wind and storm",   maxLoss: 0.010 },
  { id: "coast", name: "Coastal salt and sea level", maxLoss: 0.005 },
  { id: "quake", name: "Earthquake",              maxLoss: 0.012 }
];
const HZ = HAZARDS.map(h => h.id);

/* Hazard intensity, 0 (none) to 1 (extreme), from the site numbers. */
function intensity(s, stress) {
  const k = stress ? 1.2 : 1;
  const heat  = clamp((0.6 * clamp(s.d40 / 60, 0, 1) + 0.4 * clamp(s.d35 / 150, 0, 1)) * k, 0, 1);
  const rain  = clamp(s.rainMax / 150, 0, 1);
  const low   = s.elev < 8 ? clamp((8 - s.elev) / 8, 0, 1) : 0;
  const near  = s.coastKm < 20 ? 1 : 0.4;
  const flood = clamp((0.6 * rain + 0.4 * low * near) * k, 0, 1);
  const water = clamp(1 - s.annRain / 800, 0, 1);
  const wind  = clamp((s.gust - 60) / 120 * k, 0, 1);
  const coast = clamp((1 - s.coastKm / 30) * (s.elev < 15 ? 1 : 0.7) * k, 0, 1);
  const quake = clamp(s.quakes / 60, 0, 1);
  return { heat, flood, water, wind, coast, quake };
}

/* Flood depth in metres that a design flood would reach on flat ground at this site. */
function floodDepth(I) { return 2.2 * Math.pow(I.flood, 1.2); }

/* ---------- building types ---------- */
const TYPES = {
  warehouse:  { name: "Industrial warehouse", cost: 450,  contents: 600,  kwh: 20,  floors: 1, w: 60, l: 40, sens: { heat: .7, flood: 1.2, water: .3, wind: 1.2, coast: .8, quake: .8 }, cap: 60 },
  residential:{ name: "Residential building", cost: 900,  contents: 250,  kwh: 60,  floors: 5, w: 30, l: 20, sens: { heat: 1,  flood: 1,   water: .8, wind: 1,   coast: 1,  quake: 1 },  cap: 55 },
  office:     { name: "Commercial office",    cost: 1300, contents: 300,  kwh: 90,  floors: 8, w: 36, l: 28, sens: { heat: 1,  flood: .9,  water: .7, wind: .8,  coast: .8, quake: .9 }, cap: 55 },
  hospital:   { name: "Hospital",             cost: 2600, contents: 1500, kwh: 200, floors: 4, w: 50, l: 30, sens: { heat: 1.4,flood: 1.6, water: 1.4,wind: 1,   coast: 1,  quake: 1.5 },cap: 30 },
  factory:    { name: "Factory",              cost: 800,  contents: 1200, kwh: 70,  floors: 2, w: 60, l: 40, sens: { heat: 1.1,flood: 1.2, water: 1.2,wind: .9,  coast: 1,  quake: 1 },  cap: 50 },
  datacentre: { name: "Data centre",          cost: 3000, contents: 5000, kwh: 900, floors: 2, w: 40, l: 30, sens: { heat: 2.2,flood: 1.8, water: 1.5,wind: .8,  coast: 1.3,quake: 1.2 },cap: 35 }
};

/* ---------- design parameters ----------
   Each choice has: cost = extra US dollars per square metre of floor area, and m = multiplier on the loss of a hazard
   (below 1 helps, above 1 hurts). */
const PARAMS = [
  { id: "foundation", label: "Foundation", opts: [
      { v: "shallow", t: "Shallow footing",  cost: 0,  m: {} },
      { v: "deep",    t: "Deep foundation",  cost: 25, m: { quake: .85, flood: .95 } },
      { v: "pile",    t: "Pile foundation",  cost: 55, m: { quake: .75, flood: .8, coast: .95 } } ] },
  { id: "structure", label: "Structure", opts: [
      { v: "rc",    t: "Reinforced concrete", cost: 0,  m: { coast: 1.1, heat: .95 } },
      { v: "steel", t: "Steel frame",         cost: 30, m: { quake: .9, wind: .95, coast: 1.3, heat: 1.05 } } ] },
  { id: "walls", label: "Walls", opts: [
      { v: "masonry",  t: "Masonry",            cost: 0,  m: { quake: 1.1 } },
      { v: "concrete", t: "Concrete",           cost: 30, m: { heat: .95, wind: .85, quake: .9, flood: .85 } },
      { v: "panels",   t: "Insulated panels",   cost: 20, m: { heat: .8, wind: 1.1, quake: .95, flood: 1.15 } } ] },
  { id: "roof", label: "Roof", opts: [
      { v: "metal",      t: "Plain metal",              cost: 0,  m: { heat: 1.15, wind: 1.1 } },
      { v: "insulated",  t: "Insulated",                cost: 25, m: { heat: .9 } },
      { v: "reflective", t: "Reflective and insulated", cost: 35, m: { heat: .8 } } ] },
  { id: "insulation", label: "Insulation (thermal resistance R)", num: true, opts: [
      { v: 1, t: "R1", cost: 0,  m: { heat: 1 } },
      { v: 3, t: "R3", cost: 14, m: { heat: .88 } },
      { v: 5, t: "R5", cost: 28, m: { heat: .78 } } ] },
  { id: "elevation", label: "Raised above ground (metres)", num: true, slider: [0, 2, 0.5], opts: [] },
  { id: "drainage", label: "Drainage", opts: [
      { v: "standard", t: "Standard",  cost: 0,  m: {} },
      { v: "enhanced", t: "Enhanced drainage and storage", cost: 18, m: { flood: .8 } } ] },
  { id: "equipment", label: "Critical equipment", opts: [
      { v: "ground", t: "At ground level", cost: 0,  m: {} },
      { v: "raised", t: "Raised to upper level", cost: 10, m: { flood: .7 } } ] },
  { id: "water", label: "Water supply", opts: [
      { v: "standard", t: "Mains only", cost: 0,  m: {} },
      { v: "storage",  t: "Storage tank and recycling", cost: 12, m: { water: .7 } } ] },
  { id: "cooling", label: "Cooling", opts: [
      { v: "conventional", t: "Conventional", cost: 0,  m: {} },
      { v: "efficient",    t: "High efficiency", cost: 40, m: { heat: .8 } } ] },
  { id: "shading", label: "Shading", opts: [
      { v: "none",    t: "None", cost: 0,  m: {} },
      { v: "louvers", t: "Louvers and overhangs", cost: 22, m: { heat: .88 } } ] },
  { id: "windties", label: "Wind connections", opts: [
      { v: "standard", t: "Standard", cost: 0,  m: {} },
      { v: "ties",     t: "Storm ties and bracing", cost: 18, m: { wind: .7 } } ] },
  { id: "coating", label: "Corrosion protection", opts: [
      { v: "standard",  t: "Standard", cost: 0,  m: {} },
      { v: "resistant", t: "Coated steel, stainless fixings", cost: 28, m: { coast: .55 } } ] },
  { id: "seismic", label: "Earthquake detailing", opts: [
      { v: "standard", t: "Standard", cost: 0,   m: {} },
      { v: "ductile",  t: "Ductile detailing", cost: 30,  m: { quake: .75 } },
      { v: "isolated", t: "Base isolation",    cost: 120, m: { quake: .45 } } ] }
];
const BY_ID = {}; PARAMS.forEach(p => BY_ID[p.id] = p);

const BASELINE = { floors: null, w: null, l: null, foundation: "shallow", structure: "rc", walls: "masonry", roof: "metal", insulation: 1,
  elevation: 0, drainage: "standard", equipment: "ground", water: "standard", cooling: "conventional", shading: "none",
  windties: "standard", coating: "standard", seismic: "standard" };

const SCENARIOS = [
  { id: "now",    name: "Today" },
  { id: "mid",    name: "2050 projected" },
  { id: "stress", name: "2050 stress test" }
];

const YEARS = 30, RATE = 0.04;
const ANNUITY = (1 - Math.pow(1 + RATE, -YEARS)) / RATE;
const KWH_PRICE = 0.10;

function optOf(pid, value) { return BY_ID[pid].opts.find(o => o.v === value) || BY_ID[pid].opts[0]; }

/* ---------- evaluate one design ---------- */
function evaluate(site, typeId, d, scenario) {
  const T = TYPES[typeId];
  const I = scenario === "now" ? intensity(site.now, false) : intensity(site.fut, scenario === "stress");
  const area = d.w * d.l * d.floors;                 // total floor area
  const value = area * (T.cost + T.contents);        // value exposed to loss
  const fd = floodDepth(I);

  // extra cost
  let adapt = 0;
  for (const p of PARAMS) { if (p.id === "elevation") continue; adapt += optOf(p.id, d[p.id]).cost * area; }
  adapt += 45 * d.elevation * area;                  // raising the building
  const build = area * T.cost;

  // loss per hazard
  const mod = {}; HZ.forEach(h => mod[h] = 1);
  for (const p of PARAMS) { if (p.id === "elevation") continue; const m = optOf(p.id, d[p.id]).m; for (const h in m) mod[h] *= m[h]; }
  const tall = d.floors - 1;
  mod.wind *= 1 + 0.04 * tall; mod.quake *= 1 + 0.05 * tall; mod.quake *= 1 + 0.04 * d.elevation;
  // flood: raising the building lowers the depth that gets in; a small remainder stays (access, drains, backwater)
  const floodShare = fd > 0.01 ? Math.max(0.1, (fd - d.elevation) / fd) : 0.1;
  mod.flood *= Math.min(1, floodShare);

  const idx = {}, ratio = {}; let total = 0;
  for (const h of HAZARDS) {
    const r = h.maxLoss * Math.pow(I[h.id], 1.6) * T.sens[h.id] * mod[h.id];
    ratio[h.id] = r; total += r;
    idx[h.id] = clamp(r / 0.004 * 100, 0, 100);
  }
  total = Math.min(total, 0.5);
  const eal = total * value;

  // cooling energy over the year
  const heatMod = mod.heat;                           // all heat-related choices
  const kwh = area * T.kwh * (0.3 + 1.5 * I.heat) * heatMod * (d.cooling === "efficient" ? 0.75 : 1);
  const energy = kwh * KWH_PRICE;

  return { I, area, value, floodDepth: fd, build, adapt, eal, ealRatio: total, idx, ratio, energy,
           pvLoss: eal * ANNUITY, pvEnergy: energy * ANNUITY,
           total: build + adapt + eal * ANNUITY + energy * ANNUITY };
}

/* the worst hazard index, and how far the design is over the safety cap */
function violation(res, typeId) {
  const cap = TYPES[typeId].cap; let v = 0;
  for (const h of HZ) v += Math.max(0, res.idx[h] - cap);
  return v;
}

/* ---------- optimizer ---------- */
const OPT_SETS = {
  foundation: ["shallow", "deep", "pile"], structure: ["rc", "steel"], walls: ["masonry", "concrete", "panels"],
  roof: ["metal", "insulated", "reflective"], insulation: [1, 3, 5], elevation: [0, 0.5, 1, 1.5, 2],
  drainage: ["standard", "enhanced"], equipment: ["ground", "raised"], water: ["standard", "storage"],
  cooling: ["conventional", "efficient"], shading: ["none", "louvers"], windties: ["standard", "ties"],
  coating: ["standard", "resistant"], seismic: ["standard", "ductile", "isolated"]
};
function score(site, typeId, d, sc) {
  const r = evaluate(site, typeId, d, sc);
  return { r, s: r.total + 1e6 * violation(r, typeId) };
}
/* Coordinate descent: change one choice at a time while the total cost (build + extras + discounted losses + cooling energy)
   keeps falling, with a heavy penalty for breaking the safety cap. Keeps the user's size (floors, width, length). */
function optimize(site, typeId, d0, sc) {
  let best = Object.assign({}, d0, BASELINE, { floors: d0.floors, w: d0.w, l: d0.l });
  let cur = score(site, typeId, best, sc);
  for (let pass = 0; pass < 12; pass++) {
    let moved = false;
    for (const pid in OPT_SETS) {
      for (const v of OPT_SETS[pid]) {
        if (best[pid] === v) continue;
        const t = Object.assign({}, best, { [pid]: v });
        const s = score(site, typeId, t, sc);
        if (s.s < cur.s - 1e-6) { best = t; cur = s; moved = true; }
      }
    }
    if (!moved) break;
  }
  return best;
}
/* Exhaustive search, used only in tests to check the descent. */
function bruteForce(site, typeId, d0, sc) {
  const keys = Object.keys(OPT_SETS); let best = null, bs = Infinity;
  const cur = Object.assign({}, d0, BASELINE, { floors: d0.floors, w: d0.w, l: d0.l });
  (function rec(i) {
    if (i === keys.length) { const s = score(site, typeId, cur, sc).s; if (s < bs) { bs = s; best = Object.assign({}, cur); } return; }
    for (const v of OPT_SETS[keys[i]]) { cur[keys[i]] = v; rec(i + 1); }
  })(0);
  return { d: best, s: bs };
}

/* ---------- reading, text ---------- */
function level(idx) { return idx < 10 ? "Low" : idx < 30 ? "Moderate" : idx < 60 ? "High" : "Very high"; }
function intensityLevel(x) { return x < 0.15 ? "Low" : x < 0.35 ? "Moderate" : x < 0.6 ? "High" : "Very high"; }
function grade(ealRatio, worst) {
  const g = ealRatio < 0.001 ? "A" : ealRatio < 0.003 ? "B" : ealRatio < 0.007 ? "C" : ealRatio < 0.015 ? "D" : "E";
  return g;
}
function lenderText(g, typeId, res) {
  const cap = TYPES[typeId].cap, over = HZ.filter(h => res.idx[h] > cap).map(h => HAZARDS.find(x => x.id === h).name.toLowerCase());
  if (g === "A" || g === "B") return over.length ? "Acceptable overall, but check: " + over.join(", ") + "." : "Acceptable for financing and cover on the terms of a normal asset of this kind.";
  if (g === "C") return "Finance or insure with conditions: confirm the adaptations are built and inspected." + (over.length ? " Still above the safety cap for " + over.join(", ") + "." : "");
  return "Not ready. Redesign first" + (over.length ? " (above the safety cap for " + over.join(", ") + ")" : "") + ", or expect high premiums and low loan-to-value.";
}

/* Plain advice from the site hazards and the current design (the "why" for each recommendation). */
function advice(site, typeId, d, scenario) {
  const r = evaluate(site, typeId, d, scenario), I = r.I, out = [];
  const add = (hz, text, pid, v) => out.push({ hz, text, pid, v, done: d[pid] === v });
  if (I.flood > .2) {
    add("flood", "Flood risk: raise the building above the expected flood depth of about " + r.floodDepth.toFixed(1) + " m.", "elevation", Math.min(2, Math.ceil(r.floodDepth * 2) / 2));
    add("flood", "Flood risk: raise electrical and critical equipment to an upper level.", "equipment", "raised");
    add("flood", "Heavy rain: add enhanced drainage and storage.", "drainage", "enhanced");
  }
  if (I.heat > .25) {
    add("heat", "Extreme heat: use a reflective, insulated roof.", "roof", "reflective");
    add("heat", "Extreme heat: add insulation of at least R3.", "insulation", 3);
    add("heat", "Extreme heat: add louvers or overhangs for shade.", "shading", "louvers");
    add("heat", "Extreme heat: use high efficiency cooling.", "cooling", "efficient");
  }
  if (I.wind > .25) add("wind", "Strong wind: add storm ties and bracing at the roof and wall connections.", "windties", "ties");
  if (I.coast > .3) add("coast", "Coastal exposure: use corrosion-resistant steel and fixings.", "coating", "resistant");
  if (I.water > .5) add("water", "Water stress: add a storage tank and water recycling.", "water", "storage");
  if (I.quake > .2) add("quake", "Earthquake zone: use ductile detailing" + (TYPES[typeId].sens.quake > 1.3 ? " or base isolation for this building type." : "."), "seismic", I.quake > .5 || TYPES[typeId].sens.quake > 1.3 ? "isolated" : "ductile");
  return out;
}

/* ---------- site numbers ---------- */
/* Turn daily climate rows into the numbers used above. rows = [{date, tmax, rain, wind}] */
function summarise(rows, y0, y1) {
  const yrs = {};
  for (const r of rows) {
    const y = +String(r.date).slice(0, 4); if (y < y0 || y > y1) continue;
    const o = yrs[y] || (yrs[y] = { d40: 0, d35: 0, rainMax: 0, rain: 0, wind: 0, n: 0 });
    if (r.tmax != null) { if (r.tmax >= 40) o.d40++; if (r.tmax >= 35) o.d35++; }
    if (r.rain != null) { o.rain += r.rain; if (r.rain > o.rainMax) o.rainMax = r.rain; }
    if (r.wind != null && r.wind > o.wind) o.wind = r.wind;
    o.n++;
  }
  const ys = Object.values(yrs).filter(o => o.n > 300);
  if (!ys.length) return null;
  const mean = k => ys.reduce((a, o) => a + o[k], 0) / ys.length;
  return { d40: mean("d40"), d35: mean("d35"), rainMax: mean("rainMax"), annRain: mean("rain"), gust: mean("wind") * 1.5 };
}

const api = { HAZARDS, HZ, TYPES, PARAMS, BY_ID, BASELINE, SCENARIOS, OPT_SETS, YEARS, ANNUITY, intensity, floodDepth, evaluate, violation,
  optimize, bruteForce, level, intensityLevel, grade, lenderText, advice, summarise, optOf, clamp };
if (typeof module !== "undefined" && module.exports) module.exports = api; else root.RASD = api;
})(typeof window !== "undefined" ? window : globalThis);
