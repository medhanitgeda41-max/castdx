(function () {
"use strict";
const E = window.RASD, P = window.PRESETS;
const $ = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* ---------------- state ---------------- */
const S = {
  place: null,            // {name, lat, lon}
  shared: { elev: 5, coastKm: 10, quakes: 5 },
  now: { d40: 0, d35: 0, rainMax: 50, annRain: 800, gust: 90 },
  fut: { d40: 0, d35: 0, rainMax: 50, annRain: 800, gust: 90 },
  status: [],
  type: "warehouse",
  d: null,
  scenario: "mid",
  showFlood: true
};
const site = () => ({ now: Object.assign({}, S.shared, S.now), fut: Object.assign({}, S.shared, S.fut) });
const fmtMoney = x => { const a = Math.abs(x); return (x < 0 ? "-" : "") + "$" + (a >= 1e9 ? (a / 1e9).toFixed(2) + " B" : a >= 1e6 ? (a / 1e6).toFixed(a >= 1e7 ? 1 : 2) + " M" : a >= 1e3 ? Math.round(a / 1e3) + " K" : Math.round(a)); };
const pct = (x, n) => (x * 100).toFixed(n == null ? 2 : n) + "%";
const LV = { "Low": "lv0", "Moderate": "lv1", "High": "lv2", "Very high": "lv3" };

/* ---------------- screen 1: place ---------------- */
let map = null, marker = null, picked = null, mapOK = false;
function initMap() {
  try {
    if (!window.L) throw new Error("no map library");
    map = L.map("pmap", { zoomControl: false, worldCopyJump: true }).setView([25.2, 55.27], 4);
    const layer = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, attribution: "© OpenStreetMap contributors" }).addTo(map);
    let bad = 0;
    L.control.zoom({ position: "bottomright" }).addTo(map);
    layer.on("tileerror", () => { if (++bad === 4) $("#mapnote").textContent = "The map pictures could not load here. You can still search for a place, use a quick pick, or type latitude and longitude."; });
    map.on("click", e => pick(e.latlng.lat, e.latlng.lng, "Chosen point (" + e.latlng.lat.toFixed(3) + ", " + e.latlng.lng.toFixed(3) + ")"));
    mapOK = true;
  } catch (e) { $("#mapnote").textContent = "The map could not start. Search for a place, use a quick pick, or type latitude and longitude."; }
}
function pick(lat, lon, name, fly) {
  picked = { name, lat: +lat, lon: +lon };
  if (mapOK) {
    if (marker) marker.setLatLng([lat, lon]); else marker = L.circleMarker([lat, lon], { radius: 9, color: "#0f2230", weight: 3, fillColor: "#d14b2f", fillOpacity: 1 }).addTo(map);
    if (fly) map.setView([lat, lon], Math.max(map.getZoom(), 8));
  }
  $("#pchosen").innerHTML = "<b>" + esc(name) + "</b><br><span class='mono'>" + (+lat).toFixed(4) + ", " + (+lon).toFixed(4) + "</span>";
  $("#plat").value = (+lat).toFixed(4); $("#plon").value = (+lon).toFixed(4);
  $("#pgo").disabled = false;
}
function initPlace() {
  initMap();
  const chips = $("#chips");
  P.forEach(p => { const b = document.createElement("button"); b.type = "button"; b.className = "chip"; b.textContent = p.name; b.onclick = () => pick(p.lat, p.lon, p.name, true); chips.appendChild(b); });
  $("#psearch").onsubmit = async ev => {
    ev.preventDefault(); const q = $("#pq").value.trim(); if (!q) return;
    const ul = $("#presults"); ul.innerHTML = "<li class='muted'>Searching…</li>";
    try {
      const r = await fetchJson("https://geocoding-api.open-meteo.com/v1/search?count=6&language=en&format=json&name=" + encodeURIComponent(q), 10000);
      const rs = r.results || []; ul.innerHTML = "";
      if (!rs.length) { ul.innerHTML = "<li class='muted'>No place found. Try another spelling, or click the map.</li>"; return; }
      rs.forEach(x => { const li = document.createElement("li"); const b = document.createElement("button"); b.type = "button"; b.className = "res";
        b.textContent = [x.name, x.admin1, x.country].filter(Boolean).join(", "); b.onclick = () => { pick(x.latitude, x.longitude, b.textContent, true); ul.innerHTML = ""; }; li.appendChild(b); ul.appendChild(li); });
    } catch (e) { ul.innerHTML = "<li class='muted'>Search is not reachable from here. Use a quick pick, click the map, or type latitude and longitude.</li>"; }
  };
  const fromBoxes = () => { const la = parseFloat($("#plat").value), lo = parseFloat($("#plon").value); if (isFinite(la) && isFinite(lo) && Math.abs(la) <= 90 && Math.abs(lo) <= 180) pick(la, lo, "Typed point", true); };
  $("#plat").onchange = $("#plon").onchange = fromBoxes;
  $("#pgo").onclick = () => { if (picked) usePlace(picked); };
}

/* ---------------- live site data ---------------- */
async function fetchJson(url, ms) {
  const ctl = new AbortController(), t = setTimeout(() => ctl.abort(), ms || 15000);
  try { const r = await fetch(url, { signal: ctl.signal }); if (!r.ok) throw new Error("HTTP " + r.status); return await r.json(); } finally { clearTimeout(t); }
}
function dest(lat, lon, bearing, km) {
  const R = 6371, d = km / R, b = bearing * Math.PI / 180, p1 = lat * Math.PI / 180, l1 = lon * Math.PI / 180;
  const p2 = Math.asin(Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(b));
  const l2 = l1 + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(p1), Math.cos(d) - Math.sin(p1) * Math.sin(p2));
  return [p2 * 180 / Math.PI, ((l2 * 180 / Math.PI + 540) % 360) - 180];
}
function kmBetween(a, b, c, d) { const R = 6371, t = x => x * Math.PI / 180, dl = t(d - b), dp = t(c - a), h = Math.sin(dp / 2) ** 2 + Math.cos(t(a)) * Math.cos(t(c)) * Math.sin(dl / 2) ** 2; return 2 * R * Math.asin(Math.sqrt(h)); }

