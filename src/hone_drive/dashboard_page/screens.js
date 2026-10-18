// The six screens. Each takes the state and returns the nodes to show; all words come from the server's
// plain-language view (hone_drive/views.py) or from here, never raw identifiers.

import { el, svg, size, speed, secs, pct, ago, tip, toast, api, icon, store, storeColor, storeOrder, seriesColor, ICONS } from "./ui.js";
import { go, load, start } from "./app.js";

const SPEED_TEST = { tests: ["seq", "model"], size: "4G" };

// -- shared pieces ---------------------------------------------------------------------------------------

function pageTitle(title, text, ...actions) {
  return el("div", { class: "row" }, el("div", { class: "page-title" }, el("h1", { text: title }), text ? el("p", { text }) : null),
    el("span", { class: "spacer" }), ...actions);
}

function card(...children) {
  return el("section", { class: "card" }, ...children);
}

function heading(title, text, ...actions) {
  return el("div", { class: "section-head" }, el("div", {}, el("h2", { text: title }), text ? el("p", { class: "soft small", text }) : null),
    el("div", { class: "row" }, ...actions));
}

function meter(fraction, tone) {
  const fill = el("div", { class: tone });
  fill.style.width = `${Math.min(100, Math.round(fraction * 100))}%`;
  return el("div", { class: "meter", role: "img", "aria-label": `${pct(fraction)} used` }, fill);
}

function fact(tone, what, detail, extra) {
  return el("div", { class: "fact" }, el("span", { class: `dot ${tone}` }), el("span", { class: "what", text: what }),
    extra || el("span"), detail ? el("span", { class: "detail", text: detail }) : null);
}

function testButton(drive, label = "Test speed") {
  return el("button", {
    class: "btn small", "data-busy": "", text: label, title: "Writes a 4 GB test file, reads it back and reads your largest model files. About a minute; the file is deleted afterwards.",
    onclick: () => start("/bench", { folders: [drive.test_folder], ...SPEED_TEST }, `Testing ${drive.name} …`),
  });
}

// -- the drive card (Overview and Drives) ------------------------------------------------------------------

function spaceFact(volume) {
  const f = fact(volume.tone, `${size(volume.free)} free of ${size(volume.total)}`, null,
    el("span", { class: `chip ${volume.tone}`, text: `${pct(volume.fraction)} full` }));
  f.append(meter(volume.fraction, volume.tone));
  return f;
}

function speedFact(drive) {
  const s = drive.speed;
  if (!s) {
    return fact("neutral", "Speed not tested yet", "Test it to see how fast models load from this drive.",
      drive.test_folder ? testButton(drive) : null);
  }
  const parts = [s.read ? `Reads ${speed(s.read)}` : null, s.write ? `writes ${speed(s.write)}` : null].filter(Boolean).join(" · ");
  const detail = s.model_seconds ? `A 20 GB model loads in about ${secs(s.model_seconds)}.` : "";
  const tone = s.read && s.read < 0.3 ? "warn" : "good";
  return fact(tone, parts, `${detail} Tested ${ago(s.measured)}.`, drive.test_folder ? testButton(drive, "Test again") : null);
}

function driveCard(drive, compact) {
  const main = drive.volumes.find((v) => v.mount === drive.mount);
  const facts = [main ? spaceFact(main) : fact("neutral", "Not mounted", "No volume of this drive is mounted.")];
  facts.push(fact(drive.connection.tone, drive.connection.title, drive.connection.detail));
  if (main) facts.push(speedFact(drive));
  if (!compact && main && main.missing.length) {
    const cannot = main.missing.filter((m) => m !== "a journal");
    const text = [cannot.length ? `It can't store ${cannot.join(", ").replace(/, ([^,]*)$/, " or $1")}.` : "",
      main.missing.includes("a journal") ? "No journal: unplugging it while writing can corrupt files." : ""].filter(Boolean).join(" ");
    facts.push(fact(main.missing.includes("file owners") ? "warn" : "neutral", `Formatted as ${main.filesystem}`, text));
  }
  if (!compact && main && main.at_boot === false) {
    facts.push(fact("neutral", "Mounted when you log in", "Services that start before login (Ollama, Docker) won't see it."));
  }
  return el("article", { class: "card drive" },
    el("div", { class: "drive-head" }, el("div", { class: "drive-icon" }, icon(drive.external ? "external" : "internal")),
      el("div", {}, el("h2", { text: drive.name }),
        el("p", { class: "muted small", text: [drive.model !== drive.name ? drive.model : null, `${size(drive.size)} ${drive.kind}`].filter(Boolean).join(" · ") }))),
    el("div", { class: "facts" }, ...facts));
}

