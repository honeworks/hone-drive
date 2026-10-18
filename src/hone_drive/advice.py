"""Advice (design §8): plain functions over a run, one per rule, in a dict keyed by rule id. Each item
cites the measurements and facts it relies on and shows commands for this OS; none is ever run."""

from __future__ import annotations

from collections.abc import Callable

from hone_drive import links, text
from hone_drive.advice_stores import (
    a02_move_stores,
    a05_filesystem,
    a06_mount_at_boot,
    a07_caches,
    a08_duplicates,
    a09_keep_fast,
)
from hone_drive.model import Advice, Gain, Run, Severity

FULL = 0.85
Rule = Callable[[Run, str], list[Advice]]


def a01_full_volumes(run: Run, home: str) -> list[Advice]:
    found: list[Advice] = []
    for volume in run.volumes:
        if volume.used_fraction <= FULL or volume.total_bytes < 10e9:
            continue
        stores = sorted(
            (s for s in run.stores if s.volume == volume.mount and (s.bytes or 0) >= 1e9),
            key=lambda s: -(s.bytes or 0),
        )
        biggest = ", ".join(f"{s.title} {text.size(s.bytes)}" for s in stores[:4]) or "no known stores"
        to_80 = int(volume.used_bytes - 0.8 * volume.total_bytes)
        found.append(
            Advice(
                rule="A01",
                severity="warn",
                title=f"{volume.mount} is {text.percent(volume.used_fraction)} full",
                why=[
                    f"{text.size(volume.free_bytes)} free of {text.size(volume.total_bytes)}.",
                    f"The biggest stores on it: {biggest}.",
                    f"Freeing {text.size(to_80)} brings it under 80%; the advice below says what to move "
                    "or clean.",
                ],
                cites=[f"volume:{volume.mount}"],
                gain=Gain(bytes_freed=to_80),
            )
        )
    return found


def a03_limited_links(run: Run, home: str) -> list[Advice]:
    found: list[Advice] = []
    for drive in run.drives:
        if not drive.link.limited:
            continue
        mounts = [v.mount for v in run.volumes if v.drive_id == drive.id]
        reads = [m for m in run.measurements if m.volume in mounts and m.test in ("seq-read", "model-read")]
        why = [f"{drive.model or drive.id} is linked at {links.describe(drive.link)}."]
        if reads:
            why.append(
                f"It reads at {text.speed(reads[0].gbs)}, which is what this link carries."
                if reads[0].label == "link-bound"
                else f"It reads at {text.speed(reads[0].gbs)}."
            )
        why += [
            "Likely causes, most likely first: the cable (a 20 Gbps link needs a full-featured USB-C cable; "
            "use the one that came with the drive), an adapter or hub in between, the port, the drive's own "
            "mode.",
            "After changing something, scan again: the link is renegotiated when the drive is plugged in.",
        ]
        if "device_max_gbps" in drive.link.unknown:
            why.append("The drive does not report its own maximum, so it may already be at its limit.")
        found.append(
            Advice(
                rule="A03",
                severity="suggest",
                title=f"{drive.model or drive.id} could be faster on another cable or port",
                why=why,
                cites=[f"drive:{drive.id}", *[m.id for m in reads]],
                commands={
                    run.machine.os: [
                        "hone-drive scan",
                        f"hone-drive bench {mounts[0]}" if mounts else "hone-drive bench",
                    ]
                },
            )
        )
    return found


def a04_full_and_slow(run: Run, home: str) -> list[Advice]:
    found: list[Advice] = []
    for volume in run.volumes:
        drive = run.drive(volume.drive_id)
        writes = [
            m
            for m in run.measurements
            if m.volume == volume.mount and m.test in ("seq-write", "sustained-write")
        ]
        if drive is None or drive.external or drive.rotational or volume.used_fraction <= FULL or not writes:
            continue
        reads = [m for m in run.measurements if m.volume == volume.mount and m.test == "seq-read"]
        reference = reads[0].gbs if reads else drive.link.expected_gbs or 0
        if writes[0].gbs >= 0.3 * reference:
            continue
        found.append(
            Advice(
                rule="A04",
                severity="warn",
                title=f"{volume.mount} writes slowly because it is nearly full",
                why=[
                    f"It writes at {text.speed(writes[0].gbs)}"
                    + (f" but reads at {text.speed(reference)}." if reads else "."),
                    f"It is {text.percent(volume.used_fraction)} full. SSDs write through a fast cache "
                    "sized from free space; with little free space left, writes slow down.",
                    "Freeing space (advice A01) should bring the write speed back; keep at least 15 to 20% "
                    "free.",
                ],
                cites=[writes[0].id, *[m.id for m in reads[:1]], f"volume:{volume.mount}"],
            )
        )
    return found


def a10_unaccounted(run: Run, home: str) -> list[Advice]:
    found: list[Advice] = []
    for acc in run.accounting:
        if not acc.measure_command:
            continue
        found.append(
            Advice(
                rule="A10",
                severity="suggest",
                title=f"{text.size(acc.unaccounted_bytes)} of {acc.volume} could not be seen",
                why=[
                    "The walk could read less than the filesystem reports as used.",
                    "It may be in: " + "; ".join(acc.hints) + ".",
                    *(
                        [f"Folders it could not read include {', '.join(acc.unreadable[:5])}."]
                        if acc.unreadable
                        else []
                    ),
                    "The command below measures them; it needs administrator rights, so hone-drive never "
                    "runs it.",
                ],
                cites=[f"accounting:{acc.volume}"],
                commands={run.machine.os: [acc.measure_command]},
            )
        )
    return found


RULES: dict[str, Rule] = {
    "A01": a01_full_volumes,
    "A02": a02_move_stores,
    "A03": a03_limited_links,
    "A04": a04_full_and_slow,
    "A05": a05_filesystem,
    "A06": a06_mount_at_boot,
    "A07": a07_caches,
    "A08": a08_duplicates,
    "A09": a09_keep_fast,
    "A10": a10_unaccounted,
}
ORDER: dict[Severity, int] = {"warn": 0, "suggest": 1, "info": 2}


def advise(run: Run, home: str) -> list[Advice]:
    """Every rule's advice, ranked by severity and then by the space it frees (design §8.2)."""
    found = [item for rule in RULES.values() for item in rule(run, home)]
    return sorted(found, key=lambda a: (ORDER[a.severity], -(a.gain.bytes_freed or 0), a.rule))