async function loadSite(pl) {
  const { lat, lon } = pl, st = [];
  const near = P.map(p => ({ p, km: kmBetween(lat, lon, p.lat, p.lon) })).sort((a, b) => a.km - b.km)[0];
  const preset = near.km < 40 ? near.p : null;
  // start from typical values (preset if this is a known place, else a neutral guess) so the page works even when nothing is reachable
  if (preset) { S.shared = { elev: preset.elev, coastKm: preset.coastKm, quakes: preset.quakes }; S.now = Object.assign({}, preset.now); S.fut = Object.assign({}, preset.fut); }
  else { S.shared = { elev: 20, coastKm: 20, quakes: 5 }; S.now = { d40: 2, d35: 20, rainMax: 70, annRain: 800, gust: 90 }; S.fut = { d40: 4, d35: 30, rainMax: 80, annRain: 800, gust: 92 }; }

  const tasks = [];
  // 1. elevation of the point and a ring of points, to estimate the distance to the sea
  tasks.push((async () => {
    const pts = [[lat, lon]];
    for (const km of [1, 3, 6, 12, 25, 50]) for (let b = 0; b < 360; b += 45) pts.push(dest(lat, lon, b, km));
    const r = await fetchJson("https://api.open-meteo.com/v1/elevation?latitude=" + pts.map(p => p[0].toFixed(4)).join(",") + "&longitude=" + pts.map(p => p[1].toFixed(4)).join(","));
    const el = r.elevation; if (!el || el.length !== pts.length) throw new Error("bad answer");
    S.shared.elev = Math.round(el[0] * 10) / 10;
    let best = null;
    for (let i = 1; i < pts.length; i++) if (el[i] === 0) { const km = kmBetween(lat, lon, pts[i][0], pts[i][1]); if (best == null || km < best) best = km; }
    S.shared.coastKm = el[0] === 0 ? 0 : best == null ? 60 : Math.round(best * 10) / 10;
    st.push({ k: "Ground height and distance to the sea", ok: true, note: best == null ? "no sea found within 50 km" : "estimated from a ring of heights" });
  })().catch(() => st.push({ k: "Ground height and distance to the sea", ok: false })));
  // 2. climate model: today and 2050
  tasks.push((async () => {
    const r = await fetchJson("https://climate-api.open-meteo.com/v1/climate?latitude=" + lat.toFixed(3) + "&longitude=" + lon.toFixed(3) +
      "&start_date=1995-01-01&end_date=2050-12-31&models=MRI_AGCM3_2_S&daily=temperature_2m_max,precipitation_sum,wind_speed_10m_max&timezone=GMT", 40000);
    const dd = r.daily; if (!dd || !dd.time) throw new Error("bad answer");
    const key = v => Object.keys(dd).find(k => k === v || k.indexOf(v + "_") === 0);
    const kt = key("temperature_2m_max"), kr = key("precipitation_sum"), kw = key("wind_speed_10m_max");
    const rows = dd.time.map((t, i) => ({ date: t, tmax: kt ? dd[kt][i] : null, rain: kr ? dd[kr][i] : null, wind: kw ? dd[kw][i] : null }));
    const a = E.summarise(rows, 1995, 2024), b = E.summarise(rows, 2031, 2050);
    if (!a || !b) throw new Error("not enough years");
    S.now = a; S.fut = b;
    st.push({ k: "Heat, rain and wind (climate model)", ok: true, note: "one model, 1995 to 2024 against 2031 to 2050" });
  })().catch(() => st.push({ k: "Heat, rain and wind (climate model)", ok: false })));
  // 3. earthquakes
  tasks.push((async () => {
    const r = await fetchJson("https://earthquake.usgs.gov/fdsnws/event/1/count?format=geojson&latitude=" + lat.toFixed(3) + "&longitude=" + lon.toFixed(3) + "&maxradiuskm=300&minmagnitude=5&starttime=1990-01-01");
    if (typeof r.count !== "number") throw new Error("bad answer");
    S.shared.quakes = r.count; st.push({ k: "Earthquakes (USGS, size 5 and above, 300 km, since 1990)", ok: true });
  })().catch(() => st.push({ k: "Earthquakes (USGS, size 5 and above, 300 km, since 1990)", ok: false })));
  await Promise.all(tasks);
  const order = ["Ground", "Heat", "Earthquakes"]; st.sort((x, y) => order.findIndex(o => x.k.startsWith(o)) - order.findIndex(o => y.k.startsWith(o)));
  S.status = st; S.presetName = preset ? preset.name : null;
}

/* ---------------- workspace ---------------- */
async function usePlace(pl) {
  S.place = pl;
  $("#s-place").hidden = true; $("#s-work").hidden = false; $("#steps").hidden = false; $("#scbox").hidden = false;
  $("#loading").hidden = false; $("#loadtext").textContent = "Reading the site data for " + pl.name + "…";
  await loadSite(pl);
  $("#loading").hidden = true;
  $("#placename").textContent = pl.name;
  if (!S.d) setType(S.type, true);
  buildSiteEditor(); update(true);
  View.resize(); window.scrollTo(0, 0);
}
function setStep(n) { $$("#steps button").forEach(b => b.classList.toggle("on", +b.dataset.s === n)); }
function changePlace() { $("#s-place").hidden = false; $("#s-work").hidden = true; $("#scbox").hidden = true; setStep(1); if (map) setTimeout(() => map.invalidateSize(), 50); }