// -- Overview --------------------------------------------------------------------------------------------------

function overview(state) {
  const v = state.view;
  const h = v.headline;
  const cta = h.action === "plan" ? el("button", { class: "btn primary", text: "See the plan", onclick: () => go("plan") })
    : h.action === "drives" ? el("button", { class: "btn", text: "See why", onclick: () => go("drives") }) : null;
  const hero = el("section", { class: "card hero" }, el("div", { class: `icon ${h.tone}`, "aria-hidden": "true", text: ICONS[h.tone] }),
    el("div", {}, el("h1", { text: h.title }), el("p", { text: h.detail })), cta);
  const drives = el("div", { class: "cards" }, ...v.drives.filter((d) => d.mount).map((d) => driveCard(d, true)));
  const untested = v.drives.filter((d) => d.mount && !d.speed);
  const biggest = card(heading("What's using the most space", null, el("button", { class: "btn link", text: "All space →", onclick: () => go("space") })),
    rankList(v.biggest.slice(0, 6)));
  const planCard = card(heading("Your plan", v.plan.gain ? `Can free up to ${size(v.plan.gain)}` : null,
    el("button", { class: "btn link", text: "Open the plan →", onclick: () => go("plan") })), planSummary(v.plan));
  return [hero,
    el("div", {}, heading("Your drives", untested.length > 1 ? "Test their speed to see how fast your models would load from each." : null,
      untested.length > 1 ? el("button", { class: "btn", "data-busy": "", text: "Test all drives (≈ 2 min)",
        onclick: () => start("/bench", { folders: untested.map((d) => d.test_folder), ...SPEED_TEST }, "Testing your drives …") }) : null), drives),
    el("div", { class: "cards" }, planCard, biggest)];
}

function planSummary(plan) {
  if (!plan.steps.length) return el("p", { class: "soft", text: "Nothing to do: no drive is full and every connection runs at full speed." });
  const done = store.get("hone-drive-done", {});
  return el("div", { class: "stack" }, ...plan.steps.map((step, i) => {
    const finished = step.tasks.filter((t) => done[t.key]).length;
    return el("div", { class: "plan-row" }, el("span", { class: `step-num ${finished === step.tasks.length ? "done" : ""}`, text: finished === step.tasks.length ? "✓" : String(i + 1) }),
      el("div", {}, el("div", { class: "task-title", text: step.title }),
        el("div", { class: "small muted", text: `${finished} of ${step.tasks.length} done` })),
      step.gain ? el("span", { class: "chip gain", text: `frees ${size(step.gain)}` }) : null);
  }));
}

function rankList(rows) {
  if (!rows.length) return el("p", { class: "soft", text: "No model stores or caches found." });
  const max = Math.max(...rows.map((r) => r.bytes));
  return el("div", { class: "rank" }, ...rows.map((r) => {
    const fill = el("div", {});
    fill.style.width = `${(100 * r.bytes) / max}%`;
    fill.style.background = storeColor(r.store);
    const row = el("div", { class: "rank-row" }, el("div", {}, el("div", { class: "name", text: r.name }), el("div", { class: "sub", text: `${r.kind} · ${r.where}` })),
      el("div", { class: "rank-bar" }, fill), el("div", { class: "num", text: size(r.bytes) }));
    tip(row, [size(r.bytes), r.name, `${r.kind} on ${r.where}`]);
    return row;
  }));
}

// -- Plan --------------------------------------------------------------------------------------------------------

