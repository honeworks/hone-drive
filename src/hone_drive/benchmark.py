"""Benchmarks (design §6): throughput from monotonic time, with the page cache bypassed and the safety
rules of §6.3 kept on every path."""

from __future__ import annotations

import os
import random
import statistics
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from hone_drive import bench_safety as safety
from hone_drive.directio import ALIGN, buffer, drop_cache, open_file
from hone_drive.errors import UnsafeBenchmark
from hone_drive.links import bound_label
from hone_drive.model import CacheBypass, Measurement, Run, TestName
from hone_drive.volumes import mount_of

CHUNK = 16 * 1024 * 1024
TESTS = ("seq", "random", "sustained", "model")
DEFAULT_SIZE = 4 * 1024**3


@dataclass(frozen=True)
class Tick:
    """Progress of the test that is running: what, where, how far and how fast right now."""

    test: str
    volume: str
    done: int
    total: int
    gbs: float


@dataclass
class Options:
    size: int = DEFAULT_SIZE
    sustained_size: int = 32 * 1024**3
    sustained_seconds: float = 120.0
    random_seconds: float = 10.0
    model_files: list[str] = field(default_factory=list[str])
    on_tick: Callable[[Tick], None] | None = None
    stop: threading.Event = field(default_factory=threading.Event)


def bench(
    run: Run, folders: list[str], tests: list[str] | None = None, options: Options | None = None
) -> Run:
    """Benchmark each folder's volume and add the measurements to `run`. `tests` from `TESTS`: `seq`
    (write then read), `random`, `sustained`, `model` (the `model_files` on that volume, read only)."""
    tests = list(tests or ["seq"])
    options = options or Options()
    unknown = sorted(set(tests) - set(TESTS))
    if unknown:
        raise UnsafeBenchmark(f"unknown tests {unknown}; choose from {list(TESTS)}")
    mounts = [v.mount for v in run.volumes]
    with safety.machine_lock():
        safety.clean_leftovers()
        for folder in folders:
            mount = mount_of(os.path.realpath(folder), mounts)
            if mount is None or not _same_device(folder, mount):
                raise UnsafeBenchmark(
                    f"{folder} is not on a volume of this scan; scan again after mounting it"
                )
            run.measurements = [
                m for m in run.measurements if m.volume != mount or m.test not in _names(tests)
            ]
            run.measurements += _bench_volume(run, folder, mount, tests, options)
            if options.stop.is_set():
                break
    return run


def _same_device(folder: str, mount: str) -> bool:
    """False when `folder` is on a filesystem the scan skipped (a RAM disk mounted under a volume)."""
    try:
        return os.stat(folder).st_dev == os.stat(mount).st_dev
    except OSError:
        return False


def default_folders(run: Run, home: str) -> list[str]:
    """The home folder's volume and every external volume (design §6.1)."""
    folders = [home]
    for volume in run.volumes:
        drive = run.drive(volume.drive_id)
        if drive is not None and drive.external and not volume.read_only and volume.mount not in folders:
            folders.append(volume.mount)
    return folders


def model_files(run: Run, mount: str, limit: int = 3) -> list[str]:
    """The largest single-file items of the stores on `mount`, for `model-read`."""
    items = [i for s in run.stores for i in s.items if i.volume == mount and i.files == 1 and i.path]
    return [i.path for i in sorted(items, key=lambda i: -(i.bytes or 0))[:limit] if i.path]


def _names(tests: list[str]) -> set[str]:
    names: dict[str, set[str]] = {
        "seq": {"seq-write", "seq-read"},
        "random": {"random-read"},
        "sustained": {"sustained-write"},
        "model": {"model-read"},
    }
    return {name for test in tests for name in names[test]}


def _bench_volume(run: Run, folder: str, mount: str, tests: list[str], options: Options) -> list[Measurement]:
    volume = run.volume(mount)
    if volume is not None and volume.read_only:
        raise UnsafeBenchmark(f"{mount} is read-only")
    found: list[Measurement] = []
    writes = any(t in tests for t in ("seq", "random", "sustained"))
    if writes:
        safety.check_writable(folder)
    if "seq" in tests or "random" in tests:
        path = safety.temp_path(folder, run.id, "seq")
        safety.check_room(folder, options.size)
        safety.remember(path)
        try:
            write = _write(path, mount, options.size, options, "seq-write")
            found.append(write)
            if not write.partial and "seq" in tests:
                found.append(_read(path, mount, options, "seq-read"))
            if not write.partial and "random" in tests:
                found.append(_random(path, mount, options))
        finally:
            safety.remove(path)
    if "sustained" in tests and not options.stop.is_set():
        path = safety.temp_path(folder, run.id, "sustained")
        safety.check_room(folder, options.sustained_size)
        safety.remember(path)
        try:
            found.append(_write(path, mount, options.sustained_size, options, "sustained-write"))
        finally:
            safety.remove(path)
    if "model" in tests:
        mounts = [v.mount for v in run.volumes]
        chosen = options.model_files or model_files(run, mount)
        files = [f for f in chosen if mount_of(os.path.realpath(f), mounts) == mount]
        for file in files[:3]:
            found.append(_read(file, mount, options, "model-read"))
    if "seq" not in tests and "seq-write" in {m.test for m in found}:
        found = [m for m in found if m.test != "seq-write"]  # written only to have a file to read at random
    _label(run, found)
    return found