function setType(t, init) {
  S.type = t; const T = E.TYPES[t];
  const keep = S.d ? S.d : E.BASELINE;
  S.d = Object.assign({}, init ? E.BASELINE : keep, { floors: T.floors, w: T.w, l: T.l });
  $$(".tile").forEach(b => b.classList.toggle("on", b.dataset.t === t));
  syncControls(); if (!init) update();
}

function buildTypeTiles() {
  const box = $("#tiles");
  Object.keys(E.TYPES).forEach(k => { const b = document.createElement("button"); b.type = "button"; b.className = "tile"; b.dataset.t = k; b.textContent = E.TYPES[k].name; b.onclick = () => setType(k); box.appendChild(b); });
  const sl = (id, label, min, max, step, unit) => { const w = document.createElement("label"); w.className = "slider"; w.innerHTML = "<span>" + label + "</span><input type='range' id='" + id + "' min='" + min + "' max='" + max + "' step='" + step + "'><output id='o_" + id + "'></output>";
    $("#sizes").appendChild(w); $("#" + id).oninput = e => { S.d[{ sz_f: "floors", sz_w: "w", sz_l: "l" }[id]] = +e.target.value; update(); }; };
  sl("sz_f", "Floors", 1, 12, 1); sl("sz_w", "Width (metres)", 10, 100, 2); sl("sz_l", "Length (metres)", 10, 100, 2);
}
function buildParams() {
  const box = $("#params");
  E.PARAMS.forEach(p => {
    const w = document.createElement("div"); w.className = "param"; w.dataset.p = p.id;
    if (p.slider) {
      w.innerHTML = "<label class='slider'><span>" + p.label + "</span><input type='range' id='pr_" + p.id + "' min='" + p.slider[0] + "' max='" + p.slider[1] + "' step='" + p.slider[2] + "'><output id='o_pr_" + p.id + "'></output></label><div class='sub' id='sub_" + p.id + "'></div>";
      box.appendChild(w); $("#pr_" + p.id).oninput = e => { S.d[p.id] = +e.target.value; update(); };
    } else {
      w.innerHTML = "<div class='plabel'>" + p.label + "</div><div class='seg' role='group'></div><div class='sub' id='sub_" + p.id + "'></div>";
      const seg = $(".seg", w);
      p.opts.forEach(o => { const b = document.createElement("button"); b.type = "button"; b.dataset.v = o.v; b.textContent = o.t; b.title = o.cost ? "Extra cost about $" + o.cost + " per square metre of floor area" : "No extra cost";
        b.onclick = () => { S.d[p.id] = o.v; update(); }; seg.appendChild(b); });
      box.appendChild(w);
    }
  });
}
function syncControls() {
  const d = S.d; if (!d) return;
  [["sz_f", d.floors], ["sz_w", d.w], ["sz_l", d.l]].forEach(([id, v]) => { $("#" + id).value = v; $("#o_" + id).textContent = v; });
  E.PARAMS.forEach(p => {
    if (p.slider) { $("#pr_" + p.id).value = d[p.id]; $("#o_pr_" + p.id).textContent = d[p.id].toFixed(1) + " m"; return; }
    $$(".param[data-p='" + p.id + "'] .seg button").forEach(b => b.classList.toggle("on", String(b.dataset.v) === String(d[p.id])));
  });
  E.PARAMS.forEach(p => {
    const el = $("#sub_" + p.id); if (!el) return;
    if (p.id === "elevation") el.textContent = d.elevation > 0 ? "Extra cost about $" + (45 * d.elevation) + " per square metre of floor area" : "";
    else { const o = E.optOf(p.id, d[p.id]); el.textContent = o.cost ? "+$" + o.cost + " per square metre" : ""; }
  });
}

/* Editable site numbers */
function buildSiteEditor() {
  const f = (k, label, unit, step) => "<label class='nf'><span>" + label + "</span><input type='number' data-k='" + k + "' step='" + step + "' value='" + S.shared[k] + "'><i>" + unit + "</i></label>";
  $("#siteedit").innerHTML = f("elev", "Ground height above sea", "m", 0.5) + f("coastKm", "Distance to the sea", "km", 1) + f("quakes", "Earthquakes size 5+ nearby", "count", 1) +
    "<p class='note'>Today and 2050 values for heat, rain and wind come from the climate model and cannot be typed here. Change the three numbers above if you know better.</p>";
  $$("#siteedit input").forEach(i => i.onchange = () => { const v = parseFloat(i.value); if (isFinite(v)) { S.shared[i.dataset.k] = v; update(); } });
  const st = $("#sitestatus"); st.innerHTML = "";
  S.status.forEach(x => { const li = document.createElement("li"); li.className = x.ok ? "ok" : "bad";
    li.textContent = (x.ok ? "Read: " : "Could not read: ") + x.k + (x.ok && x.note ? " (" + x.note + ")" : ""); st.appendChild(li); });
  const anyBad = S.status.some(x => !x.ok);
  $("#sitewarn").hidden = !anyBad;
  $("#sitewarn").textContent = anyBad ? (S.presetName ? "Some live data could not be read, so typical values for " + S.presetName + " are used for those parts." : "Some live data could not be read, so general middle values are used for those parts. They are not real for this place. Check the numbers or try again.") : "";
}

