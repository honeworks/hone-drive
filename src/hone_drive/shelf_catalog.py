"""The shelf's catalog (change 0003): what is on a drive, each file with its SHA-256, size and the times
it had when it was checked. It lives on the drive (`<drive>/hone-drive/shelf.json`) and, so an unplugged
drive can still be listed, as a copy in the data folder (`shelves/<drive id>.json`)."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import uuid
from collections.abc import Generator
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from hone_drive.bench_safety import lock_file
from hone_drive.errors import ShelfBusy, UnsupportedFormat
from hone_drive.model import CacheBypass
from hone_drive.runs import data_home

FORMAT = 1
FOLDER = "hone-drive"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ShelfFile(Strict):
    rel: str  # under the model's folder on the computer, "/"-separated
    bytes: int
    sha256: str
    drive_ns: int  # the drive copy's modification time (ns) when it was checked
    local_ns: int | None = None  # the computer's file's modification time when it last matched
    cache: CacheBypass = "direct"  # how the read-back of the drive copy bypassed the cache


class ShelfLink(Strict):
    rel: str
    target: str  # as `os.readlink` gave it (Hugging Face: "../../blobs/<hash>")


class ShelfModel(Strict):
    key: str  # "ollama:gemma4:31b", "comfyui:diffusion_models/wan.safetensors"
    tool: str  # the store id
    names: list[str]
    root: str  # the folder on the computer its files are relative to
    files: list[ShelfFile] = Field(default_factory=list[ShelfFile])
    links: list[ShelfLink] = Field(default_factory=list[ShelfLink])
    complete: bool = False  # every file copied and checked; only then may the computer's copy go
    copied: str | None = None  # ISO-8601 UTC
    replaceable: bool = False  # can be downloaded again (Ollama's library, Hugging Face)


class Shelf(Strict):
    format: int = FORMAT
    drive: str  # the id in drive.json
    name: str  # the drive's name, as the dashboard says it
    mount: str  # where it was mounted when last written
    models: dict[str, ShelfModel] = Field(default_factory=dict[str, ShelfModel])


def folder(mount: str) -> Path:
    return Path(mount, FOLDER)


def root_id(root: str) -> str:
    return hashlib.sha256(root.encode()).hexdigest()[:12]


def drive_file(mount: str, model: ShelfModel, rel: str) -> str:
    """Where a file of `model` lives on the drive. Files of one tool's folder share a place, so an Ollama
    blob two models list is stored once."""
    return str(folder(mount).joinpath("files", model.tool, root_id(model.root), *rel.split("/")))


def drive_id(mount: str, create: bool = False) -> str | None:
    path = folder(mount) / "drive.json"
    try:
        return str(json.loads(path.read_text(encoding="utf-8"))["id"])
    except (OSError, ValueError, KeyError, TypeError):
        if not create:
            return None
    found = uuid.uuid4().hex
    _write(path, json.dumps({"format": FORMAT, "id": found}))
    return found


def load(mount: str) -> Shelf | None:
    return _read(folder(mount) / "shelf.json")


def open_shelf(mount: str, name: str) -> Shelf:
    """The drive's shelf, created (with the drive's id) the first time."""
    shelf = load(mount)
    if shelf is None:
        shelf = Shelf(drive=drive_id(mount, create=True) or "", name=name, mount=mount)
    shelf.mount, shelf.name = mount, name
    return shelf


def save(shelf: Shelf, home: Path | None = None) -> None:
    """On the drive first (the truth), then the copy in the data folder."""
    text = shelf.model_dump_json(indent=1)
    _write(folder(shelf.mount) / "shelf.json", text)
    _write(copies(home) / f"{shelf.drive}.json", text)


def copies(home: Path | None = None) -> Path:
    return (home or data_home()) / "shelves"


def known(home: Path | None = None) -> list[Shelf]:
    """Every shelf this computer has written or seen, from the copies in the data folder."""
    found = [_read(p) for p in sorted(copies(home).glob("*.json"))] if copies(home).is_dir() else []
    return [s for s in found if s is not None]


@contextlib.contextmanager
def locked(mount: str) -> Generator[None]:
    """One writer per drive, also across computers (change 0003, "Interruptions and two at once")."""
    with lock_file(folder(mount) / "shelf.lock", ShelfBusy, "another move is changing this drive"):
        yield


def _read(path: Path) -> Shelf | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if int(data.get("format", 0)) > FORMAT:
        raise UnsupportedFormat(f"{path} was written by a newer hone-drive; upgrade to read it")
    return Shelf.model_validate(data)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".shelf-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
