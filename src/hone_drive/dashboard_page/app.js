// hone-drive dashboard (design §10). Everything shown comes from /api; text goes in with textContent only.

const TOKEN = document.querySelector('meta[name="hone-token"]').content;
const STORE_SLOTS = ["ollama", "comfyui", "huggingface", "docker", "lmstudio", "llamacpp", "caches", "custom"];
const TEST_NAMES = { "seq-write": "Sequential write", "seq-read": "Sequential read", "model-read": "Model files read",
  "random-read": "Random 4 KiB reads", "sustained-write": "Sustained write" };
const state = { runs: [], view: null, job: { running: false }, screen: "overview", ticks: [], tickTest: "" };

// -- helpers -----------------------------------------------------------------------------------------------

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "style") node.style.cssText = value; // CSSOM: allowed by the page's CSP, unlike style=""
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const SVG = "http://www.w3.org/2000/svg";
function svg(tag, attrs = {}, ...children) {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "text") node.textContent = value;
    else if (key === "fill" || key === "stroke") node.style[key] = value; // var() works in style, not in attributes
    else node.setAttribute(key, value);
  }
  children.flat().forEach((c) => c && node.append(c));
  return node;
}

function size(n) {
  if (n === null || n === undefined) return "?";
  for (const [unit, scale] of [["TB", 1e12], ["GB", 1e9], ["MB", 1e6], ["kB", 1e3]]) {
    if (Math.abs(n) >= scale) { const v = n / scale; return `${v < 100 ? v.toFixed(1) : v.toFixed(0)} ${unit}`; }
  }
  return `${n} B`;
}
const speed = (g) => (g === null || g === undefined ? "?" : g >= 1 ? `${g.toFixed(2)} GB/s` : `${Math.round(g * 1000)} MB/s`);
const secs = (s) => (s === null || s === undefined ? "?" : s < 10 ? `${s.toFixed(1)} s` : s < 60 ? `${Math.round(s)} s`
  : s < 3600 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`);
const pct = (f) => `${Math.round(f * 100)}%`;
const storeColor = (id) => { const i = STORE_SLOTS.indexOf(id); return i >= 0 ? `var(--s${i + 1})` : "var(--other)"; };
const volColor = (i) => `var(--s${(i % 3) + 1})`;

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: "POST", body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", "X-Hone-Drive-Token": TOKEN },
  };
  const response = await fetch(`/api${path}`, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

const tooltip = document.getElementById("tooltip");
function tip(target, lines) {
  const show = (event) => {
    tooltip.replaceChildren(el("strong", { text: lines[0] }), ...lines.slice(1).map((l) => el("div", { class: "secondary", text: l })));
    tooltip.hidden = false;
    const box = event.target.getBoundingClientRect();
    const x = event.clientX ?? box.left + box.width / 2;
    const y = event.clientY ?? box.top;
    tooltip.style.left = `${Math.min(x + 12, window.innerWidth - 340)}px`;
    tooltip.style.top = `${y + 14}px`;
  };
  target.addEventListener("pointermove", show);
  target.addEventListener("focus", show);
  target.addEventListener("pointerleave", () => (tooltip.hidden = true));
  target.addEventListener("blur", () => (tooltip.hidden = true));
  target.tabIndex = 0;
}

// -- loading and events ------------------------------------------------------------------------------------

async function load(runId = "latest") {
  const listing = await api("/runs");
  state.runs = listing.runs;
  state.job = listing.job;
  renderJob();
  document.getElementById("empty").hidden = state.runs.length > 0;
  if (!state.runs.length) { document.querySelectorAll(".screen").forEach((s) => (s.hidden = true)); return; }
  state.view = await api(`/runs/${runId}`);
  render();
}

function listen() {
  const events = new EventSource("/api/events");
  events.addEventListener("job", (e) => { state.job = JSON.parse(e.data); state.ticks = []; renderJob(); });
  events.addEventListener("progress", (e) => { state.job.message = JSON.parse(e.data).message; renderJob(); });
  events.addEventListener("tick", (e) => {
    const t = JSON.parse(e.data);
    if (t.test !== state.tickTest) { state.ticks = []; state.tickTest = t.test; }
    state.ticks.push(t);
    state.job.message = `${TEST_NAMES[t.test] || t.test} on ${t.volume}: ${Math.floor((100 * t.done) / Math.max(t.total, 1))}%`;
    renderJob();
    if (state.screen === "bench") renderLive();
  });
  events.addEventListener("done", (e) => { state.job = JSON.parse(e.data); renderJob(); load(); });
  events.addEventListener("failed", (e) => { state.job = JSON.parse(e.data); renderJob(); });
}

function renderJob() {
  const box = document.getElementById("job");
  const job = state.job || {};
  const children = [];
  if (job.running) {
    children.push(el("span", { class: "spinner", "aria-hidden": "true" }), el("span", { text: job.message || job.kind }),
      el("button", { class: "secondary-btn", text: "Stop", onclick: () => api("/stop", {}) }));
  } else if (job.message && job.message !== "done") {
    children.push(el("span", { class: "status warn", text: job.message }));
  }
  box.replaceChildren(...children);
  document.querySelectorAll("[data-action]").forEach((b) => (b.disabled = !!job.running));
}

async function start(path, body) {
  try { state.job = await api(path, body); renderJob(); } catch (error) { state.job = { message: error.message }; renderJob(); }
}

// -- screens -----------------------------------------------------------------------------------------------

function render() {
  const screens = { overview: renderOverview, drives: renderDrives, bench: renderBench, models: renderModels,
    advice: renderAdvice, history: renderHistory };
  for (const [name, draw] of Object.entries(screens)) {
    const section = document.getElementById(`screen-${name}`);
    section.hidden = name !== state.screen;
    if (name === state.screen) section.replaceChildren(...draw());
  }
  renderJob();
}

function runHeader() {
  const run = state.view.run;
  const m = run.machine;
  return el("div", { class: "row" },
    el("div", {}, el("h1", { text: m.os_version }),
      el("p", { class: "secondary", text: [m.cpu, m.ram_bytes ? `RAM ${size(m.ram_bytes)}` : null,
        ...m.gpus.map((g) => g.name + (g.memory_bytes ? ` ${size(g.memory_bytes)}` : ""))].filter(Boolean).join(" · ") }),
      el("p", { class: "muted", text: `Run ${run.id} · ${new Date(run.created).toLocaleString()}` })),
    el("div", { class: "row", style: "margin-left:auto" },
      el("button", { class: "primary", "data-action": "scan", text: "Scan again", onclick: () => start("/scan", {}) }),
      el("button", { class: "secondary-btn", "data-action": "account", text: "Account for space",
        title: "Walk each volume to find space no scan could see (minutes on a big disk)",
        onclick: () => start("/scan", { accounting: true }) }),
      el("button", { class: "secondary-btn", "data-action": "dups", text: "Find duplicates",
        onclick: () => start("/scan", { duplicates: true }) })));
}

function renderOverview() {
  const { run, advice, volumes } = state.view;
  const used = run.volumes.reduce((a, v) => a + v.used_bytes, 0);
  const biggest = [...run.stores].sort((a, b) => (b.bytes || 0) - (a.bytes || 0))[0];
  const external = run.drives.filter((d) => d.external).length;
  const tiles = el("div", { class: "tiles" },
    tile("Drives", String(run.drives.length), external ? `${external} external` : "none external"),
    tile("Used on all volumes", size(used), `${run.volumes.length} volumes`),
    tile("Biggest store", biggest ? size(biggest.bytes) : "–", biggest ? biggest.title : "no model stores found"),
    tile("Advice", String(advice.length), `${advice.filter((a) => a.severity === "warn").length} warnings`));
  const bars = el("div", { class: "card" }, el("h2", { text: "Where the space goes" }),
    ...volumes.map((v) => volumeBar(run.volumes.find((x) => x.mount === v.mount), v)),
    volumes.every((v) => v.accounted) ? null : el("p", { class: "muted", text: "Space outside the known stores shows as other files. “Account for space” walks each volume to find what no scan could read." }));
  const top = el("div", { class: "card" }, el("h2", { text: "Top advice" }),
    ...(advice.length ? advice.slice(0, 3).map((a) => adviceCard(a, false)) : [el("p", { class: "muted", text: "Nothing to fix." })]),
    advice.length > 3 ? el("button", { class: "secondary-btn", text: `All ${advice.length} items`, onclick: () => go("advice") }) : null);
  return [runHeader(), tiles, bars, top];
}

function tile(label, value, sub) {
  return el("div", { class: "tile" }, el("div", { class: "label", text: label }), el("div", { class: "value", text: value }),
    el("div", { class: "sub", text: sub }));
}

function volumeBar(volume, view) {
  const bar = el("div", { class: "bar", role: "img", "aria-label": `${volume.mount}: ${size(volume.used_bytes)} used of ${size(volume.total_bytes)}` });
  const ordered = [...view.segments].sort((a, b) => slot(a.kind) - slot(b.kind));
  for (const s of ordered) {
    const seg = el("div", { class: `seg ${s.kind === "unaccounted" ? "unaccounted" : ""}` });
    seg.style.width = `${(100 * s.bytes) / volume.total_bytes}%`;
    if (s.kind !== "unaccounted") seg.style.background = s.kind === "other" ? "var(--other)" : storeColor(s.kind);
    tip(seg, [size(s.bytes), s.label, `${pct(s.bytes / volume.total_bytes)} of ${volume.mount}`]);
    bar.append(seg);
  }
  const legend = el("div", { class: "legend" }, ...ordered.map((s) => el("span", { class: "key" },
    el("span", { class: `swatch ${s.kind === "unaccounted" ? "unaccounted" : ""}`,
      style: s.kind === "unaccounted" ? "" : `background:${s.kind === "other" ? "var(--other)" : storeColor(s.kind)}` }),
    `${s.label} ${size(s.bytes)}`)), el("span", { class: "key" }, el("span", { class: "swatch", style: "background:var(--track)" }),
    `free ${size(volume.free_bytes)}`));
  const drive = state.view.run.drives.find((d) => d.id === volume.drive_id);
  return el("div", { class: "vol" },
    el("div", { class: "vol-head" },
      el("div", {}, el("span", { class: "vol-title", text: volume.mount }), " ",
        el("span", { class: "muted", text: `${volume.filesystem}${drive ? ` · ${drive.model || drive.id}` : ""}` })),
      el("div", { class: "secondary", text: `${size(volume.used_bytes)} of ${size(volume.total_bytes)} · ${pct(volume.used_bytes / volume.total_bytes)} full` })),
    bar, legend);
}
const slot = (kind) => (kind === "other" ? 90 : kind === "unaccounted" ? 99 : STORE_SLOTS.indexOf(kind) < 0 ? 80 : STORE_SLOTS.indexOf(kind));

function renderDrives() {
  const { run, drives } = state.view;
  return [runHeader(), el("div", { class: "grid2" }, ...run.drives.map((d) => {
    const view = drives.find((x) => x.id === d.id);
    const link = d.link;
    const wireClass = link.limited ? "limited" : link.limited === false ? "full" : "";
    const facts = [
      ["Kind", `${d.kind}, ${d.external ? "external" : "internal"}`], ["Size", size(d.size_bytes)],
      ["Bus", link.bus], ["Link", link.name || "unknown"],
      ["Negotiated", link.negotiated_gbps ? `${link.negotiated_gbps} Gbps${link.negotiated_lanes ? `, ${link.negotiated_lanes} lane(s)` : ""}` : null],
      ["Port can do", link.port_max_gbps ? `${link.port_max_gbps} Gbps${link.port_max_lanes ? `, ${link.port_max_lanes} lane(s)` : ""}` : null],
      ["Usable throughput", link.expected_gbs ? `about ${speed(link.expected_gbs)}` : null],
    ];
    const volumes = run.volumes.filter((v) => v.drive_id === d.id).map((v) => {
      const missing = v.traits ? [["Unix permissions", v.traits.posix_permissions], ["hard links", v.traits.hard_links],
        ["symlinks", v.traits.symlinks], ["a journal", v.traits.journaled], ["files over 4 GB", v.traits.large_files]]
        .filter(([, ok]) => !ok).map(([name]) => name) : [];
      return el("li", {}, el("strong", { text: v.mount }), ` · ${v.filesystem} · ${pct(v.used_bytes / v.total_bytes)} full`,
        v.at_boot === false ? " · mounted at login, not at boot" : "",
        missing.length ? el("div", { class: "secondary", text: `${v.filesystem} has no ${missing.join(", no ")}` }) : null);
    });
    const reasons = Object.entries({ ...d.unknown, ...link.unknown });
    return el("div", { class: "card" },
      el("div", { class: "row" }, el("h2", { text: d.model || d.id }),
        link.limited ? el("span", { class: "status suggest", text: "Link limited" }) : null),
      el("div", { class: "link" }, el("div", { class: "node", text: link.port_max_gbps ? `Port · ${link.port_max_gbps} Gbps` : "Port" }),
        el("div", { class: `wire ${wireClass}` }, el("span", { text: view.link_text })), el("div", { class: "node", text: "Cable" }),
        el("div", { class: `wire ${wireClass}` }), el("div", { class: "node", text: d.kind })),
      el("dl", { class: "facts" }, ...facts.filter(([, v]) => v).flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { text: v })])),
      volumes.length ? el("ul", { class: "volumes" }, ...volumes) : el("p", { class: "muted", text: "No volume mounted." }),
      reasons.length ? el("details", {}, el("summary", { class: "muted", text: `${reasons.length} fact(s) the OS could not tell` }),
        el("ul", {}, ...reasons.map(([k, v]) => el("li", { class: "secondary", text: `${k}: ${v}` })))) : null);
  }))];
}

function renderBench() {
  const { run } = state.view;
  const home = run.volumes.find((v) => v.system);
  const form = el("div", { class: "card" }, el("h2", { text: "Measure" }),
    el("p", { class: "secondary", text: "Writes one temporary file per drive (removed afterwards) and reads it back with the operating system's cache bypassed. Model reads use your largest model files, read only." }),
    el("div", { class: "row" }, ...run.volumes.filter((v) => !v.read_only).map((v) => {
      const drive = run.drives.find((d) => d.id === v.drive_id);
      const checked = v === home || (drive && drive.external);
      return el("label", { class: "check" }, el("input", { type: "checkbox", name: "vol", value: v.mount, checked }),
        `${v.mount} (${size(v.free_bytes)} free)`);
    })),
    el("div", { class: "row" }, ...[["seq", "Sequential", true], ["model", "Model files", true], ["random", "Random reads", false],
      ["sustained", "Sustained write (32 GB)", false]].map(([value, label, checked]) =>
      el("label", { class: "check" }, el("input", { type: "checkbox", name: "test", value, checked }), label)),
    el("label", { class: "check" }, "File size ", el("select", { name: "size" },
      ...["1G", "4G", "16G"].map((s) => el("option", { value: s, selected: s === "4G", text: s.replace("G", " GiB") }))))),
    el("div", { class: "row" }, el("button", { class: "primary", "data-action": "bench", text: "Start", onclick: () => {
      const pick = (name) => [...document.querySelectorAll(`input[name="${name}"]:checked`)].map((i) => i.value);
      const folders = pick("vol").map((m) => (m === home?.mount ? null : m)).filter(Boolean);
      start("/bench", { folders: home && pick("vol").includes(home.mount) ? ["~", ...folders] : folders,
        tests: pick("test"), size: document.querySelector('select[name="size"]').value });
    } })));
  const live = el("div", { class: "card", id: "live" });
  const results = el("div", { class: "card" }, el("h2", { text: "Results" }), ...benchResults(run));
  setTimeout(renderLive);
  return [form, live, results];
}

function renderLive() {
  const box = document.getElementById("live");
  if (!box) return;
  if (!state.ticks.length) { box.replaceChildren(el("h2", { text: "Live" }), el("p", { class: "muted", text: "Throughput appears here while a test runs." })); return; }
  const values = state.ticks.map((t) => t.gbs);
  box.replaceChildren(el("h2", { text: `${TEST_NAMES[state.tickTest] || state.tickTest} · ${state.ticks.at(-1).volume}` }),
    lineChart(values, "chunk", "var(--s1)"));
}

function lineChart(values, unit, color) {
  const w = 720, h = 180, pad = { l: 56, r: 12, t: 10, b: 24 };
  const max = Math.max(...values, 0.1) * 1.1;
  const x = (i) => pad.l + (i / Math.max(values.length - 1, 1)) * (w - pad.l - pad.r);
  const y = (v) => h - pad.b - (v / max) * (h - pad.t - pad.b);
  const ticks = [0, max / 2, max];
  const chart = svg("svg", { viewBox: `0 0 ${w} ${h}`, class: "chart", role: "img", "aria-label": `Throughput per ${unit}` },
    ...ticks.map((t) => svg("line", { class: "gridline", x1: pad.l, x2: w - pad.r, y1: y(t), y2: y(t) })),
    ...ticks.map((t) => svg("text", { x: pad.l - 6, y: y(t) + 4, "text-anchor": "end", text: speed(t) })),
    svg("path", { class: "line", stroke: color, d: values.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join(" ") }),
    svg("circle", { cx: x(values.length - 1), cy: y(values.at(-1)), r: 4, fill: color, stroke: "var(--surface)", "stroke-width": 2 }),
    svg("text", { class: "value", x: Math.min(x(values.length - 1) + 8, w - 60), y: y(values.at(-1)) - 8, text: speed(values.at(-1)) }));
  const hair = svg("line", { class: "axis", y1: pad.t, y2: h - pad.b, visibility: "hidden" });
  chart.append(hair);
  chart.addEventListener("pointermove", (e) => {
    const box = chart.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * w;
    const i = Math.max(0, Math.min(values.length - 1, Math.round(((px - pad.l) / (w - pad.l - pad.r)) * (values.length - 1))));
    hair.setAttribute("x1", x(i)); hair.setAttribute("x2", x(i)); hair.setAttribute("visibility", "visible");
    tooltip.replaceChildren(el("strong", { text: speed(values[i]) }), el("div", { class: "secondary", text: `${unit} ${i + 1}` }));
    tooltip.hidden = false; tooltip.style.left = `${e.clientX + 12}px`; tooltip.style.top = `${e.clientY + 14}px`;
  });
  chart.addEventListener("pointerleave", () => { hair.setAttribute("visibility", "hidden"); tooltip.hidden = true; });
  return chart;
}

function benchResults(run) {
  if (!run.measurements.length) return [el("p", { class: "muted", text: "No measurements yet." })];
  const volumes = [...new Set(run.measurements.map((m) => m.volume))];
  const tests = ["seq-read", "model-read", "seq-write", "sustained-write", "random-read"].filter((t) => run.measurements.some((m) => m.test === t));
  const rowH = 22, groupGap = 18, labelW = 150, w = 720;
  const max = Math.max(...run.measurements.map((m) => m.gbs)) * 1.15;
  let y = 4;
  const marks = [];
  for (const test of tests) {
    marks.push(svg("text", { class: "label", x: 0, y: y + 14, text: TEST_NAMES[test] }));
    for (const [vi, volume] of volumes.entries()) {
      const items = run.measurements.filter((m) => m.test === test && m.volume === volume);
      if (!items.length) continue;
      const m = items.reduce((a, b) => (a.bytes > b.bytes ? a : b));
      const bw = Math.max(2, ((w - labelW - 120) * m.gbs) / max);
      const bar = svg("path", { d: roundedBar(labelW, y + 2, bw, 16), fill: volColor(vi) });
      bar.setAttribute("tabindex", "0");
      tip(bar, [speed(m.gbs), `${TEST_NAMES[test]} · ${volume}`, m.label || "", m.cache === "direct" ? "page cache bypassed" : `cache: ${m.cache}`,
        m.iops ? `${Math.round(m.iops).toLocaleString()} IOPS at 32 in flight, ${m.latency_ms} ms one at a time` : ""].filter(Boolean));
      marks.push(bar, svg("text", { class: "value", x: labelW + bw + 6, y: y + 14, text: speed(m.gbs) + (m.label ? ` · ${m.label}` : "") }));
      y += rowH;
    }
    y += groupGap;
  }
  const chart = svg("svg", { viewBox: `0 0 ${w} ${y}`, class: "chart", role: "img", "aria-label": "Measured speeds" },
    svg("line", { class: "axis", x1: labelW, x2: labelW, y1: 0, y2: y - groupGap }), ...marks);
  const legend = el("div", { class: "legend" }, ...volumes.map((v, i) => el("span", { class: "key" },
    el("span", { class: "swatch", style: `background:${volColor(i)}` }), v)));
  const table = el("details", {}, el("summary", { class: "muted", text: "Table" }), el("table", {},
    el("tr", {}, ...["Test", "Volume", "Speed", "Spread", "Cache", "Label", "Measured"].map((h) => el("th", { text: h }))),
    ...run.measurements.map((m) => el("tr", {}, el("td", { text: TEST_NAMES[m.test] }), el("td", { text: m.volume }),
      el("td", { class: "num", text: speed(m.gbs) }), el("td", { class: "num", text: m.spread != null ? `±${Math.round(m.spread * 100)}%` : "" }),
      el("td", { text: m.cache }), el("td", { text: [m.label, m.partial ? "stopped early" : ""].filter(Boolean).join(", ") }),
      el("td", { class: "muted", text: m.measured ? new Date(m.measured).toLocaleString() : "" })))));
  const curves = run.measurements.filter((m) => m.curve.length > 1).map((m) =>
    el("div", {}, el("h3", { text: `Sustained write · ${m.volume}${m.label ? ` · ${m.label}` : ""}` }), lineChart(m.curve, "second", "var(--s2)")));
  return [legend, chart, ...curves, table];
}

function roundedBar(x, y, w, h) {
  const r = Math.min(4, w / 2);
  return `M${x},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h - r} Q${x + w},${y + h} ${x + w - r},${y + h} H${x} Z`;
}

function renderModels() {
  const { models, run } = state.view;
  const mounts = [...new Set(models.flatMap((m) => Object.keys(m.loads)))];
  const filter = el("input", { type: "text", placeholder: "Filter by name or store", "aria-label": "Filter models" });
  const body = el("tbody");
  const draw = () => {
    const q = filter.value.toLowerCase();
    body.replaceChildren(...models.filter((m) => !q || `${m.names.join(" ")} ${m.store}`.toLowerCase().includes(q)).slice(0, 500).map((m) =>
      el("tr", { class: m.duplicate ? "dup" : "" }, el("td", { text: m.names.join(", ") }), el("td", { class: "secondary", text: m.store }),
        el("td", { class: "num", text: size(m.bytes) }), el("td", { text: m.volume || "?" }),
        ...mounts.map((mount) => { const l = m.loads[mount]; const cell = el("td", { class: "num", text: l ? secs(l.seconds) : "–" });
          if (l) tip(cell, [secs(l.seconds), `${secs(l.low)}–${secs(l.high)} from ${mount}`, l.basis, "disk time only"]); return cell; }))));
  };
  filter.addEventListener("input", draw);
  draw();
  const measured = state.view.measured_volumes;
  return [el("div", { class: "card" }, el("div", { class: "row" }, el("h2", { text: `Models (${models.length})` }), filter),
    el("p", { class: "secondary", text: measured.length ? "Load times are disk time only, from each drive's measured read speed; a model server also spends time on GPU upload."
      : "No drive measured yet: load times use the link's expected speed. Run a benchmark for measured ones." }),
    el("div", { class: "scroll" }, el("table", {}, el("thead", {}, el("tr", {}, ...["Model", "Store", "Size", "On"].map((h, i) => el("th", { class: i === 2 ? "num" : "", text: h })),
      ...mounts.map((mo) => el("th", { class: "num", text: `Load from ${mo}` })))), body)),
    run.duplicates.length ? el("p", { class: "secondary", text: `${run.duplicates.length} file(s) are stored more than once; see Advice.` }) : null)];
}

function renderAdvice() {
  const { advice } = state.view;
  if (!advice.length) return [el("div", { class: "card" }, el("h2", { text: "No advice" }), el("p", { class: "secondary", text: "Nothing is full, slow or in the wrong place." }))];
  return [el("p", { class: "secondary", text: "hone-drive never runs these commands. Read them, then run what you agree with yourself." }),
    ...advice.map((a) => adviceCard(a, true))];
}

function adviceCard(a, full) {
  const os = state.view.run.machine.os;
  const lines = a.commands[os] || [];
  const label = { warn: "Warning", suggest: "Suggestion", info: "Note" }[a.severity];
  const gain = [a.gain.bytes_freed ? `frees ${size(a.gain.bytes_freed)}` : null,
    a.gain.seconds_saved_per_load ? `saves ${secs(a.gain.seconds_saved_per_load)} per load` : null].filter(Boolean).join(" · ");
  return el("div", { class: full ? "card advice" : "advice", style: full ? "" : "padding:10px 0;border-top:1px solid var(--grid)" },
    el("div", { class: "advice-head" }, el("span", { class: `status ${a.severity}`, text: label }), el("h3", { text: a.title }),
      gain ? el("span", { class: "pill", text: gain }) : null, el("span", { class: "muted", text: a.rule })),
    full ? el("ul", {}, ...a.why.map((w) => el("li", { text: w }))) : el("p", { class: "secondary", text: a.why[0] }),
    full && lines.length ? el("pre", {}, el("button", { class: "secondary-btn copy", text: "Copy",
      onclick: (e) => { navigator.clipboard.writeText(lines.join("\n")); e.target.textContent = "Copied"; } }), lines.join("\n")) : null,
    full && a.risks.length ? el("div", { class: "risks", text: `Risks: ${a.risks.join(" ")}` }) : null,
    full && a.cites.length ? el("div", { class: "muted", style: "font-size:12px", text: `Based on: ${a.cites.join(", ")}` }) : null);
}

function renderHistory() {
  const pickA = el("select", { "aria-label": "Older run" }, ...state.runs.map((r, i) => el("option", { value: r, selected: i === 1, text: r })));
  const pickB = el("select", { "aria-label": "Newer run" }, ...state.runs.map((r, i) => el("option", { value: r, selected: i === 0, text: r })));
  const out = el("div");
  const compare = async () => {
    const data = await api(`/compare?a=${encodeURIComponent(pickA.value)}&b=${encodeURIComponent(pickB.value)}`);
    out.replaceChildren(data.changes.length ? el("table", {}, el("tr", {}, ...["What", "", "Before", "After", ""].map((h) => el("th", { text: h }))),
      ...data.changes.map((c) => el("tr", {}, el("td", { text: c.kind }), el("td", { text: c.subject }), el("td", { text: c.before }),
        el("td", { text: c.after }), el("td", { class: "secondary", text: c.note }))))
      : el("p", { class: "muted", text: "No changes beyond the measurements' spread." }));
  };
  return [el("div", { class: "card" }, el("h2", { text: "Compare two runs" }),
    el("div", { class: "row" }, pickA, "→", pickB, el("button", { class: "secondary-btn", text: "Compare", onclick: compare })), out),
    el("div", { class: "card" }, el("h2", { text: `Runs (${state.runs.length})` }),
      el("table", {}, ...state.runs.map((r) => el("tr", {}, el("td", { text: r }),
        el("td", {}, el("button", { class: "secondary-btn", text: "Open", onclick: () => load(r) })),
        el("td", {}, el("a", { href: `/api/report/${r}?format=md`, target: "_blank", text: "Report to share" }))))))];
}

// -- start -------------------------------------------------------------------------------------------------

function go(screen) {
  state.screen = screen;
  if (location.hash !== `#${screen}`) history.replaceState(null, "", `#${screen}`);
  document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.screen === screen)));
  if (state.view) render();
}
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => go(b.dataset.screen)));
document.querySelector('#empty [data-action="scan"]').addEventListener("click", () => start("/scan", {}));
if (location.hash.slice(1) in { overview: 1, drives: 1, bench: 1, models: 1, advice: 1, history: 1 }) go(location.hash.slice(1));
if (!new URLSearchParams(location.search).has("still")) listen(); // ?still: no live updates (screenshots)
load().catch((error) => { state.job = { message: error.message }; renderJob(); });