/* ---------------- calculation and drawing ---------------- */
let CACHE = null;
function compute() {
  const s = site(), t = S.type, sc = S.scenario;
  const std = Object.assign({}, E.BASELINE, { floors: S.d.floors, w: S.d.w, l: S.d.l });
  const optD = E.optimize(s, t, S.d, sc);
  const run = d => { const o = { d, by: {} }; E.SCENARIOS.forEach(x => o.by[x.id] = E.evaluate(s, t, d, x.id)); o.r = o.by[sc]; return o; };
  CACHE = { std: run(std), mine: run(S.d), opt: run(optD), s };
  return CACHE;
}

function update(first) {
  if (!S.d || !S.place) return;
  syncControls();
  const C = compute();
  renderHazards(C); renderCompare(C); renderHazardBars(C); renderAdvice(C); renderScen(C); renderLender(C);
  View.setDesign(S.d, C.mine.r, C);
  $$(".sc button").forEach(b => b.classList.toggle("on", b.dataset.sc === S.scenario));
}

function renderHazards(C) {
  const I = C.mine.r.I, r = C.mine.r;
  const detail = {
    heat: (E.SCENARIOS.find(x => x.id === S.scenario).id === "now" ? S.now : S.fut),
  };
  const cl = S.scenario === "now" ? S.now : S.fut;
  const text = {
    heat: Math.round(cl.d40) + " days a year at or above 40 °C, " + Math.round(cl.d35) + " at or above 35 °C",
    flood: "Wettest day about " + Math.round(cl.rainMax) + " mm; ground height " + S.shared.elev + " m; design flood about " + r.floodDepth.toFixed(1) + " m",
    water: "About " + Math.round(cl.annRain) + " mm of rain a year",
    wind: "Strong gusts about " + Math.round(cl.gust) + " km/h",
    coast: "Sea is about " + S.shared.coastKm + " km away",
    quake: S.shared.quakes + " earthquakes of size 5 or more within 300 km since 1990"
  };
  $("#hz").innerHTML = E.HAZARDS.map(h => {
    const lv = E.intensityLevel(I[h.id]);
    return "<div class='hzrow'><div class='hzhead'><b>" + h.name + "</b><span class='chip2 " + LV[lv] + "'>" + lv + "</span></div><div class='meter'><i class='" + LV[lv] + "' style='width:" + Math.max(3, I[h.id] * 100) + "%'></i></div><div class='hzsub'>" + text[h.id] + "</div></div>";
  }).join("");
}

function stack(o, max, label) {
  const r = o.r, seg = [["Construction", r.build, "c1"], ["Adaptation extras", r.adapt, "c2"], ["Losses over " + E.YEARS + " years", r.pvLoss, "c3"], ["Cooling energy over " + E.YEARS + " years", r.pvEnergy, "c4"]];
  return "<div class='brow'><div class='blabel'>" + label + "</div><div class='btrack'>" + seg.map(x => "<i class='" + x[2] + "' style='width:" + (x[1] / max * 100) + "%' title='" + x[0] + ": " + fmtMoney(x[1]) + "'></i>").join("") + "</div><div class='btotal'>" + fmtMoney(r.total) + "</div></div>";
}
function renderCompare(C) {
  const max = Math.max(C.std.r.total, C.mine.r.total, C.opt.r.total);
  $("#bars").innerHTML = stack(C.std, max, "Standard") + stack(C.mine, max, "Your design") + stack(C.opt, max, "Optimized") +
    "<div class='legend'><span><i class='c1'></i>Construction</span><span><i class='c2'></i>Adaptation extras</span><span><i class='c3'></i>Losses over " + E.YEARS + " years</span><span><i class='c4'></i>Cooling energy over " + E.YEARS + " years</span></div>";
  const row = (lab, f, cls) => "<tr><th>" + lab + "</th>" + [C.std, C.mine, C.opt].map(o => "<td class='" + (cls || "") + "'>" + f(o.r, o) + "</td>").join("") + "</tr>";
  const gr = r => E.grade(r.ealRatio);
  $("#cmp").innerHTML = "<thead><tr><th></th><th>Standard</th><th>Your design</th><th>Optimized</th></tr></thead><tbody>" +
    row("Construction cost", r => fmtMoney(r.build)) + row("Adaptation extras", r => fmtMoney(r.adapt)) +
    row("Expected loss each year", r => fmtMoney(r.eal) + " <small>(" + pct(r.ealRatio) + " of value)</small>") +
    row("Residual risk (worst hazard)", r => { const w = E.HZ.reduce((a, h) => r.idx[h] > r.idx[a] ? h : a, "heat"); return E.level(r.idx[w]) + " <small>" + E.HAZARDS.find(x => x.id === w).name.toLowerCase() + "</small>"; }) +
    row("Total over " + E.YEARS + " years", r => "<b>" + fmtMoney(r.total) + "</b>") + row("Resilience grade", r => "<span class='grade g" + gr(r) + "'>" + gr(r) + "</span>") + "</tbody>";
  const save = C.std.r.total - C.mine.r.total, saveO = C.std.r.total - C.opt.r.total;
  $("#cmpnote").innerHTML = "Compared with the standard design, your design " + (save >= 0 ? "saves " : "costs ") + "<b>" + fmtMoney(Math.abs(save)) + "</b> over " + E.YEARS + " years; the optimized design " + (saveO >= 0 ? "saves " : "costs ") + "<b>" + fmtMoney(Math.abs(saveO)) + "</b>.";
}
function renderHazardBars(C) {
  const cap = E.TYPES[S.type].cap;
  $("#hb").innerHTML = E.HAZARDS.map(h => {
    const a = C.std.r.idx[h.id], b = C.mine.r.idx[h.id], c = C.opt.r.idx[h.id];
    const bar = (v, cls) => "<div class='hbar'><i class='" + cls + "' style='width:" + Math.max(1, v) + "%'></i></div><span class='hv'>" + Math.round(v) + "</span>";
    return "<div class='hbrow'><div class='hbname'>" + h.name + "</div><div class='hbbars'>" + bar(a, "s0") + bar(b, "s1") + bar(c, "s2") + "</div></div>";
  }).join("") + "<div class='legend'><span><i class='s0'></i>Standard</span><span><i class='s1'></i>Your design</span><span><i class='s2'></i>Optimized</span><span class='muted'>Index 0 to 100. Safety cap for this building type: " + cap + ".</span></div>";
}
function renderAdvice(C) {
  const adv = E.advice(site(), S.type, S.d, S.scenario);
  const todo = adv.filter(a => !a.done);
  $("#advice").innerHTML = adv.length ? adv.map((a, i) => {
    const done = a.done || (a.pid === "elevation" && S.d.elevation >= a.v);
    return "<li class='" + (done ? "done" : "") + "'><span class='mk'>" + (done ? "✓" : "•") + "</span><span class='tx'>" + esc(a.text) + "</span>" + (done ? "" : "<button type='button' data-i='" + i + "'>Apply</button>") + "</li>";
  }).join("") : "<li class='done'><span class='mk'>✓</span><span class='tx'>No strong hazard found here. The standard design is a reasonable start.</span></li>";
  $$("#advice button").forEach(b => b.onclick = () => { const a = adv[+b.dataset.i]; S.d[a.pid] = a.v; update(); });
  $("#applyall").disabled = !todo.length;
}
function renderScen(C) {
  const rows = E.SCENARIOS.map(sc => "<tr><th>" + sc.name + "</th>" + [C.std, C.mine, C.opt].map(o => { const r = o.by[sc.id]; return "<td>" + pct(r.ealRatio) + " <small>" + fmtMoney(r.eal) + " a year</small></td>"; }).join("") + "</tr>").join("");
  $("#scen").innerHTML = "<thead><tr><th>Expected yearly loss</th><th>Standard</th><th>Your design</th><th>Optimized</th></tr></thead><tbody>" + rows + "</tbody>";
}
function renderLender(C) {
  const T = E.TYPES[S.type];
  const g = r => E.grade(r.ealRatio);
  const mine = C.mine.r;
  $("#lender").innerHTML = "<div class='lgrades'>" + [["Standard", C.std.r], ["Your design", mine], ["Optimized", C.opt.r]].map(x => "<div class='lg'><span class='grade big g" + g(x[1]) + "'>" + g(x[1]) + "</span><small>" + x[0] + "</small></div>").join("") + "</div>" +
    "<p>" + esc(E.lenderText(g(mine), S.type, mine)) + "</p><p class='note'>Grade A: expected yearly loss below 0.1% of the value. B: below 0.3%. C: below 0.7%. D: below 1.5%. E: above that. Safety cap for a " + T.name.toLowerCase() + ": no single hazard index above " + T.cap + ".</p>";
}

