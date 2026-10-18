"""What changed between two runs (design §9): drives, links, fill levels and speeds beyond their spread."""

from __future__ import annotations

from dataclasses import dataclass

from hone_drive import links, text
from hone_drive.model import Run

MIN_SPREAD = 0.1  # speed changes under 10% are noise even when a run reports a tighter spread
USED_CHANGE = 1e9


@dataclass(frozen=True)
class Change:
    kind: str  # "drive", "link", "volume", "speed"
    subject: str
    before: str
    after: str
    note: str = ""


def compare(a: Run, b: Run) -> list[Change]:
    """Changes from run `a` to run `b`."""
    return _drives(a, b) + _volumes(a, b) + _speeds(a, b)


def _drives(a: Run, b: Run) -> list[Change]:
    changes: list[Change] = []
    before = {d.id: d for d in a.drives}
    after = {d.id: d for d in b.drives}
    for drive_id in sorted(set(before) | set(after)):
        old, new = before.get(drive_id), after.get(drive_id)
        if old is None and new is not None:
            changes.append(Change("drive", drive_id, "absent", new.model or drive_id, "added"))
        elif new is None and old is not None:
            changes.append(Change("drive", drive_id, old.model or drive_id, "absent", "removed"))
        elif (
            old is not None
            and new is not None
            and (old.link.negotiated_gbps, old.link.negotiated_lanes)
            != (new.link.negotiated_gbps, new.link.negotiated_lanes)
        ):
            changes.append(
                Change(
                    "link",
                    new.model or drive_id,
                    links.describe(old.link),
                    links.describe(new.link),
                    "renegotiated",
                )
            )
    return changes


def _volumes(a: Run, b: Run) -> list[Change]:
    changes: list[Change] = []
    for new in b.volumes:
        old = a.volume(new.mount)
        if old is not None and abs(new.used_bytes - old.used_bytes) >= USED_CHANGE:
            delta = new.used_bytes - old.used_bytes
            changes.append(
                Change(
                    "volume",
                    new.mount,
                    text.size(old.used_bytes),
                    text.size(new.used_bytes),
                    f"{'+' if delta > 0 else '-'}{text.size(abs(delta))} used",
                )
            )
    return changes


def _speeds(a: Run, b: Run) -> list[Change]:
    changes: list[Change] = []
    before = {m.id: m for m in a.measurements}
    for new in b.measurements:
        old = before.get(new.id)
        if old is None or not old.gbs:
            continue
        spread = max(old.spread or 0, new.spread or 0, MIN_SPREAD)
        ratio = new.gbs / old.gbs - 1
        if abs(ratio) > spread:
            changes.append(
                Change(
                    "speed",
                    new.id,
                    text.speed(old.gbs),
                    text.speed(new.gbs),
                    f"{ratio * 100:+.0f}% (beyond the ±{spread * 100:.0f}% spread)",
                )
            )
    return changes
