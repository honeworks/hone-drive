"""Is a model in use? (change 0003; the owner's answer 3: refuse, with the reason). One look at this user's
programs finds the files they have open or mapped into memory (llama.cpp and LM Studio map GGUF files and
may close them) and the ComfyUI installs that run, whatever port they listen on; Ollama says itself which
models it has loaded; on Windows a file another program holds can't be opened exclusively."""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, cast

from hone_drive.access import held_elsewhere
from hone_drive.shelf_models import LocalModel
from hone_drive.store_ollama import server
from hone_drive.stores import Context

COMFYUI_PORT = "http://127.0.0.1:8188"
APP = "comfyui"  # the ComfyUI desktop app's process name contains it


@dataclass
class Busy:
    files: set[str] = field(default_factory=set[str])  # real paths open or mapped by this user's programs
    comfyui: set[str] = field(default_factory=set[str])  # real paths of running installs' main.py
    comfyui_app: bool = False  # the desktop app runs (it keeps its models where its settings say)


def look() -> Busy:
    """One pass over this user's programs (half a second on the reference machine)."""
    import psutil  # noqa: PLC0415

    found = Busy()
    me = psutil.Process().username()
    for proc in psutil.process_iter(["username", "name", "cmdline", "cwd"]):
        info: dict[str, Any] = proc.info
        if info.get("username") != me or proc.pid == os.getpid():
            continue
        if APP in str(info.get("name") or "").lower():
            found.comfyui_app = True
        found.comfyui |= _main_py(info)
        with contextlib.suppress(psutil.Error, OSError):
            found.files |= {os.path.realpath(f.path) for f in proc.open_files()}
        maps: Any = getattr(proc, "memory_maps", None)  # not on every OS psutil supports
        with contextlib.suppress(psutil.Error, OSError, NotImplementedError, TypeError):
            found.files |= {os.path.realpath(str(m.path)) for m in maps(grouped=True) if m.path}
    return found


def _main_py(info: dict[str, Any]) -> set[str]:
    """The `main.py` a Python process runs (`python main.py --port 8190`), as a real path."""
    raw = cast(list[object], info.get("cmdline") or [])
    args = [str(a) for a in raw]
    cwd = str(info.get("cwd") or "")
    return {os.path.realpath(os.path.join(cwd, a)) for a in args if os.path.basename(a) == "main.py"}


def in_use(model: LocalModel, ctx: Context, busy: Busy) -> str | None:
    """Why `model` can't be moved now, or None."""
    if model.tool == "ollama" and set(model.names) & _ollama_loaded(ctx):
        name = model.names[0]
        return f"Ollama has {name} loaded; unload it (ollama stop {name}) and try again"
    if model.tool == "comfyui" and _comfyui_runs(model, ctx, busy):
        return "ComfyUI is running; close it and try again"
    for rel in model.files:
        path = model.path(rel)
        if os.path.realpath(path) in busy.files or held_elsewhere(path):
            return f"a program has {rel} open; close it and try again"
    return None


def _comfyui_runs(model: LocalModel, ctx: Context, busy: Busy) -> bool:
    main = os.path.realpath(os.path.join(model.install, "main.py")) if model.install else None
    return (
        (main is not None and main in busy.comfyui)
        or busy.comfyui_app
        or ctx.fetch(f"{COMFYUI_PORT}/system_stats") is not None
    )


def _ollama_loaded(ctx: Context) -> set[str]:
    raw = ctx.fetch(f"{server(ctx.env)}/api/ps")
    try:
        models = cast(list[dict[str, Any]], json.loads(raw or b"{}").get("models") or [])
    except (ValueError, AttributeError):
        return set()
    return {str(m.get("name", "")) for m in models} | {str(m.get("model", "")) for m in models}
