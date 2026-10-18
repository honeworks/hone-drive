"""ComfyUI's models (design §5.1): `ComfyUI/models` folders under the home folder, two levels deep, and
the folders an `extra_model_paths.yaml` adds. Items are model files, named by their subfolder."""

from __future__ import annotations

import os
import re

from hone_drive.model import Item, Store
from hone_drive.sizing import Tally
from hone_drive.stores import Context, file_items

SKIP_KEYS = {"base_path", "is_default", "custom_nodes", "datasets"}


def find_comfyui(ctx: Context) -> list[Store]:
    stores: list[Store] = []
    seen: set[str] = set()
    for install in _installs(ctx):
        folders = [os.path.join(install, "models")]
        yaml = ctx.files.read(os.path.join(install, "extra_model_paths.yaml"))
        folders += extra_model_paths(yaml or "", install)
        total = Tally()
        items: list[Item] = []
        for folder in folders:
            real = os.path.realpath(folder)
            if real in seen or not os.path.isdir(real):
                continue
            seen.add(real)
            tally, found = file_items(folder, ctx, prefix=_prefix(folder, install))
            total.bytes += tally.bytes
            total.unreadable += tally.unreadable
            total.partial |= tally.partial
            items += found
        if items or total.bytes:
            stores.append(
                Store(
                    id="comfyui",
                    title=f"ComfyUI models ({os.path.basename(install)})",
                    path=os.path.join(install, "models"),
                    bytes=total.bytes,
                    items=sorted(items, key=lambda i: -(i.bytes or 0)),
                    unreadable=total.unreadable[:20],
                    partial=total.partial,
                )
            )
    return stores


def _installs(ctx: Context) -> list[str]:
    """Folders with a `models/` folder and ComfyUI's `main.py`, at most two levels under the home folder."""
    found: list[str] = []
    level = [ctx.home]
    for _ in range(3):
        next_level: list[str] = []
        for folder in level:
            try:
                names = sorted(n for n in os.listdir(folder) if not n.startswith("."))
            except OSError:
                continue
            for name in names:
                path = os.path.join(folder, name)
                if not os.path.isdir(path) or os.path.islink(path):
                    continue
                if (
                    os.path.isfile(os.path.join(path, "main.py"))
                    and os.path.isdir(os.path.join(path, "models"))
                    and "comfy" in name.lower()
                ):
                    found.append(path)
                else:
                    next_level.append(path)
        level = next_level
    return found


def _prefix(folder: str, install: str) -> str:
    rel = os.path.relpath(folder, install)
    return "" if rel == "models" else rel.replace(os.sep, "/") + "/"


def extra_model_paths(text: str, install: str) -> list[str]:
    """The folders an `extra_model_paths.yaml` names, resolved against each section's `base_path`.

    Reads the subset ComfyUI documents: sections of `key: value` lines and `key: |` blocks of paths."""
    folders: list[str] = []
    base = install
    block_indent = -1  # indent of the key whose `|` block is being read; -1 outside a block
    for raw in text.splitlines():
        line = raw.split(" #", 1)[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if block_indent >= 0 and indent > block_indent:
            folders.append(os.path.join(base, os.path.expanduser(line.strip())))
            continue
        block_indent = -1
        if indent == 0:
            base = install  # a new section
            continue
        match = re.match(r"\s*([A-Za-z0-9_]+):\s*(.*)$", line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip().strip("'\"")
        if value == "|":
            block_indent = indent
        elif key == "base_path":
            base = os.path.join(install, os.path.expanduser(value))
        elif key not in SKIP_KEYS and value:
            folders.append(os.path.join(base, os.path.expanduser(value)))
    return folders
