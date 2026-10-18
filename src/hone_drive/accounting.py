"""Unaccounted space (design §5.4): walk a volume as the user, compare what was readable with what the
filesystem reports used, and say where the rest is likely hiding."""

from __future__ import annotations

import os
import time
from collections.abc import Callable

from hone_drive.model import Accounting, Folder, Volume
from hone_drive.sizing import Tally, walk

UNACCOUNTED_SHARE = 0.05  # report when over 5% of the volume (design §5.4)
TOP_FOLDERS = 25
ACCOUNT_TIME_LIMIT_S = 600.0

HINTS = {
    "linux": [
        "other users' home folders (/home/*)",
        "model servers running as their own user (Ollama: /usr/share/ollama)",
        "container data (/var/lib/docker, /var/lib/containerd, /var/lib/containers)",
        "system folders only root can read (/root, /var/lib, /var/log, /opt)",
        "deleted files a running program still holds open",
        "filesystem snapshots (Btrfs, ZFS, Timeshift)",
    ],
    "macos": [
        "other users' home folders (/Users/*)",
        "local Time Machine and APFS snapshots (tmutil listlocalsnapshots /)",
        "Docker Desktop's disk image (~/Library/Containers/com.docker.docker)",
        "system folders only root can read (/private/var, /Library)",
        "purgeable space that macOS counts as used",
    ],
    "windows": [
        "other users' folders (C:\\Users\\*)",
        "WSL and Docker Desktop disk images (*.vhdx under %LOCALAPPDATA%)",
        "System Restore points and shadow copies (vssadmin list shadowstorage)",
        "the hibernation file and page file (hiberfil.sys, pagefile.sys)",
        "C:\\Windows\\WinSxS and Windows Update files",
    ],
}
MEASURE = {
    "linux": "sudo du -xh --max-depth=3 {mount} 2>/dev/null | sort -rh | head -40",
    "macos": "sudo du -xh -d 3 {mount} 2>/dev/null | sort -rh | head -40",
    "windows": 'Get-ChildItem {mount} -Directory -Force | ForEach-Object { "{0,10:N1} GB  {1}" -f '
    "((Get-ChildItem $_.FullName -Recurse -File -Force -ErrorAction SilentlyContinue | "
    "Measure-Object Length -Sum).Sum / 1GB), $_.FullName }   # in an administrator PowerShell",
}


def account(
    volume: Volume,
    os_name: str,
    *,
    explained: list[Folder] | None = None,
    time_limit: float = ACCOUNT_TIME_LIMIT_S,
    progress: Callable[[int], None] | None = None,
) -> Accounting:
    """Walk `volume` without crossing into other mounts; `progress` gets the bytes counted so far."""
    folders: dict[str, int] = {}
    counted = [0]

    def visit(_path: str, info: os.stat_result) -> None:
        counted[0] += 1
        if progress is not None and counted[0] % 5000 == 0:
            progress(tally.bytes)

    tally = Tally()
    walk(
        volume.mount,
        tally,
        deadline=time.monotonic() + time_limit,
        visit=visit,
        folder_total=folders.__setitem__,
        depth_limit=2,
    )
    top = sorted(((p, b) for p, b in folders.items() if p != volume.mount), key=lambda pb: (-pb[1], pb[0]))
    return summarise(volume, os_name, tally, top[:TOP_FOLDERS], explained)


def summarise(
    volume: Volume,
    os_name: str,
    tally: Tally,
    top: list[tuple[str, int]],
    explained: list[Folder] | None = None,
) -> Accounting:
    """`explained`: stores on this volume that the walk couldn't read but a server sized (design §5.4)."""
    explained = explained or []
    unaccounted = max(0, volume.used_bytes - tally.bytes - sum(f.bytes for f in explained))
    result = Accounting(
        volume=volume.mount,
        readable_bytes=tally.bytes,
        unaccounted_bytes=unaccounted,
        explained=explained,
        top_folders=[Folder(path=p, bytes=b) for p, b in top],
        unreadable=_outermost(tally.unreadable)[:40],
        partial=tally.partial,
    )
    if volume.total_bytes and unaccounted > UNACCOUNTED_SHARE * volume.total_bytes:
        result.hints = HINTS[os_name]
        result.measure_command = MEASURE[os_name].replace("{mount}", volume.mount)
    return result


def _outermost(paths: list[str]) -> list[str]:
    """Unreadable folders without the ones inside another listed folder."""
    kept: list[str] = []
    for path in sorted(set(paths)):
        if not any(path.startswith(k.rstrip(os.sep) + os.sep) for k in kept):
            kept.append(path)
    return kept
