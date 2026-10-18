from __future__ import annotations

import threading
from pathlib import Path

import psutil
import pytest

from hone_drive import benchmark as bench_module
from hone_drive.benchmark import Options, Tick, bench, default_folders, model_files, sustained_label
from hone_drive.errors import UnsafeBenchmark
from hone_drive.model import Item, Run, Store, Volume
from hone_drive.scanner import scan
from hone_drive.testing import FakeProbe, reference_probe
from hone_drive.volumes import disk_usage, mount_of
from tests.conftest import write

MIB = 1024**2


def local_run(tmp_path: Path) -> Run:
    """A run with one volume: the one the temporary folder is on (a RAM disk on some machines)."""
    mounts = [p.mountpoint for p in psutil.disk_partitions(all=True)]
    mount = mount_of(str(tmp_path.resolve()), mounts) or str(tmp_path)
    total, used, free = disk_usage(mount)
    volume = Volume(
        mount=mount, device="test", filesystem="test", total_bytes=total, used_bytes=used, free_bytes=free
    )
    return scan(probe=FakeProbe(reference_probe().facts, [], [volume]), finders={})


def small(**changes: object) -> Options:
    options = Options(size=32 * MIB, sustained_size=48 * MIB, sustained_seconds=5, random_seconds=0.2)
    for key, value in changes.items():
        setattr(options, key, value)
    return options


def test_seq_random_and_model_reads(tmp_path: Path) -> None:
    run = local_run(tmp_path)
    model = write(tmp_path / "model.gguf", 20 * MIB)
    ticks: list[Tick] = []
    bench(
        run,
        [str(tmp_path)],
        ["seq", "random", "model"],
        small(model_files=[str(model)], on_tick=ticks.append),
    )
    tests = sorted(m.test for m in run.measurements)
    assert tests == ["model-read", "random-read", "seq-read", "seq-write"]
    for m in run.measurements:
        assert m.cache in ("direct", "fallback", "possibly-cached")
        assert m.gbs > 0 and m.seconds > 0
    random_read = next(m for m in run.measurements if m.test == "random-read")
    assert random_read.iops and random_read.latency_ms is not None
    assert next(m for m in run.measurements if m.test == "model-read").files == [str(model)]
    assert ticks and ticks[-1].done <= ticks[-1].total
    assert not list(tmp_path.glob(".hone-drive-bench-*"))


def test_a_second_bench_replaces_the_volumes_results(tmp_path: Path) -> None:
    run = local_run(tmp_path)
    bench(run, [str(tmp_path)], ["seq"], small())
    bench(run, [str(tmp_path)], ["seq"], small())
    assert sorted(m.test for m in run.measurements) == ["seq-read", "seq-write"]


def test_random_only_keeps_no_write_measurement(tmp_path: Path) -> None:
    run = local_run(tmp_path)
    bench(run, [str(tmp_path)], ["random"], small())
    assert [m.test for m in run.measurements] == ["random-read"]


def test_sustained_curve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = local_run(tmp_path)
    bench(run, [str(tmp_path)], ["sustained"], small())
    m = run.measurements[0]
    assert m.test == "sustained-write" and m.bytes == 48 * MIB


def test_stop_makes_a_partial_measurement_and_removes_the_file(tmp_path: Path) -> None:
    run = local_run(tmp_path)
    stop = threading.Event()

    def on_tick(tick: Tick) -> None:
        if tick.done >= 16 * MIB:
            stop.set()

    bench(run, [str(tmp_path)], ["seq"], small(stop=stop, on_tick=on_tick))
    assert [m.partial for m in run.measurements] == [True]
    assert not list(tmp_path.glob(".hone-drive-bench-*"))


def test_refusals(tmp_path: Path) -> None:
    run = local_run(tmp_path)
    with pytest.raises(UnsafeBenchmark, match="unknown tests"):
        bench(run, [str(tmp_path)], ["fast"], small())
    with pytest.raises(UnsafeBenchmark, match="--size"):
        bench(run, [str(tmp_path)], ["seq"], small(size=10**18))
    empty = scan(probe=FakeProbe(reference_probe().facts, [], []), finders={})
    with pytest.raises(UnsafeBenchmark, match="not on a volume"):
        bench(empty, [str(tmp_path)], ["seq"], small())
    for volume in run.volumes:
        volume.read_only = True
    with pytest.raises(UnsafeBenchmark, match="read-only"):
        bench(run, [str(tmp_path)], ["seq"], small())


def test_an_error_mid_write_still_removes_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = local_run(tmp_path)

    def broken(*args: object, **kwargs: object) -> object:
        raise OSError("device unplugged")

    monkeypatch.setattr(bench_module, "_read", broken)
    with pytest.raises(OSError, match="unplugged"):
        bench(run, [str(tmp_path)], ["seq"], small())
    assert not list(tmp_path.glob(".hone-drive-bench-*"))


def test_defaults_and_model_files() -> None:
    run = scan(probe=reference_probe(), finders={})
    assert default_folders(run, "/home/user") == ["/home/user", "/run/media/user/PNYRP60PSSD"]
    run.stores = [
        Store(
            id="x",
            title="x",
            items=[
                Item(id="a", names=["a"], bytes=5, files=1, path="/m/a", volume="/"),
                Item(id="b", names=["b"], bytes=9, files=1, path="/m/b", volume="/"),
                Item(id="c", names=["c"], bytes=99, files=4, path="/m/c", volume="/"),
            ],
        )
    ]
    assert model_files(run, "/") == ["/m/b", "/m/a"]


def test_sustained_label() -> None:
    assert sustained_label([1.0] * 3) is None
    assert sustained_label([2.0, 2.0, 2.0, 2.0, 0.2, 0.2, 0.2, 0.2]) == "cache exhausted after 8 GB"
    assert sustained_label([1.0] * 10) == "steady"


def test_a_folder_on_a_skipped_filesystem_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = local_run(tmp_path)
    monkeypatch.setattr(bench_module, "_same_device", lambda folder, mount: False)
    with pytest.raises(UnsafeBenchmark, match="not on a volume"):
        bench(run, [str(tmp_path)], ["seq"], small())
