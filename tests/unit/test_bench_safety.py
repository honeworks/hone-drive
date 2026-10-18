from __future__ import annotations

import os
from pathlib import Path

import pytest

from hone_drive import bench_safety as safety
from hone_drive.errors import BenchmarkBusy, UnsafeBenchmark


def test_room_and_writable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    safety.check_room(str(tmp_path), 1)
    with pytest.raises(UnsafeBenchmark, match="--size"):
        safety.check_room(str(tmp_path), 10**18)
    with pytest.raises(UnsafeBenchmark, match="can't write"):
        safety.check_writable(str(tmp_path / "missing"))
    monkeypatch.setattr(safety, "MARGIN_FREE", 10**18)
    assert safety.low_on_space(str(tmp_path))


def test_ledger_removes_leftovers(tmp_path: Path) -> None:
    left = tmp_path / ".hone-drive-bench-r1-seq.tmp"
    left.write_bytes(b"x")
    other = tmp_path / "not-ours.tmp"
    other.write_bytes(b"x")
    safety.remember(str(left))
    safety.remember(str(other))
    assert safety.clean_leftovers() == sorted([str(left), str(other)])
    assert not left.exists() and other.exists()  # only files named like ours are deleted
    assert safety.clean_leftovers() == []
    safety.remember(str(left))
    safety.remove(str(left))
    assert safety.clean_leftovers() == []


def test_ledger_survives_a_broken_file(data_home: Path) -> None:
    data_home.mkdir(parents=True)
    (data_home / "bench-files.json").write_text("{not json")
    assert safety.clean_leftovers() == []


def test_one_benchmark_per_machine(data_home: Path) -> None:
    with safety.machine_lock():
        assert (data_home / "bench.lock").read_text() == str(os.getpid())
    assert not (data_home / "bench.lock").exists()
    (data_home / "bench.lock").write_text("999999999")  # a process that is gone
    with safety.machine_lock():
        pass
    (data_home / "bench.lock").write_text(str(os.getppid()))  # our parent is running
    with pytest.raises(BenchmarkBusy), safety.machine_lock():
        pass
