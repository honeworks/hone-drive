"""Reports to share (design §13): Markdown or JSON from a run, with private details removed by default."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from hone_drive import links, text
from hone_drive.model import Run

UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b|\b[0-9A-F]{4}-[0-9A-F]{4}\b"
)


def redacted(run: Run, home: str | None = None) -> Run:
    """A copy of `run` without serial numbers, UUIDs, the host name, the user name and home path, and with
    paths outside the known places replaced by `<custom>`."""
    home = home or str(Path.home())
    user = Path(home).name
    data: dict[str, Any] = run.model_dump()
    for drive in data["drives"]:
        drive["serial"] = None
    for volume in data["volumes"]:
        volume["uuid"] = None
    for store in data["stores"]:
        if store["id"] == "custom":
            store["path"] = "<custom>"
            for item in store["items"]:
                item["path"] = "<custom>"
    data["machine"]["hostname"] = "<host>"
    raw = json.dumps(data)
    raw = raw.replace(json.dumps(home)[1:-1], "~")
    if len(user) > 2:
        raw = re.sub(rf"\b{re.escape(user)}\b", "<user>", raw)
    if len(run.machine.hostname) > 2:
        raw = re.sub(rf"\b{re.escape(run.machine.hostname)}\b", "<host>", raw)
    raw = UUID.sub("<uuid>", raw)
    return Run.model_validate_json(raw)


def markdown(run: Run) -> str:
    m = run.machine
    lines = [
        f"# hone-drive report ({run.created[:10]}, hone-drive {run.version})",
        "",
        f"{m.os_version} · {m.cpu or '?'} · RAM {text.size(m.ram_bytes)}"
        + "".join(f" · {g.name}" for g in m.gpus),
        "",
        "## Drives",
        "",
        "| Drive | Kind | Size | Link |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {d.model or d.id} | {d.kind}, {'external' if d.external else 'internal'} | "
        f"{text.size(d.size_bytes)} | {links.describe(d.link)}{' (limited)' if d.link.limited else ''} |"
        for d in run.drives
    ]
    lines += ["", "## Volumes", "", "| Volume | Filesystem | Used | Free |", "|---|---|---|---|"]
    lines += [
        f"| `{v.mount}` | {v.filesystem} | {text.size(v.used_bytes)} ({text.percent(v.used_fraction)}) | "
        f"{text.size(v.free_bytes)} |"
        for v in run.volumes
    ]
    if run.measurements:
        lines += [
            "",
            "## Measurements",
            "",
            "| Test | Volume | Speed | Cache | Label |",
            "|---|---|---|---|---|",
        ]
        lines += [
            f"| {x.test} | `{x.volume}` | {text.speed(x.gbs)} | {x.cache} | {x.label or ''} |"
            for x in run.measurements
        ]
    if run.stores:
        lines += ["", "## Stores", "", "| Store | Size | Volume |", "|---|---|---|"]
        lines += [
            f"| {s.title} | {text.size(s.bytes)} | `{s.volume or '?'}` |"
            for s in sorted(run.stores, key=lambda s: -(s.bytes or 0))
        ]
    if run.advice:
        lines += ["", "## Advice", ""] + [f"- **{a.rule}** ({a.severity}) {a.title}" for a in run.advice]
    return "\n".join(lines) + "\n"


def report(run: Run, fmt: str = "md", keep_private: bool = False, home: str | None = None) -> str:
    """The run as Markdown (`md`) or JSON (`json`) to share, private details removed unless asked."""
    shared = run if keep_private else redacted(run, home)
    return shared.model_dump_json(indent=1) if fmt == "json" else markdown(shared)
