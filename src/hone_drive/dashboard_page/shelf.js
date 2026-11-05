// The model shelf on the Models screen (change 0003): pick models, see what would happen, then move them to
// a drive, bring them back, or remove them from a drive. The server checks everything; the page asks.

import { el, size, toast, api } from "./ui.js";
import { start } from "./app.js";

export const selected = new Set();

const VERBS = { move: "Move", restore: "Bring back", remove: "Remove from drive" };

// The bar above the table: what is selected and what can be done with it.
export function selectionBar(rows, shelf, redraw) {
  const picked = rows.filter((r) => r.key && selected.has(r.key));
  if (!picked.length) {
    return el("p", { class: "soft small", text: shelf.targets.length
      ? "Tick models to move them to a drive. They are copied, checked byte for byte, and only then removed from this computer."
      : "Plug in an external drive and scan again to move models to it." });
  }
  const total = picked.reduce((sum, r) => sum + r.bytes, 0);
  const actions = [];
  const here = picked.filter((r) => r.on_computer);
  if (here.length && shelf.targets.length) {
    const target = shelf.targets[0];
    actions.push(el("button", { class: "btn primary", "data-busy": "", text: `Move to ${target.name}`,
      onclick: () => openPlan("move", here.map((r) => r.key), target.mount, redraw) }));
  }
  const away = picked.filter((r) => !r.on_computer);
  if (away.length) {
    actions.push(el("button", { class: "btn", "data-busy": "", text: "Bring back",
      onclick: () => openPlan("restore", away.map((r) => r.key), null, redraw) }));
  }
  const onDrive = picked.filter((r) => r.on_drives.length);
  if (onDrive.length) {
    actions.push(el("button", { class: "btn", "data-busy": "", text: "Remove from drive",
      onclick: () => openRemove(onDrive, redraw) }));
  }
  return el("div", { class: "row selection" }, el("strong", { text: `${picked.length} selected · ${size(total)}` }),
    el("span", { class: "spacer" }), ...actions,
    el("button", { class: "btn link", text: "Clear", onclick: () => { selected.clear(); redraw(); } }));
}

// Where a model is: the computer, a drive, or both.
export function placeChips(row) {
  const chips = [];
  if (!row.on_computer) {
    chips.push(el("span", { class: `chip ${row.connected ? "neutral" : "warn"}`, text: row.connected ? "on drive" : "drive not connected" }));
  } else if (row.on_drives.length) {
    chips.push(el("span", { class: "chip good", text: `also on ${row.on_drives.join(", ")}` }));
  }
  return chips;
}

export function checkbox(row, redraw) {
  if (!row.key) return el("td", {});
  const box = el("input", { type: "checkbox", "aria-label": `Select ${row.names[0]}` });
  box.checked = selected.has(row.key);
  box.addEventListener("change", () => { box.checked ? selected.add(row.key) : selected.delete(row.key); redraw(); });
  return el("td", {}, box);
}

// -- the confirmation panel -----------------------------------------------------------------------------------

function panel(...children) {
  const node = el("section", { class: "card shelf-panel", role: "dialog", "aria-label": "Confirm" }, ...children);
  document.getElementById("shelf-panel").replaceChildren(node);
  node.scrollIntoView({ block: "nearest", behavior: "smooth" });
  return node;
}

function close() {
  document.getElementById("shelf-panel").replaceChildren();
}

