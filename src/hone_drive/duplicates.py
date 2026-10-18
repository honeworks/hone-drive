"""Byte-identical files across stores (design §5.3, AC12). Only equal bytes make a duplicate: the same
model in two formats is two different files (history §4)."""

from __future__ import annotations

import hashlib
import os
from collections import defaultdict

from hone_drive.model import Duplicate, Store

MIN_BYTES = 100 * 1000**2
CHUNK = 16 * 1024 * 1024


def sha256_of(path: str) -> str | None:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            while chunk := handle.read(CHUNK):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _ollama_digest(path: str) -> str | None:
    """An Ollama blob's name is its SHA-256 (`sha256-<hex>`), so it needs no hashing."""
    name = os.path.basename(path)
    return name[7:] if name.startswith("sha256-") and len(name) == 71 else None


def _candidates(stores: list[Store]) -> dict[int, list[tuple[str, str]]]:
    """Files over MIN_BYTES by size: (path, store id). Ollama blobs are listed from its blobs folder."""
    by_size: dict[int, list[tuple[str, str]]] = defaultdict(list)
    for store in stores:
        for path in _files(store):
            try:
                size = os.stat(path).st_size
            except OSError:
                continue
            if size >= MIN_BYTES:
                by_size[size].append((path, store.id))
    return by_size


def _files(store: Store) -> list[str]:
    if store.id == "ollama" and store.path and store.source == "files":
        blobs = os.path.join(store.path, "blobs")
        return [os.path.join(blobs, n) for n in os.listdir(blobs)] if os.path.isdir(blobs) else []
    return [i.path for i in store.items if i.path and i.files == 1 and os.path.isfile(i.path)]


def _identity(path: str) -> tuple[int, int] | str:
    """Two paths to one file (a symlink or a hard link) are not a duplicate."""
    try:
        info = os.stat(path)
    except OSError:
        return path
    return (info.st_dev, info.st_ino) if info.st_ino else os.path.realpath(path)


def find_duplicates(stores: list[Store]) -> list[Duplicate]:
    """Hash only files whose size matches a file in another store; group by content."""
    groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for files in _candidates(stores).values():
        if len({_identity(p) for p, _ in files}) < 2 or len({s for _, s in files}) < 2:
            continue
        for path, store_id in files:
            digest = _ollama_digest(path) or sha256_of(path)
            if digest is not None:
                groups[digest].append((path, store_id))
    found: list[Duplicate] = []
    for digest, files in groups.items():
        if len({_identity(p) for p, _ in files}) > 1:
            found.append(
                Duplicate(
                    sha256=digest,
                    bytes=os.stat(files[0][0]).st_size,
                    paths=sorted(p for p, _ in files),
                    stores=sorted({s for _, s in files}),
                )
            )
    return sorted(found, key=lambda d: -d.bytes)
