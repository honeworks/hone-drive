"""The shelf with the real Ollama (change 0003, "Tests": `-m real`). Not in the default suite or CI: it
starts a private `ollama serve` as this user (its own port and model folder, never the user's Ollama),
pulls a small model from the network, and loads it, so run it by hand under the machine-wide lock:

    scripts/gpu-lock.sh uv run pytest -m real

It moves the model to a "drive" folder, checks Ollama no longer lists it, brings it back, and checks Ollama
lists it with the same digest and answers with it."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from hone_drive import shelf
from hone_drive.filesystems import traits
from hone_drive.model import Drive, Link, Volume
from hone_drive.scanner import scan
from hone_drive.store_ollama import find_ollama
from hone_drive.stores import Context
from hone_drive.testing import FakeProbe, reference_probe
from hone_drive.volumes import disk_usage

pytestmark = pytest.mark.real
MODEL = os.environ.get("HONE_DRIVE_REAL_MODEL", "all-minilm:22m")
PORT = "127.0.0.1:11555"
URL = f"http://{PORT}"
ROOT = Path.home() / ".cache" / "hone-drive-real-test"  # on the real disk: /tmp is a small RAM disk


def call(path: str, body: dict[str, Any] | None = None, method: str = "POST") -> dict[str, Any]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{URL}{path}", data=data, method=method if data else "GET")  # noqa: S310
    with urllib.request.urlopen(request, timeout=600) as response:  # noqa: S310 - the private server
        text = response.read().decode()
    return json.loads(text.strip().splitlines()[-1]) if text.strip() else {}


@pytest.fixture
def ollama(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    if shutil.which("ollama") is None:
        pytest.skip("ollama is not installed")
    if not os.environ.get("HONE_GPU_LOCK_HELD"):
        pytest.skip("run it through scripts/gpu-lock.sh: it loads a model")
    shutil.rmtree(ROOT, ignore_errors=True)
    models = ROOT / "models"
    models.mkdir(parents=True)
    env = {**os.environ, "OLLAMA_HOST": PORT, "OLLAMA_MODELS": str(models)}
    server = subprocess.Popen(
        ["ollama", "serve"],  # noqa: S607 - the ollama on PATH is the one under test
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                call("/api/version", method="GET")
                break
            except OSError:
                time.sleep(0.2)
        call("/api/pull", {"model": MODEL, "stream": False})
        monkeypatch.setattr("hone_drive.store_ollama.DEFAULTS", {"linux": [], "macos": [], "windows": []})
        yield models
    finally:
        server.terminate()
        server.wait(30)
        shutil.rmtree(ROOT, ignore_errors=True)


def machine(models: Path) -> tuple[Context, Any]:
    drive = ROOT / "drive"
    drive.mkdir(exist_ok=True)
    ctx = Context(
        os_name="linux", home=str(Path.home()), env={"OLLAMA_MODELS": str(models), "OLLAMA_HOST": PORT}
    )
    volumes = []
    for path, drive_id in ((models, "int"), (drive, "ext")):
        total, used, free = disk_usage(str(path))
        volumes.append(
            Volume(mount=str(path), device=str(path), drive_id=drive_id, filesystem="ext4", total_bytes=total,
                   used_bytes=used, free_bytes=free, traits=traits("ext4", "linux"))
        )  # fmt: skip
    link = Link(bus="usb")
    drives = [
        Drive(id="int", kind="nvme", external=False, link=link),
        Drive(id="ext", kind="usb-ssd", external=True, link=link),
    ]
    probe = FakeProbe(reference_probe().facts, drives, volumes)
    return ctx, lambda: scan(probe=probe, context=ctx, finders={"ollama": find_ollama})


def listed() -> dict[str, str]:
    return {m["name"]: m["digest"] for m in call("/api/tags", method="GET")["models"]}


def test_a_real_ollama_model_leaves_and_comes_back(ollama: Path) -> None:
    ctx, run = machine(ollama)
    before = listed()
    name = next(n for n in before if n.startswith(MODEL.split(":")[0]))
    key = f"ollama:{name}"
    the_run = run()
    the_plan = shelf.plan(the_run, [key], to=str(ROOT / "drive"), ctx=ctx, busy=set)
    assert the_plan.models[0].refused is None, the_plan
    assert shelf.move(the_run, the_plan, ctx=ctx).done == [key]
    assert name not in listed()  # Ollama itself no longer has it
    assert not list((ollama / "blobs").iterdir())  # and its blobs are gone from the computer
    assert shelf.restore(run(), [key]).done == [key]
    assert listed().get(name) == before[name]  # listed again with the same digest
    answer = call("/api/embed", {"model": name, "input": "hello", "keep_alive": 0})
    assert answer["embeddings"] and len(answer["embeddings"][0]) > 10
