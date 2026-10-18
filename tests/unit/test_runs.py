from __future__ import annotations

import json
from pathlib import Path

import pytest

from hone_drive.errors import NotFound, UnsupportedFormat
from hone_drive.model import MachineFacts, Run
from hone_drive.runs import data_home, list_runs, load_run, save_run


def run(run_id: str) -> Run:
    return Run(
        id=run_id,
        created="2026-10-18T00:00:00+00:00",
        version="0",
        machine=MachineFacts(os="linux", os_version="x", hostname="h"),
    )


def test_save_list_load_latest(data_home: Path) -> None:
    with pytest.raises(NotFound):
        load_run("latest")
    save_run(run("20261018T000000Z-aaaaaa"))
    save_run(run("20261019T000000Z-bbbbbb"))
    assert list_runs() == ["20261019T000000Z-bbbbbb", "20261018T000000Z-aaaaaa"]
    assert load_run("latest").id == "20261019T000000Z-bbbbbb"
    assert not list(data_home.glob("runs/.run-*"))
    with pytest.raises(NotFound):
        load_run("nope")


def test_newer_format_is_refused(data_home: Path) -> None:
    path = save_run(run("r1"))
    data = json.loads(path.read_text())
    data["format"] = 99
    path.write_text(json.dumps(data))
    with pytest.raises(UnsupportedFormat):
        load_run("r1")


def test_data_home_per_os() -> None:
    assert data_home({"HONE_DRIVE_HOME": "/x"}) == Path("/x")
    assert data_home({"HOME": "/h"}, "linux") == Path("/h/.local/share/hone-drive")
    assert data_home({"HOME": "/h", "XDG_DATA_HOME": "/d"}, "linux") == Path("/d/hone-drive")
    assert data_home({"HOME": "/h"}, "darwin") == Path("/h/Library/Application Support/hone-drive")
    assert data_home({"LOCALAPPDATA": "/l"}, "win32") == Path("/l/hone-drive")
