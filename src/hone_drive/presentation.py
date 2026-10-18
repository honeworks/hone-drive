"""Plain-language facts for the dashboard (design §10, change 0002): drive names people recognise,
verdicts with a tone, and the advice grouped into a plan of steps."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Literal

from hone_drive import text
from hone_drive.model import Advice, Drive, Run, Volume

Tone = Literal["good", "warn", "bad", "neutral"]
SMALL_VOLUME = 10e9  # boot and firmware partitions are not worth a card
KIND_LABELS = {
    "nvme": "NVMe SSD",
    "sata-ssd": "SATA SSD",
    "hdd": "hard disk",
    "usb-ssd": "USB SSD",
    "usb-hdd": "USB disk",
    "sd-card": "memory card",
    "virtual": "virtual disk",
    "other": "drive",
}


def drive_names(run: Run) -> dict[str, str]:
    """`Internal SSD` (numbered when there are several) and external drives by their model."""
    internal = [d for d in run.drives if not d.external]
    names: dict[str, str] = {}
    for index, drive in enumerate(internal, 1):
        names[drive.id] = "Internal drive" + (f" {index}" if len(internal) > 1 else "")
    for drive in run.drives:
        if drive.external:
            names[drive.id] = drive.model or f"External drive {drive.id}"
    return names


def volume_names(run: Run) -> dict[str, str]:
    """A name per mount point: its drive's name, with the label when a drive has several big volumes."""
    drives = drive_names(run)
    names: dict[str, str] = {}
    for volume in run.volumes:
        base = drives.get(volume.drive_id or "", volume.label or volume.mount)
        siblings = [v for v in run.volumes if v.drive_id == volume.drive_id and v.total_bytes >= SMALL_VOLUME]
        names[volume.mount] = f"{base} ({volume.label or volume.mount})" if len(siblings) > 1 else base
    return names


def space_tone(fraction: float) -> tuple[Tone, str]:
    if fraction >= 0.95:
        return "bad", "full"
    if fraction >= 0.85:
        return "bad", "almost full"
    if fraction >= 0.75:
        return "warn", "getting full"
    return "good", "plenty of room"


def connection(drive: Drive) -> dict[str, Any]:
    link = drive.link
    if link.limited:
        lanes = (
            f" on {link.negotiated_lanes} of {link.port_max_lanes} lanes"
            if link.negotiated_lanes and link.port_max_lanes and link.port_max_lanes > link.negotiated_lanes
            else ""
        )
        return {
            "tone": "warn",
            "title": "Not at full speed",
            "detail": f"Connected at {link.negotiated_gbps:g} Gbps{lanes}; this port can do "
            f"{link.port_max_gbps:g} Gbps.",
        }
    if link.negotiated_gbps:
        return {
            "tone": "good",
            "title": "Full speed for this port" if drive.external else "Full speed",
            "detail": f"{link.name or link.bus} · up to about {text.speed(link.expected_gbs)}",
        }
    reason = next(iter(link.unknown.values()), "the system doesn't report it")
    return {
        "tone": "neutral",
        "title": "Connection speed unknown",
        "detail": reason[:1].upper() + reason[1:] + ".",
    }


def speed(run: Run, volume: Volume) -> dict[str, Any] | None:
    """The drive's measured speeds and what they mean for a typical 20 GB model."""
    found = {m.test: m for m in run.measurements if m.volume == volume.mount and not m.partial}
    read = found.get("model-read") or found.get("seq-read")
    write = found.get("sustained-write") or found.get("seq-write")
    if read is None and write is None:
        return None
    return {
        "read": read.gbs if read else None,
        "write": write.gbs if write else None,
        "measured": (read or write).measured if (read or write) else None,  # type: ignore[union-attr]
        "cached": any(m.cache == "possibly-cached" for m in found.values()),
        "model_seconds": round(20e9 / (read.gbs * 1e9), 1) if read and read.gbs else None,
        "bound": read.label if read else None,
    }


# -- the plan ----------------------------------------------------------------------------------------------


@dataclass
class Step:
    key: str
    title: str
    intro: str
    tasks: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])

    @property
    def gain(self) -> int:
        return sum(t["gain"] or 0 for t in self.tasks)


STEPS = [  # (key, rules, title, intro); {ssd} is the external drive's name
    (
        "clean",
        ("A07", "A08"),
        "Clean up caches",
        "Safe to delete: the tools download what they need again. This is the quickest space you can get "
        "back.",
    ),
    (
        "prepare",
        ("A05", "A06"),
        "Prepare {ssd} for models",
        "Do this before moving anything: once models are on the drive, reformatting would erase them.",
    ),
    (
        "move",
        ("A02", "A09"),
        "Move big model folders to {ssd}",
        "Copy each folder, point its tool at the new place, check it works, then delete the old copy.",
    ),
    ("speed", ("A03",), "Get full speed from {ssd}", "Optional: the drive works as it is, just slower."),
    (
        "hidden",
        ("A10",),
        "Find the space no scan could see",
        "Measures folders only an administrator can read.",
    ),
]
DIAGNOSES = ("A01", "A04")


def plan(run: Run, advice: list[Advice]) -> dict[str, Any]:
    names = volume_names(run)
    ssd = next(
        (names[v.mount] for v in run.volumes if (d := run.drive(v.drive_id)) and d.external),
        "the external drive",
    )
    steps: list[Step] = []
    for key, rules, title, intro in STEPS:
        step = Step(key, title.format(ssd=ssd), intro)
        step.tasks = [_task(a, run.machine.os) for a in advice if a.rule in rules]
        if step.tasks:
            steps.append(step)
    findings = [_task(a, run.machine.os) for a in advice if a.rule in DIAGNOSES]
    return {
        "findings": findings,
        "steps": [
            {"key": s.key, "title": s.title, "intro": s.intro, "gain": s.gain, "tasks": s.tasks}
            for s in steps
        ],
        "gain": sum(s.gain for s in steps if s.key in ("clean", "move")),
    }


def _task(a: Advice, os_name: str) -> dict[str, Any]:
    key = hashlib.sha256(f"{a.rule}:{a.title}".encode()).hexdigest()[:10]
    return {
        "key": key,
        "rule": a.rule,
        "severity": a.severity,
        "title": a.title,
        "summary": a.why[0],
        "details": a.why[1:],
        "commands": a.commands.get(os_name, []),
        "risks": a.risks,
        "gain": a.gain.bytes_freed,
        "seconds": a.gain.seconds_saved_per_load,
        "cites": a.cites,
    }


def headline(run: Run, the_plan: dict[str, Any]) -> dict[str, Any]:
    names = volume_names(run)
    full = sorted(
        (v for v in run.volumes if v.total_bytes >= SMALL_VOLUME and v.used_fraction >= 0.85),
        key=lambda v: -v.used_fraction,
    )
    if full:
        v = full[0]
        gain = the_plan["gain"]
        return {
            "tone": "bad",
            "title": f"{names[v.mount]} is {text.percent(v.used_fraction)} full",
            "detail": f"Only {text.size(v.free_bytes)} left. "
            + (f"The plan below can free up to {text.size(gain)}." if gain else "See the plan below."),
            "action": "plan" if gain else None,
        }
    limited = [d for d in run.drives if d.link.limited]
    if limited:
        return {
            "tone": "warn",
            "title": f"{drive_names(run)[limited[0].id]} isn't running at full speed",
            "detail": connection(limited[0])["detail"],
            "action": "drives",
        }
    return {
        "tone": "good",
        "title": "Your drives look healthy",
        "detail": "No drive is nearly full and every connection runs at full speed.",
        "action": None,
    }
