"""A machine for the shelf's tests (change 0003): a home folder with Ollama, Hugging Face and ComfyUI
models, an external "drive" folder, a fake Ollama server, and nothing that reaches this machine's own
tools (a test must never ask the real Ollama to delete a model)."""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from hone_drive import shelf, stores
from hone_drive.filesystems import traits
from hone_drive.model import Drive, Run, Volume
from hone_drive.scanner import scan
from hone_drive.shelf_busy import Busy
from hone_drive.stores import Context
from hone_drive.testing import FakeProbe, reference_probe
from tests.conftest import FakeRunner

KB = 1000


@dataclass
class FakeOllama:
    """Answers `/api/ps` with what is loaded and records the deletes it is asked for. With `running`, it
    also answers `/api/version` and does what Ollama does for a pull from a registry, a copy and a delete
    in `models`, so the shelf's way back through Ollama's server (decision D-024) is tested end to end."""

    loaded: list[str] = field(default_factory=list[str])
    deleted: list[str] = field(default_factory=list[str])
    comfyui_running: bool = False
    running: bool = False
    models: Path | None = None

    def fetch(self, url: str) -> bytes | None:
        if url.endswith("/api/ps"):
            return json.dumps({"models": [{"name": n, "model": n} for n in self.loaded]}).encode()
        if url.endswith("/system_stats") and self.comfyui_running:
            return b"{}"
        if url.endswith("/api/version") and self.running:
            return b'{"version": "fake"}'
        return None

    def send(self, method: str, url: str, body: dict[str, object] | None) -> bytes | None:
        body = body or {}
        if method == "DELETE" and url.endswith("/api/delete"):
            self.deleted.append(str(body["model"]))
            if self.running and self.models:
                manifest_path(self.models, str(body["model"])).unlink(missing_ok=True)
        elif url.endswith("/api/pull") and self.models:
            self._pull(str(body["model"]))
        elif url.endswith("/api/copy") and self.models:
            target = manifest_path(self.models, str(body["destination"]))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(manifest_path(self.models, str(body["source"])).read_bytes())
        return b"{}"

    def _pull(self, name: str) -> None:
        assert self.models is not None
        host, rest = name.split("/", 1)
        repo, tag = rest.rsplit(":", 1)
        with urllib.request.urlopen(f"http://{host}/v2/{repo}/manifests/{tag}") as response:
            raw = response.read()
        data = json.loads(raw)
        for layer in [data["config"], *data["layers"]]:
            digest = str(layer["digest"])
            head = urllib.request.Request(f"http://{host}/v2/{repo}/blobs/{digest}", method="HEAD")
            with urllib.request.urlopen(head) as response:  # noqa: S310
                size = int(response.headers["Content-Length"])
            parts = b""
            step = size // 3 + 1
            for start in range(0, size, step):  # in three ranges, as Ollama downloads big blobs in parts
                ask = urllib.request.Request(f"http://{host}/v2/{repo}/blobs/{digest}")
                ask.add_header("Range", f"bytes={start}-{min(start + step - 1, size - 1)}")
                with urllib.request.urlopen(ask) as response:  # noqa: S310
                    parts += response.read()
            assert hashlib.sha256(parts).hexdigest() == digest.split(":")[1], "a blob came back wrong"
            (self.models / "blobs").mkdir(parents=True, exist_ok=True)
            (self.models / "blobs" / digest.replace(":", "-")).write_bytes(parts)
        target = manifest_path(self.models, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)


def manifest_path(models: Path, name: str) -> Path:
    """Where Ollama keeps a name's manifest: `gemma:31b` is registry.ollama.ai/library/gemma/31b."""
    repo, tag = name.rsplit(":", 1)
    parts = repo.split("/")
    if len(parts) == 1:
        parts = ["registry.ollama.ai", "library", *parts]
    elif len(parts) == 2:
        parts = ["registry.ollama.ai", *parts]
    return models.joinpath("manifests", *parts, tag)


@dataclass
class Machine:
    home: Path
    drive: Path
    ollama: FakeOllama
    ctx: Context

    def run(self) -> Run:
        return scan_machine(self)

    @property
    def models(self) -> Path:
        return self.home / ".ollama" / "models"


