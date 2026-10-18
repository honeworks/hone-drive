"""Where AI work fills a disk (design §5.1): the store finders, a dict of plain functions by store id.

Every finder takes a `Context` and returns zero or more `Store`s. Paths it can't read are listed as
unreadable and never counted as empty (design §5.2)."""

from __future__ import annotations

import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from hone_drive.model import Item, Store
from hone_drive.sizing import Tally, size_of, walk
from hone_drive.system import Files, LocalFiles, Runner, SubprocessRunner

MODEL_FILE_MIN = 50 * 1000**2  # files from this size up are listed one by one as model items
STORE_TIME_LIMIT_S = 60.0


def http_get(url: str) -> bytes | None:
    """GET a local service (the Ollama server); None when nothing answers."""
    try:
        with urllib.request.urlopen(url, timeout=2.0) as response:  # noqa: S310 - local http only
            return bytes(response.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None


@dataclass
class Context:
    os_name: str
    home: str
    env: dict[str, str] = field(default_factory=lambda: dict(os.environ))
    run: Runner = field(default_factory=SubprocessRunner)
    files: Files = field(default_factory=LocalFiles)
    fetch: Callable[[str], bytes | None] = http_get
    extra: list[str] = field(default_factory=list[str])  # folders the user added (`--add`)
    time_limit: float = STORE_TIME_LIMIT_S

    def path(self, *parts: str) -> str:
        return str(Path(self.home, *parts))

    def local_app_data(self) -> str:
        return self.env.get("LOCALAPPDATA") or self.path("AppData", "Local")

    def deadline(self) -> float:
        return time.monotonic() + self.time_limit


Finder = Callable[[Context], list[Store]]


def iso(timestamp: float) -> str | None:
    return datetime.fromtimestamp(timestamp, UTC).isoformat(timespec="seconds") if timestamp else None


def blocked(path: str) -> bool:
    """True when `path` can't be seen because a folder on the way is unreadable (another user's files)."""
    current = path
    while not os.path.exists(current):  # False, not an error, for a path behind an unreadable folder
        parent = os.path.dirname(current)
        if parent == current:
            return False
        current = parent
    return current != path and not os.access(current, os.R_OK | os.X_OK)


def store_from_tally(store_id: str, title: str, path: str, tally: Tally, items: list[Item]) -> Store:
    return Store(
        id=store_id,
        title=title,
        path=path,
        bytes=tally.bytes,
        items=items,
        unreadable=tally.unreadable[:20],
        partial=tally.partial,
    )


def file_items(root: str, ctx: Context, prefix: str = "") -> tuple[Tally, list[Item]]:
    """A folder of model files: one item per large file, the rest summed as "other files"."""
    items: list[Item] = []

    def visit(path: str, info: os.stat_result) -> None:
        if info.st_size >= MODEL_FILE_MIN:
            rel = os.path.relpath(path, root)
            items.append(
                Item(
                    id=prefix + rel.replace(os.sep, "/"),
                    names=[os.path.basename(path)],
                    bytes=info.st_size,
                    files=1,
                    path=path,
                    accessed=iso(info.st_atime),
                )
            )

    tally = walk(os.path.realpath(root), deadline=ctx.deadline(), one_device=False, visit=visit)
    rest = tally.bytes - sum(i.bytes or 0 for i in items)
    if rest > 0:
        items.append(Item(id=prefix + "(other files)", names=["other files"], bytes=rest, path=root))
    return tally, sorted(items, key=lambda i: -(i.bytes or 0))


def find_folder_store(store_id: str, title: str, candidates: list[str]) -> Finder:
    """A finder for tools that keep model files in one folder (LM Studio, llama.cpp)."""

    def find(ctx: Context) -> list[Store]:
        found: list[Store] = []
        for path in candidates_for(ctx, candidates):
            if os.path.isdir(path):
                tally, items = file_items(path, ctx)
                found.append(store_from_tally(store_id, title, path, tally, items))
        return found

    return find


def candidates_for(ctx: Context, templates: list[str]) -> list[str]:
    """Expand `~` and `$VAR` in candidate paths; a template whose variable is unset is skipped."""
    out: list[str] = []
    for template in templates:
        expanded = template
        if template.startswith("$"):
            name, _, rest = template[1:].partition("/")
            if not ctx.env.get(name):
                continue
            expanded = str(Path(ctx.env[name], rest)) if rest else ctx.env[name]
        path = ctx.path(expanded[2:]) if expanded.startswith("~/") else expanded
        if path not in out:
            out.append(path)
    return out


def find_huggingface(ctx: Context) -> list[Store]:
    hub = candidates_for(ctx, ["$HF_HUB_CACHE", "$HF_HOME/hub", "~/.cache/huggingface/hub"])
    for path in hub:
        if not os.path.isdir(path):
            continue
        total = Tally()
        items: list[Item] = []
        for name in sorted(os.listdir(path)):
            if not name.startswith(("models--", "datasets--", "spaces--")):
                continue
            tally = size_of(os.path.join(path, name), ctx.deadline(), total.seen)
            kind, _, repo = name.partition("--")
            items.append(
                Item(
                    id=name,
                    names=[repo.replace("--", "/")],
                    bytes=tally.bytes,
                    files=tally.files,
                    path=os.path.join(path, name),
                    accessed=iso(tally.newest_access),
                    details={"kind": kind[:-1]},
                )
            )
            total.bytes += tally.bytes
            total.unreadable += tally.unreadable
            total.partial |= tally.partial
        items.sort(key=lambda i: -(i.bytes or 0))
        return [store_from_tally("huggingface", "Hugging Face cache", path, total, items)]
    return []


def find_custom(ctx: Context) -> list[Store]:
    stores: list[Store] = []
    for path in ctx.extra:
        if os.path.isdir(path):
            tally, items = file_items(path, ctx)
            stores.append(
                store_from_tally("custom", f"Folder {os.path.basename(path) or path}", path, tally, items)
            )
    return stores


def all_finders() -> dict[str, Finder]:
    from hone_drive.store_caches import find_caches  # noqa: PLC0415 - the finders live in their own modules
    from hone_drive.store_comfyui import find_comfyui  # noqa: PLC0415
    from hone_drive.store_docker import find_docker  # noqa: PLC0415
    from hone_drive.store_ollama import find_ollama  # noqa: PLC0415

    return {
        "ollama": find_ollama,
        "huggingface": find_huggingface,
        "comfyui": find_comfyui,
        "lmstudio": find_folder_store(
            "lmstudio", "LM Studio models", ["~/.lmstudio/models", "~/.cache/lm-studio/models"]
        ),
        "llamacpp": find_folder_store(
            "llamacpp",
            "llama.cpp cache",
            [
                "$LLAMA_CACHE",
                "~/.cache/llama.cpp",
                "~/Library/Caches/llama.cpp",
                "$LOCALAPPDATA/llama.cpp",
            ],
        ),
        "docker": find_docker,
        "caches": find_caches,
        "custom": find_custom,
    }