function plan(state) {
  const p = state.view.plan;
  const done = store.get("hone-drive-done", {});
  const findings = p.findings.length ? card(heading("What we found"), el("div", { class: "stack" }, ...p.findings.map((f) =>
    el("div", { class: "finding" }, el("div", { class: `icon ${f.severity === "warn" ? "bad" : "warn"}`, text: "!" }),
      el("div", {}, el("h3", { text: f.title }), el("p", { text: [f.summary, ...f.details].join(" ") })))))) : null;
  const steps = p.steps.map((step, i) => stepCard(step, i + 1, done));
  return [pageTitle("Your plan", "Do the steps in order. hone-drive never runs anything itself: open a step, read what it does, copy the commands into a terminal, then tick it off."),
    findings, ...steps,
    steps.length ? null : card(el("p", { class: "soft", text: "Nothing to do right now." }))];
}

function stepCard(step, number, done) {
  const finished = step.tasks.filter((t) => done[t.key]).length;
  const all = finished === step.tasks.length;
  return el("section", { class: "card step" },
    el("div", { class: "step-head" }, el("span", { class: `step-num ${all ? "done" : ""}`, text: all ? "✓" : String(number) }),
      el("div", {}, el("h2", { text: step.title }), el("p", { text: step.intro })),
      el("div", { class: "row" }, step.gain ? el("span", { class: "chip gain", text: `frees ${size(step.gain)}` }) : null,
        el("span", { class: "chip neutral", text: `${finished}/${step.tasks.length} done` }))),
    el("div", { class: "tasks" }, ...step.tasks.map((t) => taskRow(t, done))));
}

function taskRow(task, done) {
  const box = el("input", { type: "checkbox", "aria-label": `Mark “${task.title}” as done`, checked: !!done[task.key] });
  const row = el("div", { class: `task ${done[task.key] ? "done" : ""}` });
  box.addEventListener("change", () => {
    const now = store.get("hone-drive-done", {});
    if (box.checked) now[task.key] = true; else delete now[task.key];
    store.set("hone-drive-done", now);
    row.classList.toggle("done", box.checked);
  });
  const info = task.gain ? `frees ${size(task.gain)}` : task.seconds ? `${secs(task.seconds)} faster per load` : null;
  const parts = [];
  if (task.details.length) parts.push(el("ul", {}, ...task.details.map((d) => el("li", { text: d }))));
  if (task.commands.length) {
    const text = task.commands.join("\n");
    parts.push(el("div", { class: "codebox" }, el("pre", { text }),
      el("button", { class: "btn small copy", text: "Copy", onclick: () => navigator.clipboard.writeText(text).then(() => toast("Copied")) })));
  }
  if (task.risks.length) parts.push(el("div", { class: "note", text: task.risks.join(" ") }));
  row.append(el("div", { class: "task-head" }, box, el("div", {}, el("div", { class: "task-title", text: task.title }),
      el("div", { class: "task-summary", text: task.summary })), info ? el("span", { class: "chip gain", text: info }) : el("span")),
    parts.length ? el("details", {}, el("summary", { text: task.commands.length ? "How to do it" : "Why" }), ...parts) : null);
  return row;
}

// -- Space ------------------------------------------------------------------------------------------------------