async function openPlan(action, keys, to, redraw) {
  panel(el("p", { class: "soft", text: "Checking what can be done …" }));
  let plans;
  try {
    plans = (await api("/shelf/plan", { action, models: keys, to })).plans;
  } catch (error) {
    panel(el("p", { text: error.message }), el("button", { class: "btn", text: "Close", onclick: close }));
    return;
  }
  const items = plans.flatMap((p) => p.models);
  const ready = items.filter((i) => !i.refused);
  const where = plans.map((p) => p.drive).join(", ");
  const title = action === "move" ? `Move to ${where}` : `Bring back from ${where}`;
  const lines = items.map((i) => planRow(i, action));
  const lost = action === "move" ? ready.filter((i) => !i.replaceable) : [];
  const frees = ready.reduce((sum, i) => sum + i.bytes, 0);
  const summary = action === "move"
    ? `Frees ${size(frees)} on this computer. Each model is copied, read back from the drive and compared before it is removed here.`
    : "Each model is copied back and compared with its checksum. The drive keeps its copy.";
  panel(el("h2", { text: title }), el("p", { class: "soft small", text: summary }), el("div", { class: "stack" }, ...lines),
    lost.length ? el("div", { class: "note", text: `After the move, the drive holds the only copy of ${lost.map((i) => i.names[0]).join(", ")}, which can't be downloaded again. Keep the drive safe.` }) : null,
    el("div", { class: "row", style: "margin-top:14px" },
      el("button", { class: "btn primary", "data-busy": "", disabled: !ready.length, text: ready.length ? `${VERBS[action]} ${ready.length} model${ready.length > 1 ? "s" : ""}` : "Nothing can be done yet",
        onclick: () => { close(); selected.clear(); start(`/shelf/${action}`, { models: ready.map((i) => i.key), to }, `${VERBS[action]}: started`); redraw(); } }),
      items.some((i) => i.fix.length) ? el("button", { class: "btn", text: "Check again", onclick: () => openPlan(action, keys, to, redraw) }) : null,
      el("button", { class: "btn link", text: "Cancel", onclick: close })));
}

function planRow(item, action) {
  if (!item.refused) {
    const work = item.copy_bytes ? `copies ${size(item.copy_bytes)}` : action === "move" ? "already on the drive: only removed here" : "already here";
    return el("div", { class: "fact" }, el("span", { class: "dot good" }), el("span", { class: "what", text: item.names[0] }),
      el("span", { class: "soft small", text: size(item.bytes) }), el("span", { class: "detail", text: work }));
  }
  const row = el("div", { class: "fact" }, el("span", { class: "dot bad" }), el("span", { class: "what", text: item.names[0] }),
    el("span"), el("span", { class: "detail", text: item.refused }));
  if (!item.fix.length) return row;
  const text = item.fix.join("\n");
  return el("div", {}, row, el("p", { class: "small soft", text: "Run this once in a terminal (it needs your password), then press Check again. hone-drive never runs it itself." }),
    el("div", { class: "codebox" }, el("pre", { text }),
      el("button", { class: "btn small copy", text: "Copy", onclick: () => navigator.clipboard.writeText(text).then(() => toast("Copied")) })));
}

function openRemove(rows, redraw) {
  const only = rows.filter((r) => !r.on_computer);
  const word = only.length === 1 ? only[0].names[0] : String(only.length);
  const input = el("input", { type: "text", class: "confirm-input", "aria-label": "Type to confirm", placeholder: word });
  const go = el("button", { class: "btn primary danger", "data-busy": "", text: `Remove ${rows.length} from the drive`, disabled: only.length > 0,
    onclick: () => { close(); selected.clear(); start("/shelf/remove", { models: rows.map((r) => r.key), only_copy: only.length > 0 }, "Removing from the drive …"); redraw(); } });
  input.addEventListener("input", () => { go.disabled = input.value.trim() !== word; });
  panel(el("h2", { text: "Remove from the drive" }),
    el("p", { class: "soft small", text: "Frees space on the drive. The copies on this computer are not touched." }),
    only.length ? el("div", { class: "note", text: `${only.map((r) => r.names[0]).join(", ")} ${only.length > 1 ? "are" : "is"} only on the drive: removing ${only.length > 1 ? "them" : "it"} loses ${only.length > 1 ? "them" : "it"}. Bring ${only.length > 1 ? "them" : "it"} back first, or type ${only.length > 1 ? "the number of these models" : "its name"} to confirm.` }) : null,
    only.length ? input : null,
    el("div", { class: "row", style: "margin-top:14px" }, go, el("button", { class: "btn link", text: "Cancel", onclick: close })));
}
