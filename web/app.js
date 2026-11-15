/* Solaratlas Pilot: map (MapLibre) + ranking (D3) + quality panel.
   All data comes from the files the pipeline writes to ./data/. */

const RAMP = ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]; // sequential blue, light -> dark
const NODATA = "#e6e4df";
const VIEWS = {
  laender: { label: "Bundesländer", outline: null },
  kreise: { label: "Kreise", outline: "laender" },
  pilot_kreise: { label: "Kreise", outline: "laender" },
  pilot_gemeinden: { label: "Gemeinden", outline: "pilot_kreise" },
};
const state = { view: "laender", indicator: "pv_w_per_capita", hover: null };
const data = {};
let meta, map;

const fmt = (v, d = 0) =>
  v == null || Number.isNaN(v) ? "keine Daten"
    : new Intl.NumberFormat("de-DE", { maximumFractionDigits: d, minimumFractionDigits: d }).format(v);
const ind = id => meta.indicators.find(i => i.indicator_id === id);
const $ = sel => document.querySelector(sel);

/* Planar bounds from raw coordinates. (d3.geoBounds is spherical and depends on
   ring winding order, which PostGIS does not guarantee.) */
function bbox(geojson) {
  let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity;
  const walk = c => {
    if (typeof c[0] === "number") {
      w = Math.min(w, c[0]); e = Math.max(e, c[0]); s = Math.min(s, c[1]); n = Math.max(n, c[1]);
    } else c.forEach(walk);
  };
  (geojson.features || [geojson]).forEach(f => walk(f.geometry.coordinates));
  return [[w, s], [e, n]];
}
const center = f => { const [[w, s], [e, n]] = bbox(f); return [(w + e) / 2, (s + n) / 2]; };

async function load() {
  meta = await (await fetch("data/meta.json")).json();
  await Promise.all(Object.keys(VIEWS).map(async k => {
    data[k] = await (await fetch(`data/${k}.geojson`)).json();
  }));
}

/* ---------- classes: quantiles over the current view ---------- */
function classes() {
  const vals = data[state.view].features.map(f => f.properties[state.indicator])
    .filter(v => v != null).sort(d3.ascending);
  const breaks = [];
  for (let i = 1; i < RAMP.length; i++) {
    const q = d3.quantileSorted(vals, i / RAMP.length);
    if (q != null && (!breaks.length || q > breaks[breaks.length - 1])) breaks.push(q);
  }
  const colors = RAMP.slice(RAMP.length - breaks.length - 1);
  const color = v => (v == null ? NODATA : colors[d3.bisectRight(breaks, v)]);
  return { breaks, colors, color, min: vals[0], max: vals[vals.length - 1] };
}

function fillExpression(c) {
  const step = ["step", ["get", state.indicator], c.colors[0]];
  c.breaks.forEach((b, i) => step.push(b, c.colors[i + 1]));
  return ["case", ["==", ["typeof", ["get", state.indicator]], "number"], step, NODATA];
}

