"""What one model is for each tool, whether it is in use, and how it leaves the computer (change 0003,
"What a model is, per tool"). Ollama: a manifest and its blobs; Hugging Face: a repo folder; ComfyUI,
LM Studio, llama.cpp and the user's own folders: one file."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, cast

from hone_drive.errors import ShelfRefused
from hone_drive.model import Item, Run, Store
from hone_drive.store_ollama import server
from hone_drive.stores import Context

FILE_TOOLS = ("comfyui", "lmstudio", "llamacpp", "custom")
TOOLS = ("ollama", "huggingface", *FILE_TOOLS)


@dataclass
class LocalModel:
    key: str
    tool: str
    names: list[str]
    root: str  # the folder its files are relative to
    files: list[str] = field(default_factory=list[str])  # "/"-separated, under root
    links: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])  # (rel, target)
    bytes: int = 0
    replaceable: bool = False
    readable: bool = True  # False: sized by the Ollama server, its folder not readable
    install: str | None = None  # ComfyUI: the install folder (with main.py), to see whether it runs

    def path(self, rel: str) -> str:
        return os.path.join(self.root, *rel.split("/"))

    def folders(self) -> list[str]:
        """The folders a move or a restore reads, writes and removes files in."""
        found = {os.path.dirname(self.path(rel)) for rel in self.files} | {self.root}
        return sorted(found)


def key_of(store: Store, item: Item) -> str:
    return f"{store.id}:{item.names[0] if store.id == 'ollama' else item.id}"


def local_models(run: Run) -> dict[str, LocalModel]:
    """Every model on this computer that can go on the shelf, by key."""
    found: dict[str, LocalModel] = {}
    for store in run.stores:
        if store.id not in TOOLS:
            continue
        for item in store.items:
            model = _describe(store, item)
            if model is not None:
                found[model.key] = model
    return found


def _describe(store: Store, item: Item) -> LocalModel | None:
    if item.id.startswith("(") or not item.names:
        return None
    if store.id == "ollama":
        return _ollama(store, item)
    if store.id == "huggingface":
        return _huggingface(store, item)
    if item.path and os.path.isfile(item.path):
        root, name = os.path.split(item.path)
        model = LocalModel(key_of(store, item), store.id, item.names, root, [name], bytes=item.bytes or 0)
        if store.id == "comfyui" and store.path:
            model.install = os.path.dirname(store.path)  # the store's path is <install>/models
        return model
    return None


def _ollama(store: Store, item: Item) -> LocalModel:
    model = LocalModel(key_of(store, item), "ollama", item.names, store.path or "", bytes=item.bytes or 0)
    if store.source != "files" or not store.path:
        model.readable = False  # sized by the server: its folder can't be read (decision D-010)
        return model
    model.files = manifests_of(store.path, item.id)
    model.files += sorted(blob_rel(d) for d in item.details.get("digests", "").split(",") if d)
    model.replaceable = all(n.count("/") == 0 for n in item.names)  # Ollama's own library
    return model


def blob_rel(digest: str) -> str:
    return "blobs/" + digest.replace(":", "-", 1)


def manifests_of(models: str, item_id: str) -> list[str]:
    """Every manifest with this content (one per name the model has; the item id is its hash)."""
    found: list[str] = []
    for folder, _, names in os.walk(os.path.join(models, "manifests")):
        for name in names:
            path = os.path.join(folder, name)
            with contextlib.suppress(OSError), open(path, "rb") as handle:
                if hashlib.sha256(handle.read()).hexdigest()[:12] == item_id:
                    found.append(os.path.relpath(path, models).replace(os.sep, "/"))
    return sorted(found)


def _huggingface(store: Store, item: Item) -> LocalModel | None:
    if not item.path or not store.path or not os.path.isdir(item.path):
        return None
    model = LocalModel(key_of(store, item), "huggingface", item.names, store.path, replaceable=True)
    for folder, _, names in os.walk(item.path):
        for name in names:
            path = os.path.join(folder, name)
            rel = os.path.relpath(path, store.path).replace(os.sep, "/")
            if os.path.islink(path):
                model.links.append(
                    (rel, os.readlink(path).replace(os.sep, "/"))
                )  # "/" in the catalog, on every OS
            else:
                model.files.append(rel)
                model.bytes += os.path.getsize(path)
    model.files.sort()
    model.links.sort()
    return model


# -- leaving the computer -----------------------------------------------------------------------------------


def remove_local(model: LocalModel, ctx: Context) -> None:
    """Remove `model` from the computer. Ollama: through its server when it answers, so Ollama drops the
    blobs no other model lists; otherwise the manifests, then the blobs no other manifest lists."""
    if model.tool == "ollama":
        _remove_ollama(model, ctx)
        return
    for rel in model.files:
        with contextlib.suppress(FileNotFoundError):
            os.remove(model.path(rel))
    for rel, _ in model.links:
        with contextlib.suppress(FileNotFoundError):
            os.remove(model.path(rel))
    if model.tool == "huggingface" and (model.files or model.links):
        repo = (model.files or [model.links[0][0]])[0].split("/")[0]
        _remove_empty_tree(os.path.join(model.root, repo))


def _remove_ollama(model: LocalModel, ctx: Context) -> None:
    answered = [ctx.send("DELETE", f"{server(ctx.env)}/api/delete", {"model": n}) for n in model.names]
    gone: tuple[type[OSError], ...] = (FileNotFoundError,)
    if all(a is not None for a in answered):
        gone = (FileNotFoundError, PermissionError)  # Ollama removed them; its folder may be read-only to us
    manifests = [rel for rel in model.files if rel.startswith("manifests/")]
    for rel in manifests:  # the server may not be running: then hone-drive removes them itself
        with contextlib.suppress(*gone):
            os.remove(model.path(rel))
    still = listed_blobs(model.root)
    for rel in model.files:
        if rel.startswith("blobs/") and rel not in still:
            with contextlib.suppress(*gone):
                os.remove(model.path(rel))
    for rel in manifests:
        _prune_empty(os.path.dirname(model.path(rel)), stop=os.path.join(model.root, "manifests"))
    if any(os.path.exists(model.path(rel)) for rel in manifests):
        raise ShelfRefused(f"Ollama did not remove {model.names[0]}; it is on the drive and still here")


def listed_blobs(models: str) -> set[str]:
    """The blobs the manifests left on the computer list (design AC21: those stay)."""
    found: set[str] = set()
    for folder, _, names in os.walk(os.path.join(models, "manifests")):
        for name in names:
            with contextlib.suppress(OSError, ValueError):
                with open(os.path.join(folder, name), encoding="utf-8") as handle:
                    data = cast(dict[str, Any], json.load(handle))
                layers = [data.get("config") or {}, *cast(list[dict[str, Any]], data.get("layers") or [])]
                found |= {blob_rel(str(layer["digest"])) for layer in layers if layer.get("digest")}
    return found


def _prune_empty(path: str, stop: str) -> None:
    """Remove `path` and its parents while they are empty, up to (not including) `stop`."""
    while path != stop and path.startswith(stop):
        try:
            os.rmdir(path)
        except OSError:
            return
        path = os.path.dirname(path)


def _remove_empty_tree(top: str) -> None:
    """Remove the empty folders under `top`, and `top` when it ends up empty."""
    for folder, _, _ in os.walk(top, topdown=False):
        with contextlib.suppress(OSError):
            os.rmdir(folder)
