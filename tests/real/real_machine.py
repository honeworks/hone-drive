"""Helpers for the real-tool tests (`-m real`, change 0003): a run whose volumes are real folders, on the
filesystem they really are on, with one of them as the external drive.

Where the drive is: `HONE_DRIVE_REAL_DRIVE` (CI mounts an exFAT disk image there on Linux, macOS and
Windows), otherwise a folder next to the test's files. The tests run when `scripts/gpu-lock.sh` holds the
machine-wide lock (they load a model) or when `HONE_DRIVE_REAL=1` (CI runners have no one to disturb)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import psutil
import pytest

from hone_drive.filesystems import traits
from hone_drive.model import Drive, Link, Run, Volume
from hone_drive.scanner import os_name, scan
from hone_drive.stores import Context, Finder
from hone_drive.testing import FakeProbe, reference_probe
from hone_drive.volumes import disk_usage, mount_of

ROOT = Path(os.environ.get("HONE_DRIVE_REAL_ROOT") or Path.home() / ".cache" / "hone-drive-real-test")


def allowed() -> None:
    if not (os.environ.get("HONE_GPU_LOCK_HELD") or os.environ.get("HONE_DRIVE_REAL")):
        pytest.skip("run it through scripts/gpu-lock.sh (or with HONE_DRIVE_REAL=1 on CI)")


def drive_folder() -> Path:
    given = os.environ.get("HONE_DRIVE_REAL_DRIVE")
    folder = Path(given) / "hone-drive-real-test" if given else ROOT / "drive"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    return folder


def filesystem(path: Path) -> str:
    parts = psutil.disk_partitions(all=True)
    mount = mount_of(str(path.resolve()), [p.mountpoint for p in parts])
    return next((p.fstype for p in parts if p.mountpoint == mount), "unknown").lower()


def volume(path: Path, drive_id: str) -> Volume:
    total, used, free = disk_usage(str(path))
    fs = filesystem(path)
    return Volume(
        mount=str(path), device=str(path), drive_id=drive_id, filesystem=fs, total_bytes=total,
        used_bytes=used, free_bytes=free, traits=traits(fs, os_name()),
    )  # fmt: skip


def machine(internal: list[Path], drive: Path, finders: dict[str, Finder], ctx: Context) -> Callable[[], Run]:
    """A scan of these folders: `internal` on the computer's drive, `drive` the external one."""
    link = Link(bus="usb")
    drives = [
        Drive(id="int", kind="nvme", external=False, link=link),
        Drive(id="ext", kind="usb-ssd", external=True, link=link),
    ]
    volumes = [volume(p, "int") for p in internal] + [volume(drive, "ext")]
    facts = reference_probe().facts.model_copy(update={"os": os_name()})
    probe = FakeProbe(facts, drives, volumes)
    return lambda: scan(probe=probe, context=ctx, finders=finders)


def run_fix(lines: list[str]) -> None:
    """What the user would do with `check()`'s fix: run it. Only CI does this (it has sudo without a
    password, and the runner is thrown away); hone-drive itself never runs a fix."""
    if not os.environ.get("HONE_DRIVE_REAL_RUN_FIX"):
        pytest.skip(f"the fix is for the user to run: {lines}")
    shell = ["powershell", "-NoProfile", "-Command"] if sys.platform == "win32" else ["bash", "-c"]
    for line in lines:
        if not line.lstrip().startswith("#"):
            subprocess.run([*shell, line], check=True, timeout=300)