/* ---------------- 3D view ---------------- */
const View = (function () {
  let renderer, scene, camera, group, water, sky, sun, wind, canvas, raf, size = { w: 10, h: 10 };
  let th = 0.8, ph = 1.2, dist = 120, target = new THREE.Vector3(0, 10, 0), lastTouch = 0, want = null, windMeshes = [];
  const texCache = {};
  const mats = {};
  const M = (c, o) => new THREE.MeshStandardMaterial(Object.assign({ color: c, roughness: 0.85, metalness: 0.05 }, o || {}));

  function wallTex(kind) {
    if (texCache[kind]) return texCache[kind];
    const c = document.createElement("canvas"); c.width = c.height = 128; const g = c.getContext("2d");
    const base = { masonry: "#c98a63", concrete: "#bfc3c6", panels: "#dde7ee" }[kind];
    g.fillStyle = base; g.fillRect(0, 0, 128, 128);
    if (kind === "masonry") { g.strokeStyle = "rgba(90,50,30,.35)"; g.lineWidth = 1; for (let y = 0; y < 128; y += 8) { g.beginPath(); g.moveTo(0, y); g.lineTo(128, y); g.stroke(); for (let x = (y / 8 % 2) * 8; x < 128; x += 16) { g.beginPath(); g.moveTo(x, y); g.lineTo(x, y + 8); g.stroke(); } } }
    if (kind === "concrete") { g.fillStyle = "rgba(0,0,0,.05)"; for (let i = 0; i < 60; i++) g.fillRect(Math.random() * 128, Math.random() * 128, 2, 2); g.strokeStyle = "rgba(0,0,0,.25)"; g.strokeRect(0, 0, 128, 128); }
    if (kind === "panels") { g.strokeStyle = "rgba(60,90,110,.45)"; g.lineWidth = 2; for (let x = 0; x <= 128; x += 32) { g.beginPath(); g.moveTo(x, 0); g.lineTo(x, 128); g.stroke(); } g.strokeRect(0, 0, 128, 128); }
    g.fillStyle = "#26485c"; g.fillRect(20, 40, 88, 46); g.fillStyle = "rgba(255,255,255,.22)"; g.fillRect(20, 40, 88, 10);
    const t = new THREE.CanvasTexture(c); t.wrapS = t.wrapT = THREE.RepeatWrapping; t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 4;
    return texCache[kind] = t;
  }
  function sideMat(kind, reps, floors) { const t = wallTex(kind).clone(); t.needsUpdate = true; t.repeat.set(reps, floors); return new THREE.MeshStandardMaterial({ map: t, roughness: .9 }); }

  function box(w, h, d, mat, x, y, z) { const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat); m.position.set(x, y, z); group.add(m); return m; }
  function cyl(r, h, mat, x, y, z) { const m = new THREE.Mesh(new THREE.CylinderGeometry(r, r, h, 16), mat); m.position.set(x, y, z); group.add(m); return m; }
  function strap(x1, y1, z1, x2, y2, z2, mat, t) { const a = new THREE.Vector3(x1, y1, z1), b = new THREE.Vector3(x2, y2, z2), len = a.distanceTo(b);
    const m = new THREE.Mesh(new THREE.BoxGeometry(t, t, len), mat); m.position.copy(a).add(b).multiplyScalar(.5); m.lookAt(b); group.add(m); }

  function clearGroup() { while (group.children.length) { const o = group.children.pop(); o.traverse(n => { if (n.geometry) n.geometry.dispose(); if (n.material) { (Array.isArray(n.material) ? n.material : [n.material]).forEach(m => { if (m.map && m.map.image && !Object.values(texCache).includes(m.map)) m.map.dispose(); m.dispose(); }); } }); } }

  function build(d, r) {
    clearGroup();
    const W = d.w, L = d.l, FH = 3.6, H = d.floors * FH, e = d.elevation, iso = d.seismic === "isolated" ? 0.6 : 0, y0 = e + iso;
    // soil block (see-through so foundations show)
    const soil = new THREE.Mesh(new THREE.BoxGeometry(1600, 9, 1600), new THREE.MeshStandardMaterial({ color: 0xc9b99a, transparent: true, opacity: 0.55, roughness: 1, depthWrite: false }));
    soil.position.y = -4.5; soil.renderOrder = 1; group.add(soil);
    const top = new THREE.Mesh(new THREE.PlaneGeometry(1600, 1600), new THREE.MeshStandardMaterial({ color: 0xd9ceb4, roughness: 1 })); top.rotation.x = -Math.PI / 2; top.position.y = 0.01; group.add(top);
    // support positions
    const nx = Math.max(2, Math.round(W / 10) + 1), nz = Math.max(2, Math.round(L / 10) + 1), pts = [];
    for (let i = 0; i < nx; i++) for (let j = 0; j < nz; j++) pts.push([-W / 2 + 1 + i * (W - 2) / (nx - 1), -L / 2 + 1 + j * (L - 2) / (nz - 1)]);
    const conc = M(0x8d9499), dark = M(0x4a545c), iron = M(0x30404d);
    // foundation
    if (d.foundation === "shallow") box(W + 2, 0.7, L + 2, conc, 0, -0.35, 0);
    else if (d.foundation === "deep") pts.forEach(p => { box(2.2, 3.2, 2.2, conc, p[0], -1.6, p[1]); });
    else pts.forEach(p => { cyl(0.6, 8.5, M(0x6c7378), p[0], -4.3, p[1]); });
    // stilts when raised
    if (e > 0.05) pts.forEach(p => cyl(0.45, e + iso, conc, p[0], (e + iso) / 2, p[1]));
    if (iso) pts.forEach(p => { cyl(0.85, 0.6, M(0x1f6f8b), p[0], e + 0.3, p[1]); cyl(0.9, 0.12, M(0xe0b020), p[0], e + 0.3, p[1]); });
    // body
    const kind = d.walls, rx = Math.max(1, Math.round(W / 6)), rz = Math.max(1, Math.round(L / 6));
    const mx = sideMat(kind, rz, d.floors), mz = sideMat(kind, rx, d.floors), flat = M({ masonry: 0xb07a58, concrete: 0xaeb2b5, panels: 0xcfd9e0 }[kind]);
    const body = new THREE.Mesh(new THREE.BoxGeometry(W, H, L), [mx, mx, flat, flat, mz, mz]); body.position.set(0, y0 + H / 2, 0); group.add(body);
    // base slab
    box(W + 0.6, 0.5, L + 0.6, conc, 0, y0 - 0.05, 0);
    // steel frame shows as posts
    if (d.structure === "steel") { const n1 = Math.max(2, Math.round(W / 8) + 1), n2 = Math.max(2, Math.round(L / 8) + 1);
      for (let i = 0; i < n1; i++) { const x = -W / 2 + i * W / (n1 - 1); box(0.5, H, 0.5, iron, x, y0 + H / 2, L / 2 + 0.2); box(0.5, H, 0.5, iron, x, y0 + H / 2, -L / 2 - 0.2); }
      for (let j = 1; j < n2 - 1; j++) { const z = -L / 2 + j * L / (n2 - 1); box(0.5, H, 0.5, iron, W / 2 + 0.2, y0 + H / 2, z); box(0.5, H, 0.5, iron, -W / 2 - 0.2, y0 + H / 2, z); } }
    // corrosion band
    if (d.coating === "resistant") { box(W + 0.4, 0.9, L + 0.4, M(0x1fa6a0), 0, y0 + 0.5, 0); }
    // roof
    const rt = 0.45 + 0.12 * d.insulation, rc = { metal: 0x59626b, insulated: 0xcdbfa6, reflective: 0xf5f7f8 }[d.roof];
    box(W + 1.2, rt, L + 1.2, M(rc, d.roof === "reflective" ? { roughness: .4, emissive: 0x222222 } : {}), 0, y0 + H + rt / 2, 0);
    const ry = y0 + H + rt;
    // roof units
    const nUnits = Math.max(1, Math.min(6, Math.round(W * L / 600)));
    for (let i = 0; i < nUnits; i++) {
      const ux = -W / 2 + (i + 0.5) * W / nUnits, uz = -L / 4;
      if (d.cooling === "efficient") { box(4, 2.4, 2.6, M(0x6fb59a), ux, ry + 1.2, uz); cyl(0.9, 0.12, dark, ux - 1, ry + 2.46, uz); cyl(0.9, 0.12, dark, ux + 1, ry + 2.46, uz); }
      else box(3, 1.8, 2, M(0x8a9199), ux, ry + 0.9, uz);
    }
    // shading louvers on the front and right sides
    if (d.shading === "louvers") {
      const lm = M(0x3b4a56);
      for (let f = 0; f < d.floors; f++) { const y = y0 + f * FH + FH * 0.58; box(W, 0.12, 1.4, lm, 0, y, L / 2 + 0.75); box(1.4, 0.12, L, lm, W / 2 + 0.75, y, 0); }
    }
    // storm ties: orange straps over the roof and cross braces on one end
    if (d.windties === "ties") {
      const om = M(0xe8801a); for (let x = -W / 2 + 4; x < W / 2; x += 8) box(0.3, 0.2, L + 1.4, om, x, ry + 0.12, 0);
      strap(-W / 2 - 0.1, y0 + 0.4, L / 2 + 0.3, -W / 2 + Math.min(W, 12), y0 + H - 0.2, L / 2 + 0.3, om, 0.35);
      strap(W / 2 + 0.1, y0 + 0.4, L / 2 + 0.3, W / 2 - Math.min(W, 12), y0 + H - 0.2, L / 2 + 0.3, om, 0.35);
    }
    // seismic detailing: red braces on the right end
    if (d.seismic === "ductile") { const rm = M(0xb4452e); strap(W / 2 + 0.4, y0 + 0.4, -L / 2, W / 2 + 0.4, y0 + H - 0.2, L / 2, rm, 0.45); strap(W / 2 + 0.4, y0 + 0.4, L / 2, W / 2 + 0.4, y0 + H - 0.2, -L / 2, rm, 0.45); }
    // drainage and water
    if (d.drainage === "enhanced") { const wm = M(0x3a7ca5);
      box(W + 10, 0.08, 2.2, wm, 0, 0.06, L / 2 + 5); box(W + 10, 0.08, 2.2, wm, 0, 0.06, -L / 2 - 5); box(2.2, 0.08, L + 10, wm, W / 2 + 5, 0.06, 0); box(2.2, 0.08, L + 10, wm, -W / 2 - 5, 0.06, 0);
      const tk = cyl(3, 3, M(0x7f95a3), -W / 2 - 9, 0.9, -L / 2 - 4); }
    if (d.water === "storage") { cyl(2.2, 5, M(0x8fc2de), W / 2 + 9, 2.5, -L / 2 - 2); cyl(2.25, 0.3, M(0x4d86a5), W / 2 + 9, 5.1, -L / 2 - 2); }
    // critical equipment
    const wet = r.floodDepth > 0.3;
    if (d.equipment === "raised") box(3, 2.2, 2, M(0xf0c419), W / 4, ry + 1.1, L / 4);
    else box(3, 2.2, 2, M(wet && r.floodDepth > e ? 0xd14b2f : 0xf0c419), W / 2 + 4.5, 1.1, L / 4);
    // a few small trees for scale
    const tm = M(0x4f8a52); [[-W / 2 - 14, L / 2 + 8], [W / 2 + 15, L / 2 + 10], [W / 2 + 18, -L / 2 - 14]].forEach(t => { cyl(0.3, 3, M(0x6b4a2e), t[0], 1.5, t[1]); const s = new THREE.Mesh(new THREE.SphereGeometry(2.2, 12, 10), tm); s.position.set(t[0], 4.2, t[1]); group.add(s); });
    // flood water
    const fd = r.floodDepth;
    const pool = Math.max(W, L) + 90; water.scale.set(pool, pool, 1); water.visible = S.showFlood && fd > 0.05; water.position.y = fd; water.userData.base = fd;
    // framing
    const big = Math.max(W, L, H * 1.2);
    target.set(0, Math.max(3, (y0 + H) / 2.2), 0); dist = Math.max(60, big * 1.9);
    // sky from heat
    const heat = r.I.heat, c1 = new THREE.Color(0xcfe3ee), c2 = new THREE.Color(0xf3d29b); sky.copy(c1.lerp(c2, Math.min(1, heat * 1.3)));
    scene.background = sky; scene.fog.color.copy(sky);
    // wind streaks
    windMeshes.forEach(m => m.visible = r.I.wind > 0.25);
    // text overlay
    const gap = fd - e;
    $("#hud").innerHTML = "<b>Design flood at this place:</b> " + fd.toFixed(1) + " m &nbsp; <b>Building raised:</b> " + e.toFixed(1) + " m &nbsp; " +
      (fd < 0.05 ? "<span class='ok'>No flood water expected</span>" : gap > 0.05 ? "<span class='bad'>Water reaches " + gap.toFixed(1) + " m into the building</span>" : "<span class='ok'>Floor is above the flood</span>");
  }

  function init() {
    canvas = $("#cv"); renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    scene = new THREE.Scene(); sky = new THREE.Color(0xcfe3ee); scene.background = sky; scene.fog = new THREE.Fog(sky, 250, 900);
    camera = new THREE.PerspectiveCamera(42, 1, 1, 3000);
    scene.add(new THREE.HemisphereLight(0xffffff, 0xb8a98a, 0.85));
    sun = new THREE.DirectionalLight(0xffffff, 1.05); sun.position.set(60, 90, 40); scene.add(sun);
    group = new THREE.Group(); scene.add(group);
    water = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshStandardMaterial({ color: 0x2a7fd0, transparent: true, opacity: 0.5, roughness: .2, depthWrite: false }));
    water.rotation.x = -Math.PI / 2; water.visible = false; water.renderOrder = 3; scene.add(water);
    const wm = new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.6 });
    for (let i = 0; i < 10; i++) { const m = new THREE.Mesh(new THREE.BoxGeometry(14, 0.12, 0.12), wm); m.position.set(-80 + i * 20, 8 + (i % 4) * 6, -40 + (i * 37) % 80); m.visible = false; m.userData.sp = 0.5 + (i % 3) * 0.25; scene.add(m); windMeshes.push(m); }
    // controls
    const ptrs = new Map(); let prev = 0;
    const stopAuto = () => { lastTouch = performance.now(); };
    canvas.addEventListener("pointerdown", e => { canvas.setPointerCapture(e.pointerId); ptrs.set(e.pointerId, e); stopAuto(); if (ptrs.size === 2) { const a = [...ptrs.values()]; prev = Math.hypot(a[0].clientX - a[1].clientX, a[0].clientY - a[1].clientY); } });
    canvas.addEventListener("pointermove", e => { if (!ptrs.has(e.pointerId)) return; const o = ptrs.get(e.pointerId); ptrs.set(e.pointerId, e); stopAuto();
      if (ptrs.size === 1) { th -= (e.clientX - o.clientX) * 0.008; ph = Math.max(0.2, Math.min(1.5, ph - (e.clientY - o.clientY) * 0.006)); }
      else if (ptrs.size === 2) { const a = [...ptrs.values()], d = Math.hypot(a[0].clientX - a[1].clientX, a[0].clientY - a[1].clientY); dist = Math.max(20, Math.min(500, dist * prev / d)); prev = d; } });
    const up = e => ptrs.delete(e.pointerId); canvas.addEventListener("pointerup", up); canvas.addEventListener("pointercancel", up);
    canvas.addEventListener("wheel", e => { e.preventDefault(); dist = Math.max(20, Math.min(500, dist * (1 + Math.sign(e.deltaY) * 0.08))); stopAuto(); }, { passive: false });
    window.addEventListener("resize", resize);
    loop();
  }
  function resize() { const p = canvas.parentElement; size.w = Math.max(200, p.clientWidth); size.h = Math.max(240, p.clientHeight); renderer.setSize(size.w, size.h, false); camera.aspect = size.w / size.h; camera.updateProjectionMatrix(); }
  function loop(t) {
    raf = requestAnimationFrame(loop);
    if (performance.now() - lastTouch > 4000 && $("#auto").checked) th += 0.0018;
    camera.position.set(target.x + dist * Math.sin(ph) * Math.sin(th), target.y + dist * Math.cos(ph), target.z + dist * Math.sin(ph) * Math.cos(th)); camera.lookAt(target);
    if (water.visible) { water.position.y = water.userData.base + Math.sin((t || 0) / 700) * 0.04; }
    windMeshes.forEach(m => { if (m.visible) { m.position.x -= m.userData.sp; if (m.position.x < -100) m.position.x = 100; } });
    renderer.render(scene, camera);
  }
  return {
    init, resize,
    setDesign(d, r) { try { build(d, r); } catch (e) { console.error(e); $("#hud").textContent = "The 3D view could not draw this design."; } },
    toggleFlood(v) { S.showFlood = v; if (CACHE) build(S.d, CACHE.mine.r); },
    reset() { th = 0.8; ph = 1.2; lastTouch = 0; }
  };
})();

