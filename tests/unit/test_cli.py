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


def test_bench_command_on_a_stored_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.unit.test_benchmark import local_run

    run = local_run(tmp_path)
    from hone_drive.runs import save_run

    save_run(run)
    assert cli.main(["bench", str(tmp_path), "--size", "16M", "--run", run.id]) == 0
    out = capsys.readouterr().out
    assert "seq-write" in out and "seq-read" in out and "saved as run" in out
    assert cli.main(["bench", str(tmp_path), "--size", "16M", "--run", run.id, "--json"]) == 0
    assert '"seq-read"' in capsys.readouterr().out
    assert cli.main(["show", run.id]) == 0 and "seq-read" in capsys.readouterr().out


def test_parse_size() -> None:
    assert cli.parse_size("4G") == 4 * 1024**3
    assert cli.parse_size("512MiB") == 512 * 1024**2
    assert cli.parse_size("1.5g") == int(1.5 * 1024**3)
    assert cli.parse_size("4096") == 4096


def test_advise_compare_report_and_dashboard(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from hone_drive.runs import save_run
    from tests.unit.test_advice import reference_run

    a, b = reference_run(), reference_run()
    a.id, b.id = "20261018T000000Z-aaaaaa", "20261018T010000Z-bbbbbb"
    b.volumes[0].used_bytes -= 10**11
    save_run(a)
    save_run(b)
    assert cli.main(["advise", a.id]) == 0
    out = capsys.readouterr().out
    assert "[A01 warn] / is 89% full" in out and "    $ sudo systemctl stop ollama" in out and "    ! " in out
    assert cli.main(["advise", "--json"]) == 0 and capsys.readouterr().out.startswith('[{"unknown"')
    assert cli.main(["compare", a.id]) == 0 and "volume  /:" in capsys.readouterr().out
    assert cli.main(["compare", a.id, a.id]) == 0 and "No changes" in capsys.readouterr().out
    assert cli.main(["report"]) == 0 and "# hone-drive report" in capsys.readouterr().out
    served: list[tuple[int, bool]] = []
    monkeypatch.setattr(
        "hone_drive.dashboard.serve", lambda port, open_browser: served.append((port, open_browser))
    )
    assert cli.main(["dashboard", "--port", "8800", "--no-browser"]) == 0
    assert cli.main([]) == 0
    assert served == [(8800, False), (0, True)]


def test_advise_with_nothing_to_say(capsys: pytest.CaptureFixture[str]) -> None:
    from hone_drive.runs import save_run

    run = real_scan(probe=reference_probe(), finders={})
    run.volumes[0].used_bytes = run.volumes[0].total_bytes // 2
    run.drives[1].link.limited = False
    save_run(run)
    assert cli.main(["advise"]) == 0
    assert "No advice" in capsys.readouterr().out
