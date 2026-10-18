from __future__ import annotations

from pathlib import Path

import pytest

from hone_drive import cli
from hone_drive.model import Accounting, Store
from hone_drive.runs import list_runs
from hone_drive.scanner import scan as real_scan
from hone_drive.testing import reference_probe


@pytest.fixture
def fake_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    def scan(**kwargs: object) -> object:
        run = real_scan(probe=reference_probe(), finders={})
        run.stores = [
            Store(
                id="ollama",
                title="Ollama models",
                path="/usr/share/ollama",
                bytes=388_000_000_000,
                source="ollama api",
            )
        ]
        run.accounting = [
            Accounting(
                volume="/",
                readable_bytes=1,
                unaccounted_bytes=400_000_000_000,
                measure_command="sudo du -xh / | sort -rh",
            )
        ]
        return run

    monkeypatch.setattr("hone_drive.scanner.scan", scan)


def test_scan_prints_a_summary_and_saves(fake_scan: None, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["scan"]) == 0
    out = capsys.readouterr().out
    assert "PNY RP60 PSSD" in out and "10 of 20 Gbps, 1 of 2 lanes  ← LIMITED" in out
    assert "388 GB  Ollama models  /usr/share/ollama (via the Ollama server" in out
    assert "To measure it: sudo du" in out
    assert len(list_runs()) == 1


def test_scan_json_runs_and_show(fake_scan: None, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["scan", "--json"]) == 0
    assert '"hostname": "reference"' in capsys.readouterr().out
    assert cli.main(["runs"]) == 0
    assert capsys.readouterr().out.strip() == list_runs()[0]
    assert cli.main(["show"]) == 0 and "nvme0n1" in capsys.readouterr().out
    assert cli.main(["show", "latest", "--json"]) == 0 and capsys.readouterr().out.startswith("{")


def test_errors_are_one_line(capsys: pytest.CaptureFixture[str], data_home: Path) -> None:
    assert cli.main(["show", "nope"]) == 2
    assert capsys.readouterr().err.startswith("hone-drive: no run 'nope'")


def test_no_command_scans(fake_scan: None, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == 0
    assert "saved as run" in capsys.readouterr().out
