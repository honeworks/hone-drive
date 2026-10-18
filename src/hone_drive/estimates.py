"""What measurements mean in seconds (design §7). Every estimate says what it is based on."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from hone_drive.model import Item, Measurement, Run, Store

Basis = Literal["measured model-read", "measured seq-read", "expected link speed", "measured read and write"]
DISK_ONLY = "disk time only: a model server also spends time on GPU upload and setup"


@dataclass(frozen=True)
class Estimate:
    seconds: float
    low: float
    high: float
    basis: Basis
    cites: list[str]
    note: str = DISK_ONLY


def read_speed(run: Run, mount: str) -> tuple[float, float, Basis, list[str]] | None:
    """(GB/s, relative spread, basis, measurement ids) for reading from `mount`; best evidence first."""

    def pick(test: str) -> list[Measurement]:
        return [
            m
            for m in run.measurements
            if m.volume == mount and m.test == test and m.cache != "possibly-cached" and not m.partial
        ]

    models = pick("model-read")
    if models:
        gbs = sum(m.bytes for m in models) / sum(m.seconds for m in models) / 1e9
        return gbs, max(m.spread or 0.0 for m in models), "measured model-read", [m.id for m in models]
    seq = pick("seq-read")
    if seq:
        return seq[-1].gbs, seq[-1].spread or 0.0, "measured seq-read", [seq[-1].id]
    volume = run.volume(mount)
    drive = run.drive(volume.drive_id if volume else None)
    if drive is not None and drive.link.expected_gbs:
        return drive.link.expected_gbs, 0.25, "expected link speed", []
    return None


def load_time(item: Item, mount: str, run: Run) -> Estimate | None:
    """Seconds to read `item` from `mount` (design §7); None without a size or any speed for the volume."""
    speed = read_speed(run, mount)
    if speed is None or not item.bytes:
        return None
    gbs, spread, basis, cites = speed
    seconds = item.bytes / (gbs * 1e9)
    spread = min(max(spread, 0.05), 0.5)
    return Estimate(
        round(seconds, 2), round(seconds * (1 - spread), 2), round(seconds * (1 + spread), 2), basis, cites
    )


def write_speed(run: Run, mount: str) -> tuple[float, list[str]] | None:
    for test in ("sustained-write", "seq-write"):
        found = [
            m
            for m in run.measurements
            if m.volume == mount and m.test == test and m.cache != "possibly-cached"
        ]
        if found:
            return found[-1].gbs, [found[-1].id]
    return None


def copy_time(store: Store, source: str, target: str, run: Run) -> Estimate | None:
    """Seconds to copy `store` from `source` to `target`: bytes over the slower of reading and writing."""
    read = read_speed(run, source)
    write = write_speed(run, target)
    if read is None or write is None or not store.bytes:
        return None
    gbs = min(read[0], write[0])
    seconds = store.bytes / (gbs * 1e9)
    return Estimate(
        round(seconds, 1),
        round(seconds * 0.8, 1),
        round(seconds * 1.3, 1),
        "measured read and write",
        read[3] + write[1],
        note="copy time: reading the source and writing the target",
    )


def warm(item: Item, run: Run, free_ram: int | None) -> bool:
    """True when the item fits in free memory, so a second load may come from the page cache (design §7)."""
    return bool(item.bytes and free_ram and item.bytes < free_ram)