function space(state) {
  const v = state.view;
  const cards = v.volumes.map((volume) => {
    const segments = [...volume.segments].sort((a, b) => storeOrder(a.kind) - storeOrder(b.kind));
    const bar = el("div", { class: "stackbar", role: "img", "aria-label": `${volume.name}: ${size(volume.used)} used of ${size(volume.total)}` });
    for (const s of segments) {
      const seg = el("div", { class: `seg ${s.kind === "unaccounted" ? "hatched" : ""}` });
      seg.style.width = `${(100 * s.bytes) / volume.total}%`;
      if (s.kind !== "unaccounted") seg.style.background = storeColor(s.kind);
      tip(seg, [size(s.bytes), s.label, `${pct(s.bytes / volume.total)} of the drive`]);
      bar.append(seg);
    }
    const legend = el("table", { class: "legend-table" }, ...segments.map((s) => {
      const sw = el("span", { class: `swatch ${s.kind === "unaccounted" ? "hatched" : ""}` });
      if (s.kind !== "unaccounted") sw.style.background = storeColor(s.kind);
      return el("tr", {}, el("td", {}, sw, s.label), el("td", { class: "num", text: size(s.bytes) }),
        el("td", { class: "num muted", text: pct(s.bytes / volume.total) }));
    }), el("tr", {}, el("td", {}, el("span", { class: "swatch", style: "background:var(--track);border:1px solid var(--axis)" }), "Free"),
      el("td", { class: "num", text: size(volume.free) }), el("td", { class: "num muted", text: pct(volume.free / volume.total) })));
    return card(heading(volume.name, `${size(volume.used)} used of ${size(volume.total)}`,
      el("span", { class: `chip ${volume.tone}`, text: `${pct(volume.fraction)} full · ${volume.tone_label}` })), bar, legend);
  });
  const actions = card(heading("Look deeper", "Both take a few minutes and only read files."),
    el("div", { class: "stack" },
      el("div", { class: "row" }, el("div", {}, el("h3", { text: "Find hidden space" }),
        el("p", { class: "soft small", text: v.accounted ? "Done for this scan: hidden space shows as a hatched part of the bar." : "Walks every folder you can read and shows how much space is in folders only an administrator can open." })),
        el("span", { class: "spacer" }), el("button", { class: "btn", "data-busy": "", text: "Find hidden space", onclick: () => start("/scan", { accounting: true }, "Looking for hidden space …") })),
      el("div", { class: "row" }, el("div", {}, el("h3", { text: "Find duplicate files" }),
        el("p", { class: "soft small", text: "Finds model files stored twice by different tools (byte-identical copies only)." })),
        el("span", { class: "spacer" }), el("button", { class: "btn", "data-busy": "", text: "Find duplicates", onclick: () => start("/scan", { duplicates: true }, "Looking for duplicates …") }))));
  return [pageTitle("Space", "What fills each drive. Colours stay the same on every screen: blue is always Ollama, orange ComfyUI."),
    ...cards, card(heading("The biggest single things"), rankList(v.biggest)), actions];
}

// -- Drives & speed -------------------------------------------------------------------------------------------

function drives(state) {
  const v = state.view;
  const mounted = v.drives.filter((d) => d.mount);
  const tested = mounted.filter((d) => d.speed);
  const compare = tested.length ? card(heading("Speed compared", "Measured on this computer, with the system's cache bypassed so the numbers are the drive's, not memory's."),
    speedChart(tested)) : null;
  return [pageTitle("Drives & speed", "How each drive is connected, how fast it really is, and what that means for loading models.",
    el("button", { class: "btn primary", "data-busy": "", text: "Test all drives (≈ 2 min)",
      onclick: () => start("/bench", { folders: mounted.map((d) => d.test_folder), ...SPEED_TEST }, "Testing your drives …") })),
  compare, el("div", { class: "cards" }, ...v.drives.map((d) => driveDetails(d, v)))];
}

function speedChart(tested) {
  const metrics = [["read", "Read"], ["write", "Write"]];
  const w = 760, rowH = 26, labelW = 90, gap = 16;
  const max = Math.max(...tested.flatMap((d) => [d.speed.read || 0, d.speed.write || 0])) * 1.18;
  let y = 6;
  const marks = [];
  for (const [key, label] of metrics) {
    marks.push(svg("text", { class: "label", x: 0, y: y + 17, text: label }));
    tested.forEach((d, i) => {
      const value = d.speed[key];
      if (value) {
        const bw = Math.max(3, ((w - labelW - 110) * value) / max);
        const r = Math.min(4, bw / 2);
        const bar = svg("path", { fill: seriesColor(i), d: `M${labelW},${y + 4} H${labelW + bw - r} Q${labelW + bw},${y + 4} ${labelW + bw},${y + 4 + r} V${y + 20 - r} Q${labelW + bw},${y + 20} ${labelW + bw - r},${y + 20} H${labelW} Z` });
        tip(bar, [speed(value), `${label} · ${d.name}`]);
        marks.push(bar, svg("text", { class: "value", x: labelW + bw + 8, y: y + 17, text: speed(value) }));
      }
      y += rowH;
    });
    y += gap;
  }
  const chart = svg("svg", { viewBox: `0 0 ${w} ${y}`, class: "chart", role: "img", "aria-label": "Read and write speed per drive" },
    svg("line", { class: "axis", x1: labelW, x2: labelW, y1: 0, y2: y - gap }), ...marks);
  const legend = el("div", { class: "legend" }, ...tested.map((d, i) => {
    const sw = el("span", { class: "swatch" });
    sw.style.background = seriesColor(i);
    return el("span", {}, sw, d.name);
  }));
  return el("div", { class: "stack" }, legend, chart);
}

