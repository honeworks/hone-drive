"""Ollama's model store (design §5.1). Models are read from the manifests when the folder is readable;
when it belongs to the Ollama service's own user, from the local Ollama server's API instead (decision
D-010). Names that share a manifest are one item, counted once (design §5.2, AC4)."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
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
        if os.path.isdir(os.path.join(path, "manifests")):
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


def _name(models: str, manifest: str) -> str:
    rel = os.path.relpath(manifest, os.path.join(models, "manifests")).replace(os.sep, "/")
    host_and_path, _, tag = rel.rpartition("/")
    if host_and_path.startswith("registry.ollama.ai/library/"):
        return f"{host_and_path.removeprefix('registry.ollama.ai/library/')}:{tag}"
    return f"{host_and_path.removeprefix('registry.ollama.ai/')}:{tag}"


def _from_files(models: str, ctx: Context) -> Store:
    by_id: dict[str, Item] = {}
    referenced: set[str] = set()
    manifests_dir = os.path.join(models, "manifests")
    for folder, _, names in os.walk(manifests_dir):
        for name in names:
            path = os.path.join(folder, name)
            try:
                raw = Path(path).read_bytes()
                data = cast(dict[str, Any], json.loads(raw))
            except (OSError, ValueError):
                continue
            config = cast(dict[str, Any], data.get("config") or {})
            layers = [config, *cast(list[dict[str, Any]], data.get("layers") or [])]
            digests = {str(layer.get("digest", "")) for layer in layers if layer.get("digest")}
            referenced |= digests
            model_id = hashlib.sha256(raw).hexdigest()[:12]
            item = by_id.setdefault(
                model_id,
                Item(
                    id=model_id,
                    names=[],
                    bytes=sum(int(layer.get("size", 0)) for layer in layers),
                    files=len(digests),
                    path=path,
                    details={"digests": ",".join(sorted(digests))},
                ),
            )
            item.names.append(_name(models, path))
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


def _from_api(ctx: Context) -> Store | None:
    host = ctx.env.get("OLLAMA_HOST", "127.0.0.1:11434")
    host = host if host.startswith("http") else f"http://{host}"
    host = host.replace("://0.0.0.0", "://127.0.0.1")
    raw = ctx.fetch(f"{host.rstrip('/')}/api/tags")
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
