from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from hone_drive.sizing import Tally, size_of, walk
from tests.conftest import write


def test_walk_counts_files_once_and_ignores_symlinks(tmp_path: Path) -> None:
    a = write(tmp_path / "a" / "one.bin", 10_000)
    write(tmp_path / "b" / "two.bin", 20_000)
    if sys.platform != "win32":
        os.link(a, tmp_path / "b" / "hard.bin")
        os.symlink(a, tmp_path / "b" / "soft.bin")
    tally = walk(str(tmp_path))
    assert tally.files == 2
    assert tally.bytes >= 30_000
    assert tally.newest_access > 0


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions, not root")
def test_unreadable_folders_are_named_not_counted(tmp_path: Path) -> None:
    secret = tmp_path / "secret"
    write(secret / "x.bin", 5000)
    secret.chmod(0)
    try:
        tally = walk(str(tmp_path))
    finally:
        secret.chmod(0o755)
    assert str(secret) in tally.unreadable
    assert tally.bytes == 0


def test_deadline_marks_partial(tmp_path: Path) -> None:
    for i in range(5):
        write(tmp_path / f"d{i}" / "f.bin", 10)
    assert walk(str(tmp_path), deadline=time.monotonic() - 1).partial


def test_folder_totals_to_a_depth(tmp_path: Path) -> None:
    write(tmp_path / "a" / "b" / "c" / "f.bin", 8192)
    totals: dict[str, int] = {}
    walk(str(tmp_path), folder_total=totals.__setitem__, depth_limit=1)
    assert totals[str(tmp_path / "a")] >= 8192
    assert str(tmp_path / "a" / "b") not in totals


def test_size_of_file_folder_and_missing(tmp_path: Path) -> None:
    f = write(tmp_path / "f.bin", 4096)
    assert size_of(str(f)).files == 1
    assert size_of(str(tmp_path)).files == 1
    missing = size_of(str(tmp_path / "nope"))
    assert missing.unreadable and missing.bytes == 0
    assert walk(str(tmp_path / "nope")).unreadable


def test_shared_seen_set_counts_hard_links_across_calls(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("hard links are counted by size on Windows")
    a = write(tmp_path / "a" / "x.bin", 4096)
    (tmp_path / "b").mkdir()
    os.link(a, tmp_path / "b" / "x.bin")
    seen: set[tuple[int, int]] = set()
    first = size_of(str(tmp_path / "a"), seen=seen)
    second = size_of(str(tmp_path / "b"), seen=seen)
    assert first.files == 1 and second.files == 0
    assert isinstance(Tally().seen, set)
