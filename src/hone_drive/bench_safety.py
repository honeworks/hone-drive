"""The benchmark's safety rules (design §6.3): one temporary file per test, always removed, free space
checked before and during writes, one benchmark per machine."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
from collections.abc import Generator
from pathlib import Path

from hone_drive.errors import BenchmarkBusy, UnsafeBenchmark
from hone_drive.runs import data_home

GIB = 1024**3
MARGIN_FREE = 2 * GIB  # writes stop when free space falls under this (rule 2)
PREFIX = ".hone-drive-bench-"


def temp_path(folder: str, run_id: str, test: str) -> str:
    return os.path.join(folder, f"{PREFIX}{run_id}-{test}.tmp")


def check_room(folder: str, size: int) -> None:
    """Rule 2: the file's size plus 10% plus 2 GiB must be free."""
    free = shutil.disk_usage(folder).free
    need = int(size * 1.1) + MARGIN_FREE
    if free < need:
        raise UnsafeBenchmark(
            f"{folder} has {free / 1e9:.1f} GB free; a {size / 1e9:.1f} GB write test needs "
            f"{need / 1e9:.1f} GB. Choose a smaller size (--size) or free some space first."
        )


def check_writable(folder: str) -> None:
    """Rule 3: never write to a read-only volume or a folder the user can't write to."""
    if not os.path.isdir(folder) or not os.access(folder, os.W_OK):
        raise UnsafeBenchmark(
            f"can't write a test file in {folder}; pass a folder on that drive that you can write to"
        )


def low_on_space(folder: str) -> bool:
    return shutil.disk_usage(folder).free < MARGIN_FREE


# -- the ledger: files a crash may have left behind (rule 1) ------------------------------------------------


def _ledger(home: Path | None) -> Path:
    return (home or data_home()) / "bench-files.json"


def _read(home: Path | None) -> list[str]:
    try:
        return list(json.loads(_ledger(home).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return []


def _write(paths: list[str], home: Path | None) -> None:
    path = _ledger(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(set(paths))), encoding="utf-8")
    os.replace(tmp, path)


def remember(path: str, home: Path | None = None) -> None:
    _write([*_read(home), path], home)


def remove(path: str, home: Path | None = None) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.remove(path)
    _write([p for p in _read(home) if p != path], home)


def clean_leftovers(home: Path | None = None) -> list[str]:
    """Delete the temporary files an earlier, killed benchmark left behind; returns their paths."""
    left = _read(home)
    for path in left:
        if os.path.basename(path).startswith(PREFIX):
            with contextlib.suppress(OSError):
                os.remove(path)
    _write([], home)
    return left


# -- one benchmark per machine (rule 5) ----------------------------------------------------------------------


@contextlib.contextmanager
def machine_lock(home: Path | None = None) -> Generator[None]:
    import psutil  # noqa: PLC0415

    path = (home or data_home()) / "bench.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError:
            holder = path.read_text(encoding="utf-8").strip()
            if holder.isdigit() and psutil.pid_exists(int(holder)) and int(holder) != os.getpid():
                raise BenchmarkBusy(
                    f"another benchmark is running (process {holder}); wait for it to finish"
                ) from None
            path.unlink(missing_ok=True)  # left by a process that is gone
    else:  # pragma: no cover - two processes racing for a stale lock
        raise BenchmarkBusy("could not take the benchmark lock")
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        path.unlink(missing_ok=True)
