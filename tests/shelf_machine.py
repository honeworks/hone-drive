"""A machine for the shelf's tests (change 0003): a home folder with Ollama, Hugging Face and ComfyUI
models, an external "drive" folder, a fake Ollama server, and nothing that reaches this machine's own
tools (a test must never ask the real Ollama to delete a model)."""

from __future__ import annotations

import hashlib
import json
import os
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
    """Answers `/api/ps` with what is loaded and records the deletes it is asked for."""

    loaded: list[str] = field(default_factory=list[str])
    deleted: list[str] = field(default_factory=list[str])
    comfyui_running: bool = False

    def fetch(self, url: str) -> bytes | None:
        if url.endswith("/api/ps"):
            return json.dumps({"models": [{"name": n, "model": n} for n in self.loaded]}).encode()
        if url.endswith("/system_stats") and self.comfyui_running:
            return b"{}"
        return None

    def send(self, method: str, url: str, body: dict[str, object] | None) -> bytes | None:
        if method == "DELETE" and url.endswith("/api/delete") and body:
            self.deleted.append(str(body["model"]))
        return b"{}"


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
    fake = FakeOllama()
    ctx = Context(
        os_name="linux", home=str(home), env={"HOME": str(home)}, run=FakeRunner(), fetch=fake.fetch,
        send=fake.send,
    )  # fmt: skip
    monkeypatch.setattr(shelf, "context", lambda _run: ctx)
    monkeypatch.setattr("hone_drive.shelf_move.context", lambda _run: ctx)
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