function driveDetails(drive, view) {
  const node = driveCard(drive, false);
  const ms = view.measurements.filter((m) => drive.volumes.some((v) => v.mount === m.volume));
  const names = { "seq-write": "Write", "seq-read": "Read", "model-read": "Read model files", "random-read": "Random reads", "sustained-write": "Long write" };
  const extra = el("details", { class: "more" }, el("summary", { text: "Technical details" }),
    el("dl", { class: "kv" }, ...[
      ["Connection", drive.link.name || drive.link.bus], ["Negotiated", drive.link.negotiated_gbps ? `${drive.link.negotiated_gbps} Gbps${drive.link.negotiated_lanes ? `, ${drive.link.negotiated_lanes} lane(s)` : ""}` : "unknown"],
      ["Port can do", drive.link.port_max_gbps ? `${drive.link.port_max_gbps} Gbps${drive.link.port_max_lanes ? `, ${drive.link.port_max_lanes} lane(s)` : ""}` : "unknown"],
      ["Volumes", drive.volumes.map((v) => `${v.mount} (${v.filesystem}, ${size(v.total)})`).join("; ") || "none"],
      ...ms.map((m) => [names[m.test] || m.test, `${speed(m.gbs)}${m.label ? ` · ${m.label.replace("-", " ")}` : ""}${m.cache !== "direct" ? ` · cache: ${m.cache}` : ""}${m.iops ? ` · ${Math.round(m.iops).toLocaleString()} reads/s` : ""}`]),
      ...drive.unknown.map((reason) => ["Unknown", reason]),
    ].flatMap(([k, val]) => [el("dt", { text: k }), el("dd", { text: val })])));
  node.append(extra);
  return node;
}

// -- Models ----------------------------------------------------------------------------------------------------

function models(state) {
  const m = state.view.models;
  const stores = [...new Set(m.rows.map((r) => r.store))];
  let filter = "", only = null;
  const body = el("tbody");
  const maxLoad = Math.max(1, ...m.rows.flatMap((r) => Object.values(r.loads)));
  const draw = () => {
    const rows = m.rows.filter((r) => (!only || r.store === only) && (!filter || r.names.join(" ").toLowerCase().includes(filter)));
    body.replaceChildren(...rows.slice(0, 400).map((r) => el("tr", {},
      el("td", {}, el("div", { text: r.names[0] }), r.names.length > 1 ? el("div", { class: "small muted", text: `also ${r.names.slice(1).join(", ")}` }) : null,
        r.duplicate ? el("span", { class: "chip warn", text: "stored twice" }) : null),
      el("td", { class: "soft nowrap", text: r.store }), el("td", { class: "num", text: size(r.bytes) }), el("td", { class: "soft nowrap", text: r.where }),
      ...m.columns.map((c) => {
        const s = r.loads[c.mount];
        if (s === undefined) return el("td", { class: "muted", text: "–" });
        const fill = el("div", {});
        fill.style.width = `${Math.max(2, (100 * s) / maxLoad)}%`;
        fill.style.background = "var(--accent)";
        const cell = el("td", {}, el("div", { class: "loadcell" }, el("div", { class: "rank-bar" }, fill), el("span", { class: "num", text: secs(s) })));
        tip(cell, [secs(s), `to load from ${c.name}`, c.measured ? "from its measured speed" : "estimated from its connection: test its speed for a real number", "disk time only"]);
        return cell;
      }))));
    count.textContent = `${rows.length} of ${m.rows.length}`;
  };
  const count = el("span", { class: "muted small" });
  const search = el("input", { type: "search", placeholder: "Search models", "aria-label": "Search models", oninput: (e) => { filter = e.target.value.toLowerCase(); draw(); } });
  const pills = ["All", ...stores].map((s) => el("button", { class: "pill", "aria-pressed": String(s === "All"), text: s, onclick: (e) => {
    only = s === "All" ? null : s;
    e.target.parentElement.querySelectorAll(".pill").forEach((p) => p.setAttribute("aria-pressed", String(p === e.target)));
    draw();
  } }));
  draw();
  const unmeasured = m.columns.filter((c) => !c.measured);
  return [pageTitle("Models", "Every model on this computer, and how long it takes to load from each drive. Loading is the only thing a slower drive changes: once loaded, a model runs just as fast."),
    unmeasured.length ? el("div", { class: "note", text: `Times for ${unmeasured.map((c) => c.name).join(" and ")} are estimates from the connection. Test their speed on the Drives screen for real numbers.` }) : null,
    card(el("div", { class: "filters" }, search, el("div", { class: "row" }, ...pills), el("span", { class: "spacer" }), count),
      el("div", { class: "scroll", style: "margin-top:12px" }, el("table", { class: "data" },
        el("thead", {}, el("tr", {}, ...["Model", "Tool", "Size", "Stored on"].map((h, i) => el("th", { class: i === 2 ? "num" : "", text: h })),
          ...m.columns.map((c) => el("th", { text: `Load time from ${c.name}` })))), body)))];
}

