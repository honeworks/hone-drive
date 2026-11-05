"""Copying one file and proving the copy (change 0003): written to `<name>.part`, flushed, read back past the
page cache (design §6.2) and compared by SHA-256, and only then renamed into place."""

from __future__ import annotations

import contextlib
import hashlib
import os
import threading
from collections.abc import Callable

from hone_drive.directio import buffer, open_file
from hone_drive.errors import ShelfDamaged, Stopped
from hone_drive.model import CacheBypass
from hone_drive.shelf_catalog import ShelfFile

CHUNK = 16 * 1024 * 1024
Progress = Callable[[int], None]  # bytes done in this file


def sha256_of(path: str, direct: bool, stop: threading.Event, progress: Progress | None = None) -> str:
    """The file's SHA-256; with `direct`, read with the page cache bypassed, so a copy is checked on the
    drive and not in memory."""
    digest = hashlib.sha256()
    if direct:
        data = buffer(CHUNK)
        opened = open_file(path, write=False)
        with opened.file as handle:
            done = 0
            while got := handle.readinto(data):
                _stop(stop)
                digest.update(memoryview(data)[:got])
                done += got
                if progress:
                    progress(done)
        return digest.hexdigest()
    with open(path, "rb") as handle:
        done = 0
        while chunk := handle.read(CHUNK):
            _stop(stop)
            digest.update(chunk)
            done += len(chunk)
            if progress:
                progress(done)
    return digest.hexdigest()


def read_back_cache(path: str) -> CacheBypass:
    """How a read of `path` bypasses the cache on this system (recorded with each checked copy)."""
    opened = open_file(path, write=False)
    opened.file.close()
    return opened.cache


def copy_checked(
    source: str,
    target: str,
    stop: threading.Event,
    progress: Progress | None = None,
    expected: str | None = None,
) -> tuple[str, CacheBypass]:
    """Copy `source` to `target` and prove it; returns the SHA-256 and how the check bypassed the cache.

    `expected`: the hash the source must have (the catalog's, when bringing a model back); a source that
    doesn't match is damaged and nothing is written in place."""
    part = target + ".part"
    os.makedirs(os.path.dirname(target), exist_ok=True)
    digest = hashlib.sha256()
    total = os.path.getsize(source)
    try:
        with open(source, "rb") as reader, open(part, "wb") as writer:
            done = 0
            while chunk := reader.read(CHUNK):
                _stop(stop)
                digest.update(chunk)
                writer.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done // 2)  # reading and writing is the first half, the check the second
            writer.flush()
            os.fsync(writer.fileno())
        wrote = digest.hexdigest()
        if expected is not None and wrote != expected:
            raise ShelfDamaged(f"{source} doesn't match its SHA-256 in the catalog: it is damaged")
        half = total // 2
        later: Progress | None = None
        if progress is not None:
            report = progress
            later = lambda n: report(half + n // 2)  # noqa: E731 - the check is the second half
        check = sha256_of(part, direct=True, stop=stop, progress=later)
        if check != wrote:
            raise ShelfDamaged(f"the copy at {target} doesn't match {source}; try again or check the drive")
        cache = read_back_cache(part)
        os.replace(part, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(part)
        raise
    return wrote, cache


def unchanged(path: str, entry: ShelfFile, which: str) -> bool:
    """True when `path` still has the size and modification time the catalog recorded for it (`which`:
    "drive" or "local"); the catalog is trusted then (owner, 2026-11-05: "Trust catalog")."""
    try:
        info = os.stat(path)
    except OSError:
        return False
    recorded = entry.drive_ns if which == "drive" else entry.local_ns
    return info.st_size == entry.bytes and recorded is not None and info.st_mtime_ns == recorded


def mtime_ns(path: str) -> int:
    return os.stat(path).st_mtime_ns


def _stop(stop: threading.Event) -> None:
    if stop.is_set():
        raise Stopped("stopped")
