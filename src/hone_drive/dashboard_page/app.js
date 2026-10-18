// hone-drive dashboard (design §10, change 0002): loading, the job bar, routing. Screens are in screens.js.

import { api, el, ago, speed, toast } from "./ui.js";
import { SCREENS, welcome } from "./screens.js";

const PHASES = {
  "seq-write": "Writing a test file", "seq-read": "Reading it back", "model-read": "Reading your model files",
  "random-read": "Random reads", "sustained-write": "Long write test",
};
export const state = { runs: [], view: null, job: { running: false }, screen: "overview", tick: null };

export async function load(runId = "latest") {
  const listing = await api("/runs");
  state.runs = listing.runs;
  state.job = listing.job;
  state.view = state.runs.length ? await api(`/runs/${runId}`) : null;
  render();
}

export async function start(path, body, message) {
  try {
    state.job = await api(path, body);
    state.tick = null;
    if (message) toast(message);
  } catch (error) {
    state.job = { running: false, failed: true, message: error.message };
  }
  renderJob();
}

export function go(screen) {
  location.hash = screen;
}

function route() {
  const name = location.hash.slice(1);
  state.screen = name in SCREENS ? name : "overview";
  for (const link of document.querySelectorAll(".nav a")) {
    if (link.dataset.screen === state.screen) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  render();
  window.scrollTo(0, 0);
}

function render() {
  const main = document.getElementById("main");
  main.replaceChildren(...(state.view ? SCREENS[state.screen](state) : [welcome(() => start("/scan", {}))]).filter(Boolean));
  renderStatus();
  renderJob();
}

function renderStatus() {
  const box = document.getElementById("scan-status");
  if (!state.view) { box.replaceChildren(); return; }
  box.replaceChildren(el("span", { text: `Scanned ${ago(state.view.run.created)}` }),
    el("button", { class: "btn small", "data-busy": "", text: "Scan again", onclick: () => start("/scan", {}) }));
}

export function volumeName(mount) {
  const all = (state.view?.drives || []).flatMap((d) => d.volumes);
  return all.find((v) => v.mount === mount)?.name || mount;
}

function renderJob() {
  const bar = document.getElementById("jobbar");
  const job = state.job || {};
  document.querySelectorAll("[data-busy]").forEach((b) => (b.disabled = !!job.running));
  if (!job.running && !job.failed) { bar.hidden = true; return; }
  bar.hidden = false;
  bar.classList.toggle("failed", !!job.failed);
  if (job.failed) {
    bar.replaceChildren(el("div", { class: "jobbar-inner" }, el("span", { class: "label", text: "Something went wrong:" }),
      el("span", { text: job.message }), el("span", { class: "spacer" }),
      el("button", { class: "btn small", text: "Dismiss", onclick: () => { state.job = {}; renderJob(); } })));
    return;
  }
  const t = job.kind === "bench" ? state.tick : null;
  const what = job.kind === "bench"
    ? (t ? `${PHASES[t.test] || t.test} on ${volumeName(t.volume)}` : "Preparing the speed test …")
    : `Scanning: ${job.message || "starting"} …`;
  const fraction = t ? t.done / Math.max(t.total, 1) : null;
  const fill = el("div", {});
  fill.style.width = fraction === null ? "100%" : `${Math.round(fraction * 100)}%`;
  fill.style.background = "var(--accent)";
  if (fraction === null) fill.style.opacity = "0.35";
  bar.replaceChildren(el("div", { class: "jobbar-inner" }, el("span", { class: "label", text: what }),
    el("div", { class: "progress" }, el("div", { class: "meter" }, fill)),
    t ? el("span", { class: "small soft", text: `${Math.round(fraction * 100)}% · ${speed(t.gbs)}` }) : null,
    el("button", { class: "btn small", text: "Stop", onclick: () => api("/stop", {}) })));
}

function listen() {
  const events = new EventSource("/api/events");
  events.addEventListener("job", (e) => { state.job = JSON.parse(e.data); state.tick = null; renderJob(); });
  events.addEventListener("progress", (e) => { state.job.message = JSON.parse(e.data).message; renderJob(); });
  events.addEventListener("tick", (e) => { state.tick = JSON.parse(e.data); renderJob(); });
  events.addEventListener("done", (e) => {
    const job = JSON.parse(e.data);
    state.job = {};
    state.tick = null;
    toast(job.kind === "bench" ? "Speed test finished" : "Scan finished");
    load();
  });
  events.addEventListener("failed", (e) => { state.job = { ...JSON.parse(e.data), failed: true }; renderJob(); });
}

window.addEventListener("hashchange", route);
if (!new URLSearchParams(location.search).has("still")) listen(); // ?still: no live updates (screenshots)
load().then(route).catch((error) => { state.job = { failed: true, message: error.message }; renderJob(); });