// -- History --------------------------------------------------------------------------------------------------

function history(state) {
  const label = (id) => { const d = id.slice(0, 15); return `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)} ${d.slice(9, 11)}:${d.slice(11, 13)} UTC`; };
  const pickA = el("select", { "aria-label": "Earlier scan" }, ...state.runs.map((r, i) => el("option", { value: r, selected: i === Math.min(1, state.runs.length - 1), text: label(r) })));
  const pickB = el("select", { "aria-label": "Later scan" }, ...state.runs.map((r, i) => el("option", { value: r, selected: i === 0, text: label(r) })));
  const out = el("div", { style: "margin-top:12px" });
  const compare = async () => {
    const data = await api(`/compare?a=${encodeURIComponent(pickA.value)}&b=${encodeURIComponent(pickB.value)}`);
    const words = { drive: "Drive", link: "Connection", volume: "Space used", speed: "Speed" };
    out.replaceChildren(data.changes.length ? el("table", { class: "data" }, el("tr", {}, ...["", "What", "Before", "After", ""].map((h) => el("th", { text: h }))),
      ...data.changes.map((c) => el("tr", {}, el("td", { text: words[c.kind] || c.kind }), el("td", { text: c.subject }), el("td", { text: c.before }),
        el("td", { text: c.after }), el("td", { class: "soft", text: c.note }))))
      : el("p", { class: "soft", text: "Nothing changed beyond normal measurement noise." }));
  };
  return [pageTitle("History", "Every scan is kept on this computer. Compare two to see what changed after switching a cable, a port or freeing space."),
    card(heading("Compare two scans"), el("div", { class: "row" }, pickA, el("span", { text: "→" }), pickB,
      el("button", { class: "btn", text: "Compare", onclick: compare })), out),
    card(heading("All scans", "A report has serial numbers, user and computer names removed, so you can share it."),
      el("table", { class: "data" }, ...state.runs.map((r, i) => el("tr", {}, el("td", { text: label(r) }), el("td", { class: "muted", text: i === 0 ? "latest" : "" }),
        el("td", {}, el("button", { class: "btn small", text: "Open", onclick: () => load(r).then(() => go("overview")) })),
        el("td", {}, el("a", { href: `/api/report/${r}?format=md`, target: "_blank", text: "Report to share" }))))))];
}

// -- first run ---------------------------------------------------------------------------------------------------

export function welcome(scan) {
  return el("section", { class: "card welcome" }, el("div", { class: "logo", style: "width:48px;height:48px;border-radius:14px" }),
    el("h1", { text: "Let's look at your drives" }),
    el("p", { text: "hone-drive checks what fills your disks and how fast each drive really is, then makes a plan. It only reads: nothing is moved, deleted or changed." }),
    el("ul", {}, el("li", { text: "Which model folders, caches and Docker data take the space" }),
      el("li", { text: "How each drive is connected, and whether a cable is slowing it down" }),
      el("li", { text: "How long your models take to load from each drive" })),
    el("button", { class: "btn primary", "data-busy": "", text: "Scan this computer (≈ 30 s)", onclick: scan }));
}

export const SCREENS = { overview, plan, space, drives, models, history };
