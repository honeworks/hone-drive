"""Recording this machine as a fixture (hone_drive.testing.record) and replaying it."""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from hone_drive.scanner import os_name
from hone_drive.testing import check_probe, main, recorded_probe, redact


def test_record_and_replay_this_machine(tmp_path: Path) -> None:
    out = tmp_path / "machine.json"
    assert main([str(out)]) == 0
    data = json.loads(out.read_text())
    assert data["os"] == os_name()
    assert socket.gethostname() not in out.read_text() or socket.gethostname() == "host"
    probe = recorded_probe(data)
    check_probe(probe)
    assert [v.mount for v in probe.volumes()]


def test_redact_replaces_private_details() -> None:
    data = {
        "hostname": "deadman",
        "files": {
            "/proc/cpuinfo": "model name : X\n\nprocessor : 1\n",
            "/x": "SN123 /home/someone/a deadman 97876789-fede-40b1-8b50-e88abc490d09",
        },
    }
    clean = redact(data, ["SN123"])
    text = json.dumps(clean)
    assert "SN123" not in text and "deadman" not in text and "97876789" not in text
    assert clean["files"]["/proc/cpuinfo"] == "model name : X\n"


def test_main_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "usage" in capsys.readouterr().err