def _measure(
    test: TestName, mount: str, total: int, seconds: float, cache: CacheBypass, rates: list[float]
) -> Measurement:
    gbs = total / seconds / 1e9 if seconds > 0 else 0.0
    spread = (
        statistics.pstdev(rates) / statistics.fmean(rates)
        if len(rates) > 1 and statistics.fmean(rates)
        else None
    )
    return Measurement(
        id=f"{test}@{mount}",
        test=test,
        volume=mount,
        bytes=total,
        seconds=round(seconds, 4),
        gbs=round(gbs, 4),
        cache=cache,
        spread=round(spread, 3) if spread is not None else None,
    )


def _write(path: str, mount: str, size: int, options: Options, test: TestName) -> Measurement:
    chunks = max(1, size // CHUNK)
    data = buffer(CHUNK)
    data.write(
        os.urandom(1024 * 1024) * (CHUNK // (1024 * 1024))
    )  # incompressible enough for SSD controllers
    opened = open_file(path, write=True)
    rates: list[float] = []
    curve: list[float] = []
    written, partial = 0, False
    start = second_start = time.monotonic()
    second_bytes = 0
    deadline = start + options.sustained_seconds if test == "sustained-write" else None
    with opened.file as handle:
        for index in range(chunks):
            if options.stop.is_set() or (index % 16 == 0 and safety.low_on_space(os.path.dirname(path))):
                partial = True
                break
            t0 = time.monotonic()
            handle.write(data)
            t1 = time.monotonic()
            written += CHUNK
            second_bytes += CHUNK
            rates.append(CHUNK / max(t1 - t0, 1e-9) / 1e9)
            if t1 - second_start >= 1.0:
                curve.append(round(second_bytes / (t1 - second_start) / 1e9, 3))
                second_start, second_bytes = t1, 0
            _tick(options, test, mount, written, chunks * CHUNK, rates[-1])
            if deadline is not None and t1 > deadline:
                break
        os.fsync(handle.fileno())
        drop_cache(opened)
    result = _measure(test, mount, written, time.monotonic() - start, opened.cache, rates)
    result.partial, result.curve = partial, curve if test == "sustained-write" else []
    return result


def _read(path: str, mount: str, options: Options, test: TestName) -> Measurement:
    data = buffer(CHUNK)
    opened = open_file(path, write=False)
    total = os.path.getsize(path)
    rates: list[float] = []
    done, partial = 0, False
    start = time.monotonic()
    with opened.file as handle:
        while True:
            if options.stop.is_set():
                partial = True
                break
            t0 = time.monotonic()
            got = handle.readinto(data)
            if not got:
                break
            done += got
            rates.append(got / max(time.monotonic() - t0, 1e-9) / 1e9)
            _tick(options, test, mount, done, total, rates[-1])
    result = _measure(test, mount, done, time.monotonic() - start, opened.cache, rates)
    result.partial = partial
    if test == "model-read":
        result.id, result.files = f"model-read@{mount}:{Path(path).name}", [path]
    return result


def _random(path: str, mount: str, options: Options) -> Measurement:
    """4 KiB reads at random aligned offsets: one at a time for latency, then 32 threads for throughput."""
    size = os.path.getsize(path)
    blocks = size // ALIGN
    latencies: list[float] = []
    deadline = time.monotonic() + options.random_seconds / 2
    opened = open_file(path, write=False)
    rng = random.Random(0)  # noqa: S311 - offsets, not secrets
    data = buffer(ALIGN)
    with opened.file as handle:
        while time.monotonic() < deadline and not options.stop.is_set():
            handle.seek(rng.randrange(blocks) * ALIGN)
            t0 = time.monotonic()
            handle.readinto(data)
            latencies.append(time.monotonic() - t0)
    counts = _parallel_reads(path, blocks, options)
    seconds = options.random_seconds / 2
    result = _measure("random-read", mount, sum(counts) * ALIGN, seconds, opened.cache, [])
    result.iops = round(sum(counts) / seconds, 1)
    result.latency_ms = round(statistics.median(latencies) * 1000, 3) if latencies else None
    result.warnings.append("32 reads in flight are approximated with threads (design §16)")
    return result


def _parallel_reads(path: str, blocks: int, options: Options) -> list[int]:
    deadline = time.monotonic() + options.random_seconds / 2

    def worker(seed: int) -> int:
        rng, data, count = random.Random(seed), buffer(ALIGN), 0  # noqa: S311 - offsets, not secrets
        with open_file(path, write=False).file as handle:
            while time.monotonic() < deadline and not options.stop.is_set():
                handle.seek(rng.randrange(blocks) * ALIGN)
                handle.readinto(data)
                count += 1
        return count

    with ThreadPoolExecutor(max_workers=32) as pool:
        return list(pool.map(worker, range(1, 33)))


def _tick(options: Options, test: str, mount: str, done: int, total: int, gbs: float) -> None:
    if options.on_tick is not None:
        options.on_tick(Tick(test, mount, done, total, round(gbs, 3)))


def _label(run: Run, found: list[Measurement]) -> None:
    for m in found:
        volume = run.volume(m.volume)
        drive = run.drive(volume.drive_id if volume else None)
        if m.cache == "possibly-cached":
            m.warnings.append("the OS cache could not be bypassed: this may measure memory, not the drive")
        if m.test in ("seq-read", "model-read") and drive is not None:
            m.label = bound_label(m.gbs, drive.link)
        if m.test == "sustained-write":
            m.label = sustained_label(m.curve)


def sustained_label(curve: list[float]) -> str | None:
    """`cache exhausted after N GB`: throughput falls under half its first seconds and stays there."""
    if len(curve) < 6:
        return None
    start = statistics.median(curve[:3])
    for index in range(3, len(curve) - 2):
        if all(v < start * 0.5 for v in curve[index : index + 3]):
            return f"cache exhausted after {sum(curve[:index]):.0f} GB"
    return "steady"
