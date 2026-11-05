"""A model in use is refused (change 0003, AC20), checked with real programs: one with the file open, one
with it mapped into memory and closed (as llama.cpp does), and a ComfyUI started on a port of its own."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from hone_drive import shelf, shelf_busy
from hone_drive.access import held_elsewhere
from tests.shelf_machine import Machine, build

WAN = "comfyui:diffusion_models/wan.safetensors"
HOLD = """
import mmap, sys, time
path, how = sys.argv[1], sys.argv[2]
handle = open(path, "rb")
if how == "map":
    mapped = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
    handle.close()  # only the mapping keeps it
print("ready", flush=True)
time.sleep(60)
"""


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Machine:
    return build(tmp_path, monkeypatch)


@contextmanager
def running(*args: str) -> Iterator[subprocess.Popen[str]]:
    proc = subprocess.Popen([sys.executable, *args], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout is not None and proc.stdout.readline().strip() == "ready"
        yield proc
    finally:
        proc.kill()
        proc.wait(10)


def refusal(machine: Machine) -> str | None:
    run = machine.run()
    return shelf.plan(run, [WAN], to=str(machine.drive), busy=shelf_busy.look).models[0].refused


def wan(machine: Machine) -> Path:
    return machine.home / "ComfyUI" / "models" / "diffusion_models" / "wan.safetensors"


def test_nothing_running_nothing_refused(machine: Machine) -> None:
    assert refusal(machine) is None


@pytest.mark.parametrize("how", ["open", "map"])
def test_a_file_a_program_has_open_or_mapped(machine: Machine, how: str) -> None:
    with running("-c", HOLD, str(wan(machine)), how):
        assert "open" in (refusal(machine) or "")
        if sys.platform == "win32" and how == "open":
            assert held_elsewhere(str(wan(machine)))
    assert refusal(machine) is None


def test_comfyui_running_on_any_port(machine: Machine, tmp_path: Path) -> None:
    main = machine.home / "ComfyUI" / "main.py"
    main.write_text("import time\nprint('ready', flush=True)\ntime.sleep(60)\n")
    other = tmp_path / "other-comfy"
    other.mkdir()
    (other / "main.py").write_text(main.read_text())
    with running(str(other / "main.py"), "--port", "8190"):
        assert refusal(machine) is None  # another install running is not this one
    with running(str(main), "--port", "8190", "--listen", "127.0.0.1"):
        assert refusal(machine) == "ComfyUI is running; close it and try again"
