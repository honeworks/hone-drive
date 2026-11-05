"""Ollama's model store (design §5.1). Models are read from the manifests when the folder is readable;
when it belongs to the Ollama service's own user, from the local Ollama server's API instead (decision
D-010). Names that share a manifest are one item, counted once (design §5.2, AC4)."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Any, cast

from hone_drive.model import Item, Store
from hone_drive.sizing import walk
from hone_drive.stores import Context, blocked, candidates_for

TITLE = "Ollama models"
DEFAULTS = {
    "linux": ["~/.ollama/models", "/usr/share/ollama/.ollama/models", "/var/lib/ollama/.ollama/models"],
    "macos": ["~/.ollama/models"],
    "windows": ["~/.ollama/models"],
}


def find_ollama(ctx: Context) -> list[Store]:
    stores: list[Store] = []
    hidden: list[str] = []
    for path in _locations(ctx):
        if any(os.path.isdir(os.path.join(path, top)) for top in FOLDERS):
            stores.append(_from_files(path, ctx))
        elif blocked(path):
            hidden.append(path)
    api = _from_api(ctx)
    if api is not None and not _same_models(api, stores):
        api.path = hidden[0] if hidden else None
        api.unreadable = hidden[:1]
        if api.path is None:
            api.unknown["path"] = "the Ollama server answered, but its model folder was not found"
        stores.append(api)
    elif hidden and api is None:
        stores.append(
            Store(
                id="ollama",
                title=TITLE,
                path=hidden[0],
                unreadable=hidden[:1],
                source="files",
                unknown={"bytes": "the folder belongs to another user and the Ollama server did not answer"},
            )
        )
    return stores


def _locations(ctx: Context) -> list[str]:
    paths = candidates_for(ctx, ["$OLLAMA_MODELS"])
    if ctx.os_name == "linux":
        unit = ctx.run(["systemctl", "cat", "ollama.service"], 5.0)
        match = re.search(r"OLLAMA_MODELS=([^\"\s]+)", unit.stdout) if unit.ok else None
        if match:
            paths.append(match.group(1))
    return list(dict.fromkeys(paths + candidates_for(ctx, DEFAULTS[ctx.os_name])))


FOLDERS = ("manifests", "manifests-v2")  # since 0.40 Ollama also keeps manifests-v2 (decision D-025)
LIBRARY_HOSTS = ("registry.ollama.ai", "ollama.com")


@dataclass(frozen=True)
class Manifest:
    """One name's manifest. In `manifests/` it is a file; in `manifests-v2/` (Ollama 0.40 and later) a
    link to a blob holding the manifest as the registry sent it (a copy where links can't be made)."""

    rel: str  # "/"-separated, under the models folder
    name: str  # "gemma4:31b", "user/model:tag", "hf.co/user/model:Q4"
    digests: tuple[str, ...]  # the config and the layers, "sha256:<hex>", in order
    sizes: tuple[int, ...]
    blob: str | None = None  # manifests-v2: the blob it links to, "blobs/sha256-<hex>"
    link: str | None = None  # manifests-v2: the link's target, as the OS gave it

    @property
    def model(self) -> str:
        """The same id for every name of one model, in either folder: its config and layers."""
        return hashlib.sha256("\n".join(self.digests).encode()).hexdigest()[:12]


def read_manifests(models: str) -> list[Manifest]:
    """Every manifest in `models`, in both folders; unreadable or broken ones are left out."""
    found: list[Manifest] = []
    for top in FOLDERS:
        for folder, _, names in os.walk(os.path.join(models, top)):
            for file_name in names:
                path = os.path.join(folder, file_name)
                manifest = _read(models, path)
                if manifest is not None:
                    found.append(manifest)
    return found


def _read(models: str, path: str) -> Manifest | None:
    try:
        with open(path, "rb") as handle:  # follows a manifests-v2 link to its blob
            data = cast(dict[str, Any], json.loads(handle.read()))
    except (OSError, ValueError):
        return None
    config = cast(dict[str, Any], data.get("config") or {})
    layers = [config, *cast(list[dict[str, Any]], data.get("layers") or [])]
    rel = os.path.relpath(path, models).replace(os.sep, "/")
    link = os.readlink(path) if os.path.islink(path) else None
    blob = None
    if link is not None:
        blob = "blobs/" + os.path.basename(link.replace("\\", "/"))
    return Manifest(
        rel=rel,
        name=name_of(rel),
        digests=tuple(str(layer.get("digest", "")) for layer in layers if layer.get("digest")),
        sizes=tuple(int(layer.get("size", 0)) for layer in layers if layer.get("digest")),
        blob=blob,
        link=link.replace(os.sep, "/") if link is not None else None,
    )


def name_of(rel: str) -> str:
    """The name Ollama shows for a manifest's path: `manifests/registry.ollama.ai/library/gemma/31b` and
    `manifests-v2/ollama.com/library/gemma/31b` are `gemma:31b`."""
    _, host, *repo, tag = rel.split("/")
    if host in LIBRARY_HOSTS:
        return f"{'/'.join(repo).removeprefix('library/')}:{tag}"
    return f"{host}/{'/'.join(repo)}:{tag}"


def _from_files(models: str, ctx: Context) -> Store:
    by_id: dict[str, Item] = {}
    referenced: set[str] = set()
    for manifest in read_manifests(models):
        referenced |= set(manifest.digests)
        if manifest.blob:
            referenced.add(manifest.blob.removeprefix("blobs/").replace("-", ":", 1))
        unique = dict(zip(manifest.digests, manifest.sizes, strict=True))
        item = by_id.setdefault(
            manifest.model,
            Item(
                id=manifest.model,
                names=[],
                bytes=sum(unique.values()),
                files=len(unique),
                path=os.path.join(models, *manifest.rel.split("/")),
                details={"digests": ",".join(sorted(unique))},
            ),
        )
        if manifest.name not in item.names:
            item.names.append(manifest.name)
    tally = walk(models, deadline=ctx.deadline(), one_device=False)
    items = sorted(by_id.values(), key=lambda i: -(i.bytes or 0))
    orphans = _orphans(models, referenced)
    if orphans:
        items.append(
            Item(
                id="(unreferenced blobs)",
                names=["blobs no model uses"],
                bytes=orphans,
                path=os.path.join(models, "blobs"),
            )
        )
    for item in items:
        item.names.sort()
    return Store(
        id="ollama",
        title=TITLE,
        path=models,
        bytes=tally.bytes,
        items=items,
        unreadable=tally.unreadable[:20],
        partial=tally.partial,
    )


def _orphans(models: str, referenced: set[str]) -> int:
    blobs = os.path.join(models, "blobs")
    total = 0
    for name in os.listdir(blobs) if os.path.isdir(blobs) else []:
        if name.replace("sha256-", "sha256:", 1) not in referenced:
            try:
                total += os.stat(os.path.join(blobs, name)).st_size
            except OSError:
                continue
    return total


def server(env: dict[str, str]) -> str:
    """The local Ollama server's address (`OLLAMA_HOST`, default 127.0.0.1:11434), without a slash."""
    host = env.get("OLLAMA_HOST", "127.0.0.1:11434")
    host = host if host.startswith("http") else f"http://{host}"
    return host.replace("://0.0.0.0", "://127.0.0.1").rstrip("/")


def _from_api(ctx: Context) -> Store | None:
    raw = ctx.fetch(f"{server(ctx.env)}/api/tags")
    if raw is None:
        return None
    try:
        models = cast(list[dict[str, Any]], json.loads(raw).get("models") or [])
    except (ValueError, AttributeError):
        return None
    by_digest: dict[str, Item] = {}
    for model in models:
        digest = str(model.get("digest", ""))
        details = cast(dict[str, Any], model.get("details") or {})
        item = by_digest.setdefault(
            digest,
            Item(
                id=digest[:12],
                names=[],
                bytes=int(model.get("size", 0)),
                details={
                    k: str(details[k])
                    for k in ("family", "parameter_size", "quantization_level", "format")
                    if details.get(k)
                },
                accessed=None,
                unknown={"path": "the folder is not readable; sizes come from the Ollama server"},
            ),
        )
        item.names.append(str(model.get("name", digest[:12])))
    items = sorted(by_digest.values(), key=lambda i: -(i.bytes or 0))
    for item in items:
        item.names.sort()
    return Store(
        id="ollama", title=TITLE, bytes=sum(i.bytes or 0 for i in items), items=items, source="ollama api"
    )


def _same_models(api: Store, stores: list[Store]) -> bool:
    """True when a readable store already lists the server's models (the user runs Ollama themselves)."""
    names = {n for item in api.items for n in item.names}
    return any(names and names <= {n for item in s.items for n in item.names} for s in stores)
