"""Counting the space files use (design §5.2): allocated bytes, each inode once, unreadable folders named.

`walk` is shared by the store finders and the volume accounting (design §5.4)."""

from __future__ import annotations

import os
import stat
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field

FileVisitor = Callable[[str, os.stat_result], None]


@dataclass
class Tally:
    """What a walk found. `seen` holds (device, inode) of files with several links, so they count once."""

    bytes: int = 0
    files: int = 0
    newest_access: float = 0.0
    unreadable: list[str] = field(default_factory=list[str])
    partial: bool = False
    seen: set[tuple[int, int]] = field(default_factory=set[tuple[int, int]])


def allocated(info: os.stat_result) -> int:
    """Bytes a file takes on disk: its blocks on POSIX (sparse files, compression), its size on Windows."""
    blocks = getattr(info, "st_blocks", None)
    return blocks * 512 if isinstance(blocks, int) and sys.platform != "win32" else info.st_size


def walk(
    root: str,
    tally: Tally | None = None,
    *,
    deadline: float | None = None,
    one_device: bool = True,
    visit: FileVisitor | None = None,
    folder_total: Callable[[str, int], None] | None = None,
    depth_limit: int = 0,
) -> Tally:
    """Count every file under `root` without following symlinks or (by default) crossing into other
    mounts. Stops at `deadline` (a `time.monotonic()` value) with `partial` set. `folder_total` receives
    the bytes under each folder down to `depth_limit` levels below `root`."""
    tally = tally if tally is not None else Tally()
    try:
        root_dev = os.lstat(root).st_dev
    except OSError:
        tally.unreadable.append(root)
        return tally
    _walk_dir(root, 0, root_dev if one_device else None, tally, deadline, visit, folder_total, depth_limit)
    return tally


def _walk_dir(
    path: str,
    depth: int,
    device: int | None,
    tally: Tally,
    deadline: float | None,
    visit: FileVisitor | None,
    folder_total: Callable[[str, int], None] | None,
    depth_limit: int,
) -> None:
    before = tally.bytes
    stack: list[str] = []
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if deadline is not None and time.monotonic() > deadline:
                    tally.partial = True
                    return
                _count(entry, device, tally, visit, stack)
    except OSError:
        tally.unreadable.append(path)
        return
    for sub in stack:
        if folder_total is not None and depth < depth_limit:
            _walk_dir(sub, depth + 1, device, tally, deadline, visit, folder_total, depth_limit)
        else:
            _walk_flat(sub, device, tally, deadline, visit)
        if tally.partial:
            return
    if folder_total is not None:
        folder_total(path, tally.bytes - before)


def _walk_flat(
    root: str, device: int | None, tally: Tally, deadline: float | None, visit: FileVisitor | None
) -> None:
    stack = [root]
    while stack:
        path = stack.pop()
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    if deadline is not None and time.monotonic() > deadline:
                        tally.partial = True
                        return
                    _count(entry, device, tally, visit, stack)
        except OSError:
            tally.unreadable.append(path)


def _count(
    entry: os.DirEntry[str], device: int | None, tally: Tally, visit: FileVisitor | None, stack: list[str]
) -> None:
    try:
        info = entry.stat(follow_symlinks=False)
    except OSError:
        tally.unreadable.append(entry.path)
        return
    if stat.S_ISDIR(info.st_mode):
        if device is None or info.st_dev == device:
            stack.append(entry.path)
        return
    if not stat.S_ISREG(info.st_mode):
        return  # symlinks, sockets, devices take no space of their own worth counting
    if info.st_nlink > 1:
        key = (info.st_dev, info.st_ino)
        if key in tally.seen:
            return
        tally.seen.add(key)
    tally.bytes += allocated(info)
    tally.files += 1
    tally.newest_access = max(tally.newest_access, info.st_atime)
    if visit is not None:
        visit(entry.path, info)


def size_of(path: str, deadline: float | None = None, seen: set[tuple[int, int]] | None = None) -> Tally:
    """The tally of one file or folder (following `path` itself if it is a symlink)."""
    tally = Tally(seen=seen if seen is not None else set())
    real = os.path.realpath(path)
    try:
        info = os.stat(real)
    except OSError:
        tally.unreadable.append(path)
        return tally
    if stat.S_ISREG(info.st_mode):
        tally.bytes, tally.files, tally.newest_access = allocated(info), 1, info.st_atime
        return tally
    return walk(real, tally, deadline=deadline, one_device=False)