/* ---------- map ---------- */
function initMap() {
  map = new maplibregl.Map({
    container: "map",
    style: { version: 8, sources: {}, layers: [
      { id: "bg", type: "background", paint: { "background-color": "#f3f2ee" } }] },
    attributionControl: false,
    dragRotate: false,
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.addControl(new maplibregl.AttributionControl({
    customAttribution: "© BKG dl-de/by-2-0 · © Bundesnetzagentur MaStR dl-de/by-2-0",
  }));
  map.on("load", () => {
    for (const k of Object.keys(VIEWS)) {
      map.addSource(k, { type: "geojson", data: data[k], promoteId: "ags" });
      map.addLayer({ id: `${k}-fill`, type: "fill", source: k,
        layout: { visibility: "none" }, paint: { "fill-color": NODATA } });
      map.addLayer({ id: `${k}-line`, type: "line", source: k,
        layout: { visibility: "none" },
        paint: { "line-color": "#fcfcfb", "line-width": k === "laender" || k === "pilot_kreise" ? 1 : 0.5 } });
    }
    for (const k of Object.keys(VIEWS)) {
      map.addLayer({ id: `${k}-context`, type: "line", source: k,
        layout: { visibility: "none" }, paint: { "line-color": "#52514e", "line-width": 1.2 } });
      map.addLayer({ id: `${k}-hover`, type: "line", source: k,
        layout: { visibility: "none" },
        paint: { "line-color": "#0b0b0b",
                 "line-width": ["case", ["boolean", ["feature-state", "hover"], false], 2.5, 0] } });
      map.on("mousemove", `${k}-fill`, e => k === state.view && setHover(e.features[0].properties.ags, e.point));
      map.on("mouseleave", `${k}-fill`, () => k === state.view && setHover(null));
      map.on("click", `${k}-fill`, e => k === state.view && drillDown(e.features[0].properties.ags));
    }
    render(true);
  });
}

function setHover(ags, point) {
  if (state.hover) map.setFeatureState({ source: state.view, id: state.hover }, { hover: false });
  state.hover = ags;
  const tip = $("#tooltip");
  d3.selectAll(".chart .row").classed("active", d => d.properties.ags === ags);
  if (!ags) { tip.hidden = true; map.getCanvas().style.cursor = ""; return; }
  map.setFeatureState({ source: state.view, id: ags }, { hover: true });
  map.getCanvas().style.cursor = "pointer";
  const f = data[state.view].features.find(x => x.properties.ags === ags);
  const p = f.properties, i = ind(state.indicator);
  const ranked = rankedFeatures().filter(x => x.properties[state.indicator] != null);
  const rank = ranked.findIndex(x => x.properties.ags === ags) + 1;
  tip.innerHTML = `<strong>${p.name}</strong>${p.bez && state.view !== "laender" ? `<span class="muted">${p.bez}</span><br>` : ""}
    ${i.name_de}: <b>${fmt(p[state.indicator], i.decimals)} ${i.unit}</b><br>
    <span class="muted">${rank ? `Rang ${rank} von ${ranked.length} · ` : ""}${fmt(p.population)} Einwohner</span>`;
  const pt = point || map.project(center(f));
  const box = map.getContainer().getBoundingClientRect();
  tip.style.left = `${Math.min(pt.x + 14, box.width - 270)}px`;
  tip.style.top = `${Math.max(pt.y - 10, 8)}px`;
  tip.hidden = false;
}

/* ---------- ranking chart (D3) ---------- */
function rankedFeatures() {
  return [...data[state.view].features].sort((a, b) =>
    d3.descending(a.properties[state.indicator] ?? -Infinity, b.properties[state.indicator] ?? -Infinity));
}

function renderChart(c) {
  const i = ind(state.indicator);
  const rows = rankedFeatures();
  const el = $("#chart");
  const width = el.clientWidth - 4, rowH = 20, labelW = Math.min(170, width * 0.42), valW = 74;
  const x = d3.scaleLinear().domain([0, c.max || 1]).range([0, Math.max(10, width - labelW - valW - 8)]);
  el.innerHTML = "";
  const svg = d3.select(el).append("svg").attr("width", width).attr("height", rows.length * rowH + 4)
    .attr("role", "img").attr("aria-label", `Rangfolge: ${i.name_de}`);
  const g = svg.selectAll("g.row").data(rows).join("g").attr("class", "row")
    .attr("transform", (d, k) => `translate(0,${k * rowH + 2})`);
  g.append("rect").attr("class", "hit").attr("width", width).attr("height", rowH).attr("rx", 4);
  g.append("text").attr("x", 4).attr("y", rowH / 2 + 4)
    .text(d => d.properties.name).each(function () {
      let t = this.textContent;
      while (this.getComputedTextLength() > labelW - 10 && t.length > 3) { t = t.slice(0, -2); this.textContent = t + "…"; }
    });
  g.append("rect").attr("x", labelW).attr("y", 4).attr("height", rowH - 8).attr("rx", 2)
    .attr("width", d => (d.properties[state.indicator] == null ? 0 : Math.max(2, x(d.properties[state.indicator]))))
    .attr("fill", d => c.color(d.properties[state.indicator]));
  g.append("text").attr("class", "val").attr("x", width - 4).attr("y", rowH / 2 + 4).attr("text-anchor", "end")
    .text(d => fmt(d.properties[state.indicator], i.decimals));
  g.on("mouseenter", (e, d) => setHover(d.properties.ags))
   .on("mouseleave", () => setHover(null))
   .on("click", (e, d) => drillDown(d.properties.ags));
}

/* ---------- legend, controls, panels ---------- */
function renderLegend(c) {
  const i = ind(state.indicator);
  const edges = [c.min, ...c.breaks, c.max];
  const items = c.colors.map((col, k) =>
    `<div class="item"><span class="sw" style="background:${col}"></span>${fmt(edges[k], i.decimals)} bis ${fmt(edges[k + 1], i.decimals)}</div>`);
  const hasNull = data[state.view].features.some(f => f.properties[state.indicator] == null);
  if (hasNull) items.push(`<div class="item"><span class="sw" style="background:${NODATA}"></span>keine Daten</div>`);
  $("#legend").innerHTML = `<div class="title">${i.name_de} (${i.unit})</div>${items.join("")}`;
}

function setView(v) { state.view = v; setHover(null); render(true); }

/* Click on Brandenburg (Länder view) or on a Brandenburg Kreis (Germany view) opens the pilot region. */
function drillDown(ags) {
  if (state.view === "laender" && ags === meta.pilot_land.ags) setView("pilot_kreise");
  else if (state.view === "kreise" && ags.startsWith(meta.pilot_land.ags)) setView("pilot_gemeinden");
}

function render(fit) {
  const c = classes();
  const outline = VIEWS[state.view].outline;
  for (const k of Object.keys(VIEWS)) {
    const on = k === state.view ? "visible" : "none";
    map.setLayoutProperty(`${k}-fill`, "visibility", on);
    map.setLayoutProperty(`${k}-line`, "visibility", on);
    map.setLayoutProperty(`${k}-hover`, "visibility", on);
    map.setLayoutProperty(`${k}-context`, "visibility", k === outline ? "visible" : "none");
  }
  map.setPaintProperty(`${state.view}-fill`, "fill-color", fillExpression(c));
  if (fit) {
    map.resize();
    map.fitBounds(bbox(data[state.view]), { padding: 30, duration: 600 });
  }
  document.querySelectorAll("#level-switch button").forEach(b =>
    b.setAttribute("aria-checked", String(b.dataset.view === state.view)));
  const i = ind(state.indicator);
  const where = state.view === "laender" ? "Bundesländer"
    : state.view === "kreise" ? "Kreise in Deutschland"
    : `${VIEWS[state.view].label} in ${meta.pilot_land.name}`;
  $("#rank-title").textContent = `${i.name_de}: ${where}`;
  $("#rank-hint").textContent = state.view === "laender"
    ? `${i.description_de}. Klick auf ${meta.pilot_land.name} öffnet die Pilotregion.`
    : state.view === "kreise"
    ? `${i.description_de}. Klick auf einen Kreis in ${meta.pilot_land.name} öffnet die Gemeinden.`
    : `${i.description_de}.`;
  renderLegend(c);
  renderChart(c);
  updateCsv();
}

function buildControls() {
  $("#pilot-name").textContent = meta.pilot_land.name;
  const sw = $("#level-switch");
  for (const [k, v] of Object.entries(VIEWS)) {
    const b = document.createElement("button");
    b.type = "button"; b.dataset.view = k; b.setAttribute("role", "radio");
    b.textContent = k === "laender" ? v.label
      : k === "kreise" ? `Deutschland: ${v.label}` : `${meta.pilot_land.name}: ${v.label}`;
    b.onclick = () => setView(k);
    sw.appendChild(b);
  }
  const order = ["pv_w_per_capita", "pv_units_per_1000", "pv_rooftop_share", "pv_capacity_mw"];
  const sel = $("#indicator");
  for (const id of order) {
    const i = ind(id); if (!i) continue;
    sel.insertAdjacentHTML("beforeend", `<option value="${id}">${i.name_de} (${i.unit})</option>`);
  }
  sel.value = state.indicator;
  sel.onchange = () => { state.indicator = sel.value; setHover(null); render(false); };
}

function renderKpis() {
  const n = meta.national;
  const stand = new Date(meta.period).toLocaleDateString("de-DE");
  $("#kpis").innerHTML = [
    ["PV in Betrieb", `${fmt(n.pv_capacity_mw / 1000, 1)} GW`],
    ["je Einwohner", `${fmt(n.pv_w_per_capita)} W`],
    ["Anlagen", fmt(n.units)],
    ["Datenstand", stand],
  ].map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("");
}

function renderChecks() {
  $("#checks").innerHTML = meta.checks.map(c => {
    const [cls, label] = c.passed ? ["ok", "✓ bestanden"]
      : c.severity === "error" ? ["fail", "✕ Fehler"] : ["warn", "! Hinweis"];
    const kind = c.severity === "error" ? "Fehlerprüfung" : "Hinweis";
    return `<li><div class="head"><span class="badge ${cls}">${label}</span>${c.title}</div>
      <p class="detail">${c.detail} <span class="muted">(${kind})</span></p></li>`;
  }).join("");
}

function renderSources() {
  $("#sources").innerHTML = meta.sources.map(s =>
    `<li><a href="${s.url}">${s.name}</a>, ${s.publisher}${s.ref_date ? `, Stand ${new Date(s.ref_date).toLocaleDateString("de-DE")}` : ""}. ${s.attribution}</li>`
  ).join("");
  $("#method").textContent =
    "Berücksichtigt sind Photovoltaikanlagen mit Status „In Betrieb“. Landeswerte enthalten alle Anlagen mit bekanntem Bundesland; " +
    "Kreis- und Gemeindewerte alle Anlagen, deren Gemeindeschlüssel zum Gebietsstand der Karte passt. " +
    "Einwohnerzahlen aus VG250-EW, damit Fläche und Einwohner denselben Gebietsstand haben. " +
    `Aktualisiert am ${new Date(meta.generated_at).toLocaleString("de-DE")}.`;
  const m = location.hostname.match(/^([^.]+)\.github\.io$/);
  const repo = m ? `https://github.com/${m[1]}/${location.pathname.split("/")[1]}` : "../";
  $("#repo-link").href = repo;
}

function updateCsv() {
  const ids = meta.indicators.map(i => i.indicator_id);
  const rows = [["ags", "name", "einwohner", ...ids].join(";")].concat(
    data[state.view].features.map(f => {
      const p = f.properties;
      return [p.ags, `"${p.name}"`, p.population ?? "", ...ids.map(id => p[id] ?? "")].join(";");
    }));
  const a = $("#csv");
  a.href = URL.createObjectURL(new Blob(["﻿" + rows.join("\n")], { type: "text/csv" }));
  a.download = `solaratlas_${state.view}_${meta.period}.csv`;
}

load().then(() => {
  if (meta.mode !== "live") $("#fixture-banner").hidden = false;
  buildControls(); renderKpis(); renderChecks(); renderSources(); initMap();
  window.addEventListener("resize", () => { map.resize(); renderChart(classes()); });
}).catch(err => {
  document.body.insertAdjacentHTML("afterbegin",
    `<p style="padding:20px;color:#d03b3b">Daten konnten nicht geladen werden: ${err}</p>`);
});
