// Building blocks of the page: elements (text always through textContent), formatting, tooltips, API.

const TOKEN = document.querySelector('meta[name="hone-token"]').content;

export function el(tag, attrs = {}, ...children) {
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
    if (child === null || child === undefined || child === false || child === "") continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const SVG = "http://www.w3.org/2000/svg";
export function svg(tag, attrs = {}, ...children) {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "text") node.textContent = value;
    else if (key === "fill" || key === "stroke") node.style[key] = value; // var() works in style only
    else node.setAttribute(key, value);
  }
  children.flat().forEach((c) => c && node.append(c));
  return node;
}

export function size(n) {
  if (n === null || n === undefined) return "?";
  for (const [unit, scale] of [["TB", 1e12], ["GB", 1e9], ["MB", 1e6], ["kB", 1e3]]) {
    if (Math.abs(n) >= scale) { const v = n / scale; return `${v < 100 ? v.toFixed(1) : v.toFixed(0)} ${unit}`; }
  }
  return `${n} B`;
}
export const speed = (g) => (g === null || g === undefined ? "?" : g >= 1 ? `${g.toFixed(1)} GB/s` : `${Math.round(g * 1000)} MB/s`);
export const secs = (s) => (s === null || s === undefined ? "?" : s < 10 ? `${s.toFixed(1)} s` : s < 90 ? `${Math.round(s)} s`
  : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`);
export const pct = (f) => `${Math.round(f * 100)}%`;

export function ago(iso) {
  if (!iso) return "";
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 60 * 24) return `${Math.round(minutes / 60)} h ago`;
  return new Date(iso).toLocaleDateString();
}

// Stores keep one colour everywhere, in a fixed order (design decision D-016).
const SLOTS = ["ollama", "comfyui", "huggingface", "docker", "lmstudio", "llamacpp", "caches", "custom"];
export const storeColor = (id) => (SLOTS.includes(id) ? `var(--s${SLOTS.indexOf(id) + 1})` : "var(--other)");
export const storeOrder = (id) => (id === "other" ? 90 : id === "unaccounted" ? 99 : SLOTS.includes(id) ? SLOTS.indexOf(id) : 80);
export const seriesColor = (i) => `var(--s${(i % 3) + 1})`;

export const ICONS = { good: "✓", warn: "!", bad: "!", neutral: "i" };

export function icon(name) {
  const paths = {
    internal: "M4 6h16v12H4z M7 15h4 M17 15h.01",
    external: "M7 3h10v18H7z M10 18h4 M12 6v6",
  };
  return svg("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" }, svg("path", { d: paths[name] || paths.internal }));
}

const tooltip = () => document.getElementById("tooltip");
export function tip(target, lines) {
  const box = tooltip();
  const show = (event) => {
    box.replaceChildren(el("strong", { text: lines[0] }), ...lines.slice(1).filter(Boolean).map((l) => el("div", { class: "soft", text: l })));
    box.hidden = false;
    const rect = target.getBoundingClientRect();
    const x = event.clientX ?? rect.left + rect.width / 2;
    const y = event.clientY ?? rect.bottom;
    box.style.left = `${Math.max(8, Math.min(x + 12, window.innerWidth - 340))}px`;
    box.style.top = `${y + 14}px`;
  };
  target.addEventListener("pointermove", show);
  target.addEventListener("focus", show);
  target.addEventListener("pointerleave", () => (box.hidden = true));
  target.addEventListener("blur", () => (box.hidden = true));
  if (!target.hasAttribute("tabindex")) target.tabIndex = 0;
}

export function toast(message) {
  const node = el("div", { class: "toast", role: "status", text: message });
  document.body.append(node);
  setTimeout(() => node.remove(), 2200);
}

export async function api(path, body) {
  const options = body === undefined ? {} : {
    method: "POST", body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", "X-Hone-Drive-Token": TOKEN },
  };
  const response = await fetch(`/api${path}`, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

export const store = {
  get(key, fallback) { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode: not kept */ } },
};