def blob(models: Path, content: bytes) -> str:
    digest = hashlib.sha256(content).hexdigest()
    path = models / "blobs" / f"sha256-{digest}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return f"sha256:{digest}"


def manifest(models: Path, name: str, tag: str, digests: list[str]) -> None:
    data = {
        "config": {"digest": digests[0], "size": 1},
        "layers": [{"digest": d, "size": 1} for d in digests[1:]],
    }
    path = models / "manifests" / "registry.ollama.ai" / "library" / name / tag
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Machine:
    """gemma:31b and tiny:latest share a config blob; one Hugging Face repo; one ComfyUI model file."""
    home, drive = tmp_path / "home", tmp_path / "drive"
    drive.mkdir(parents=True)
    models = home / ".ollama" / "models"
    shared = blob(models, b"config shared by two models")
    manifest(models, "gemma", "31b", [shared, blob(models, b"g" * 300 * KB)])
    manifest(models, "tiny", "latest", [shared, blob(models, b"t" * 20 * KB)])
    repo = home / ".cache" / "huggingface" / "hub" / "models--org--name"
    (repo / "blobs").mkdir(parents=True)
    (repo / "blobs" / "abc").write_bytes(b"w" * 200 * KB)
    (repo / "refs").mkdir()
    (repo / "refs" / "main").write_text("0123")
    snapshot = repo / "snapshots" / "0123"
    snapshot.mkdir(parents=True)
    try:
        os.symlink("../../blobs/abc", snapshot / "model.safetensors")
    except OSError:  # Windows without the right to make links: Hugging Face copies instead
        (snapshot / "model.safetensors").write_bytes(b"w" * 200 * KB)
    comfy = home / "ComfyUI"
    (comfy / "models" / "diffusion_models").mkdir(parents=True)
    (comfy / "main.py").write_text("")
    (comfy / "models" / "diffusion_models" / "wan.safetensors").write_bytes(b"c" * 150 * KB)
    return attach(tmp_path, monkeypatch)


def attach(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Machine:
    """The fakes for a machine `build` made (also from another process, for the kill tests)."""
    monkeypatch.setattr(
        "hone_drive.store_ollama.DEFAULTS", {"linux": ["~/.ollama/models"], "macos": [], "windows": []}
    )
    monkeypatch.setattr(stores, "MODEL_FILE_MIN", KB)
    home, drive = tmp_path / "home", tmp_path / "drive"
    fake = FakeOllama(models=home / ".ollama" / "models")
    ctx = Context(
        os_name="linux", home=str(home), env={"HOME": str(home)}, run=FakeRunner(), fetch=fake.fetch,
        send=fake.send,
    )  # fmt: skip
    monkeypatch.setattr(shelf, "context", lambda _run: ctx)
    monkeypatch.setattr("hone_drive.shelf_move.context", lambda _run: ctx)
    monkeypatch.setattr("hone_drive.shelf_restore.context", lambda _run: ctx)
    monkeypatch.setattr("hone_drive.shelf_move.look", Busy)
    return Machine(home, drive, fake, ctx)


def scan_machine(machine: Machine) -> Run:
    facts = reference_probe().facts
    usb = reference_probe().drive_list[1]
    internal = Drive(id="int", kind="nvme", external=False, link=usb.link.model_copy(update={"bus": "pcie"}))
    volumes = [
        _volume(machine.home, "int", "ext4"),
        _volume(machine.drive, usb.id, "exfat"),
    ]
    probe = FakeProbe(facts, [internal, usb], volumes)
    ctx = Context(
        os_name="linux", home=str(machine.home), env=machine.ctx.env, run=FakeRunner(), fetch=lambda _u: None
    )
    return scan(probe=probe, context=ctx)


def _volume(path: Path, drive_id: str, filesystem: str) -> Volume:
    return Volume(
        mount=str(path), device=str(path), drive_id=drive_id, filesystem=filesystem,
        total_bytes=10**12, used_bytes=10**9, free_bytes=10**11, traits=traits(filesystem, "linux"),
    )  # fmt: skip
