"""The shelf with the real Ollama (change 0003, `-m real`), on Linux, macOS and Windows.

- `private` (everywhere): a private `ollama serve` as this user, on its own port and folder, never the
  user's own Ollama.
- `system` (`HONE_DRIVE_REAL_OLLAMA=system`; CI installs Ollama on Linux with its own installer, as a
  service of its own user, like the reference machine): the Ollama this machine runs. hone-drive can't
  read its folder, says so with the fix for this system, CI runs the fix, and then the model moves.

Each pulls a small model, moves it to the drive, checks Ollama no longer lists it and its files are gone,
brings it back, and checks Ollama lists it with the same digest and answers with it.

    scripts/gpu-lock.sh uv run pytest -m real"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from hone_drive import shelf, shelf_restore
from hone_drive.model import Run
from hone_drive.scanner import os_name
from hone_drive.shelf_busy import Busy
from hone_drive.store_ollama import find_ollama, read_manifests
from hone_drive.stores import Context
from tests.real.real_machine import ROOT, allowed, drive_folder, machine, run_fix

pytestmark = pytest.mark.real
MODEL = os.environ.get("HONE_DRIVE_REAL_MODEL", "all-minilm:22m")
MODE = os.environ.get("HONE_DRIVE_REAL_OLLAMA", "private")


@dataclass
class Ollama:
    url: str
    ctx: Context
    run: Callable[[], Run]
    drive: Path
    models: Path | None  # the folder, when this user can see it

    def call(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(  # noqa: S310 - a local server
            f"{self.url}{path}", data=data, method="POST" if data else "GET"
        )
        with urllib.request.urlopen(request, timeout=900) as response:  # noqa: S310
            text = response.read().decode().strip()
        return json.loads(text.splitlines()[-1]) if text else {}

    def listed(self) -> dict[str, str]:
        return {m["name"]: m["digest"] for m in self.call("/api/tags")["models"]}


def wait(url: str) -> None:
    for _ in range(150):
        try:
            urllib.request.urlopen(f"{url}/api/version", timeout=2).close()  # noqa: S310
            return
        except OSError:
            time.sleep(0.2)
    raise AssertionError(f"Ollama did not answer at {url}")


@pytest.fixture
def ollama(monkeypatch: pytest.MonkeyPatch) -> Iterator[Ollama]:
    allowed()
    if shutil.which("ollama") is None:
        pytest.skip("ollama is not installed")
    drive = drive_folder()
    if MODE == "system":
        yield system(drive)
        return
    shutil.rmtree(ROOT / "models", ignore_errors=True)
    models = ROOT / "models"
    models.mkdir(parents=True)
    host = "127.0.0.1:11555"
    env = {**os.environ, "OLLAMA_HOST": host, "OLLAMA_MODELS": str(models)}
    server = subprocess.Popen(
        ["ollama", "serve"],  # noqa: S607 - the ollama on PATH is the one under test
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait(f"http://{host}")
        monkeypatch.setattr("hone_drive.store_ollama.DEFAULTS", {"linux": [], "macos": [], "windows": []})
        ctx = Context(
            os_name=os_name(), home=str(Path.home()), env={"OLLAMA_MODELS": str(models), "OLLAMA_HOST": host}
        )
        found = Ollama(
            f"http://{host}", ctx, machine([models], drive, {"ollama": find_ollama}, ctx), drive, models
        )
        found.call("/api/pull", {"model": MODEL, "stream": False})
        yield found
    finally:
        server.terminate()
        server.wait(30)
        shutil.rmtree(ROOT, ignore_errors=True)
        shutil.rmtree(drive, ignore_errors=True)


def system(drive: Path) -> Ollama:
    """This machine's own Ollama, wherever its installer put its models."""
    url = "http://127.0.0.1:11434"
    wait(url)
    ctx = Context(os_name=os_name(), home=str(Path.home()))
    subprocess.run(["ollama", "pull", MODEL], check=True, timeout=900)  # noqa: S607
    stores = find_ollama(ctx)
    assert stores and stores[0].path, "the scan found no Ollama folder"
    home = Path(os.path.abspath(os.sep))  # the system drive holds the service's folder
    return Ollama(url, ctx, machine([home], drive, {"ollama": find_ollama}, ctx), drive, None)


def manifests(ollama: Ollama) -> dict[str, tuple[str, ...]]:
    """Each name Ollama has, in either of its folders, and the config and layers it lists: the model."""
    root = ollama.models or Path(next(s.path for s in ollama.run().stores if s.id == "ollama" and s.path))
    return {m.name: m.digests for m in read_manifests(str(root))}


def key_of(ollama: Ollama) -> tuple[str, str]:
    name = next(n for n in ollama.listed() if n.startswith(MODEL.split(":")[0]))
    return name, f"ollama:{name}"


@pytest.mark.parametrize("route", ["auto", "always"])
def test_a_real_ollama_model_leaves_and_comes_back(
    ollama: Ollama, route: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`auto`: back through the files when the folder is writable, through Ollama's server when not (the
    service's folder); `always`: through Ollama's server, which pulls from hone-drive's registry."""
    monkeypatch.setattr(shelf_restore, "VIA_SERVER", route)
    before = ollama.listed()
    name, key = key_of(ollama)
    version = ollama.call("/api/version").get("version")
    access = shelf.check(ollama.run(), "ollama")
    if not access.ok:  # the service's own folder: what the reference machine needs
        assert access.fix and not access.ok
        run_fix(access.fix)
        assert shelf.check(ollama.run(), "ollama").ok, "the fix shown did not give access"
    on_disk = manifests(ollama)
    the_run = ollama.run()
    the_plan = shelf.plan(the_run, [key], to=str(ollama.drive), ctx=ollama.ctx, busy=Busy)
    assert the_plan.models[0].refused is None, the_plan
    moved = shelf.move(the_run, the_plan, ctx=ollama.ctx)
    assert moved.done == [key], moved
    assert name not in ollama.listed()  # Ollama itself no longer has it
    if ollama.models is not None:
        assert not list((ollama.models / "blobs").iterdir())  # and its blobs left the computer
    restored = shelf.restore(ollama.run(), [key], ctx=ollama.ctx)
    assert restored.done == [key], restored
    after = ollama.listed()
    assert name in after, (version, before, after)
    # the same model: the same names, each with the same config and layers (the bytes of a manifest and
    # Ollama's listing digest can differ: since 0.40 Ollama writes a pulled manifest in its own form)
    assert manifests(ollama) == on_disk, (version, before, after)
    answer = ollama.call("/api/embed", {"model": name, "input": "hello", "keep_alive": 0})
    assert answer["embeddings"] and len(answer["embeddings"][0]) > 10
    removed = shelf.remove(ollama.run(), [key])
    assert removed.done == [key], removed  # on the computer again: the drive may let go
