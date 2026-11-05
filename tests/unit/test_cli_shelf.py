"""`hone-drive shelf` (change 0003)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hone_drive import cli, cli_shelf
from hone_drive.errors import NotFound
from tests.shelf_machine import Machine, build

WAN = "comfyui:diffusion_models/wan.safetensors"


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Machine:
    found = build(tmp_path, monkeypatch)
    monkeypatch.setattr(cli_shelf, "_shelf_run", found.run)
    return found


def test_list_move_restore_verify(machine: Machine, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["shelf"]) == 0 and "No models on any drive yet" in capsys.readouterr().out
    assert cli.main(["shelf", "move", "wan.safetensors", "--dry-run"]) == 0
    assert f"✓ {WAN}" in capsys.readouterr().out
    assert cli.main(["shelf", "move", "gemma:31b", "wan.safetensors", "--to", str(machine.drive)]) == 0
    assert "done: ollama:gemma:31b" in capsys.readouterr().out
    assert cli.main(["shelf"]) == 0
    listed = capsys.readouterr().out
    assert "PNY RP60 PSSD" in listed and f"{WAN}  (only here)" in listed
    assert cli.main(["shelf", "--json"]) == 0 and WAN in json.loads(capsys.readouterr().out)[0]["models"]
    assert cli.main(["shelf", "restore", WAN, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["result"]["done"] == [WAN]
    assert cli.main(["shelf", "verify"]) == 0 and "every copy matches" in capsys.readouterr().out
    assert cli.main(["runs"]) == 0 and capsys.readouterr().out.strip()  # what a move left is saved


def test_refusals_and_check(machine: Machine, capsys: pytest.CaptureFixture[str]) -> None:
    machine.ollama.loaded = ["gemma:31b"]
    assert cli.main(["shelf", "move", "gemma:31b"]) == 1
    assert "✗ ollama:gemma:31b: Ollama has gemma:31b loaded" in capsys.readouterr().out
    assert cli.main(["shelf", "check", "comfyui"]) == 0 and "comfyui: ready" in capsys.readouterr().out
    assert cli.main(["shelf", "restore", "wan.safetensors"]) == 2  # on no drive
    assert "is on no drive" in capsys.readouterr().err


def test_names_resolve_to_one_model(machine: Machine) -> None:
    run = machine.run()
    assert cli_shelf.resolve(run, ["tiny:latest", WAN]) == ["ollama:tiny:latest", WAN]
    with pytest.raises(NotFound, match="no single model"):
        cli_shelf.resolve(run, ["nothing"])