/* ---------------- wiring ---------------- */
function boot() {
  buildTypeTiles(); buildParams(); initPlace();
  const ok = (function () { try { const c = document.createElement("canvas"); return !!(c.getContext("webgl2") || c.getContext("webgl")); } catch (e) { return false; } })();
  if (ok) View.init(); else { $("#viewbox").innerHTML = "<div class='nogl'>The 3D view needs WebGL, which this browser does not offer. All numbers and advice still work.</div>"; Object.assign(View, { setDesign() {}, resize() {}, toggleFlood() {}, reset() {} }); }
  $$(".sc button").forEach(b => b.onclick = () => { S.scenario = b.dataset.sc; update(); });
  $("#changeplace").onclick = changePlace;
  $$("#steps button").forEach(b => b.onclick = () => { const n = +b.dataset.s; if (n === 1) changePlace(); else if (S.place) { $("#s-work").hidden = false; $("#s-place").hidden = true; $("#scbox").hidden = false; const t = { 2: "#st2", 3: "#st3", 4: "#st4", 5: "#st5" }[n]; const el = $(t); if (el) el.scrollIntoView({ behavior: "smooth", block: "start" }); setStep(n); } });
  $("#flood").onchange = e => View.toggleFlood(e.target.checked);
  $("#resetview").onclick = View.reset;
  $("#applyopt").onclick = () => { S.d = Object.assign({}, CACHE.opt.d); update(); };
  $("#applyall").onclick = () => { E.advice(site(), S.type, S.d, S.scenario).forEach(a => { if (!(a.pid === "elevation" && S.d.elevation >= a.v)) S.d[a.pid] = a.v; }); update(); };
  $("#resetstd").onclick = () => { const T = S.d; S.d = Object.assign({}, E.BASELINE, { floors: T.floors, w: T.w, l: T.l }); update(); };
  $("#retry").onclick = async () => { $("#loading").hidden = false; $("#loadtext").textContent = "Trying again…"; await loadSite(S.place); $("#loading").hidden = true; buildSiteEditor(); update(); };
  window.__rasd = { S, E, update, usePlace, compute: () => CACHE };
}
document.addEventListener("DOMContentLoaded", boot);
})();
