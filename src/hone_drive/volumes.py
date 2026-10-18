"""Mounted volumes on every OS, from psutil and `shutil.disk_usage` (design §3.2, decision D-001)."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePath

from hone_drive.filesystems import traits
from hone_drive.model import Volume


@dataclass(frozen=True)
class Partition:
    device: str
    mount: str
    filesystem: str
    options: str


Partitions = Callable[[], list[Partition]]
Usage = Callable[[str], tuple[int, int, int]]  # mount -> (total, used, free for a user)

# Pseudo, read-only image and container filesystems: not places anyone stores data on purpose.
SKIP_FILESYSTEMS = {
    "squashfs",
    "tmpfs",
    "devtmpfs",
    "overlay",
    "proc",
    "sysfs",
    "efivarfs",
    "autofs",
    "nsfs",
    "iso9660",
    "udf",
    "devfs",
    "nullfs",
    "ramfs",
    "fuse.portal",
    "fuse.gvfsd-fuse",
    "",
}
SKIP_MOUNT_PREFIXES = (
    "/snap/",
    "/var/lib/docker/",
    "/var/snap/",
    "/System/Volumes/",
    "/System/Library/",
    "/private/var/vm",
    "/Library/Developer/CoreSimulator/",
)


def psutil_partitions() -> list[Partition]:
    import psutil  # noqa: PLC0415 - imported where it is used so the tests can run without probing

    return [Partition(p.device, p.mountpoint, p.fstype, p.opts) for p in psutil.disk_partitions(all=False)]


def disk_usage(mount: str) -> tuple[int, int, int]:
    usage = shutil.disk_usage(mount)
    return usage.total, usage.used, usage.free


def list_volumes(os_name: str, partitions: Partitions, usage: Usage) -> list[Volume]:
    """Every local volume once (bind mounts and repeated mounts of one device collapse to the shortest)."""
    found: dict[str, Volume] = {}
    for part in sorted(partitions(), key=lambda p: (len(p.mount), p.mount)):
        if _skipped(part) or part.device in found:
            continue
        try:
            total, used, free = usage(part.mount)
        except OSError:
            continue  # an empty card reader or a disconnected drive: nothing mounted to report
        options = {o.strip() for o in part.options.split(",")}
        found[part.device] = Volume(
            mount=part.mount,
            device=part.device,
            filesystem=part.filesystem.lower(),
            total_bytes=total,
            used_bytes=used,
            free_bytes=free,
            read_only="ro" in options or "read-only" in options,
            traits=traits(part.filesystem, os_name),
        )
    return sorted(found.values(), key=lambda v: v.mount)


def _skipped(part: Partition) -> bool:
    if part.filesystem.lower() in SKIP_FILESYSTEMS or "cdrom" in part.options:
        return True
    if part.device.startswith("/dev/loop"):
        return True
    return any(part.mount.startswith(prefix) for prefix in SKIP_MOUNT_PREFIXES)


def mount_of(path: str, mounts: list[str]) -> str | None:
    """The mount point holding `path` (the longest one that contains it)."""
    target = PurePath(path)
    best: str | None = None
    for mount in mounts:
        root = PurePath(mount)
        if (target == root or root in target.parents) and (best is None or len(mount) > len(best)):
            best = mount
    return best
