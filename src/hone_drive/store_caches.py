"""Package and tool caches (design §5.1): one item per cache found, with the command that cleans it.

`same_volume` marks caches that link files into projects, so moving them to another drive makes them copy
instead (uv, pnpm): advice A07 says "clean, don't move" for those (history §6)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from hone_drive.model import Item, Store
from hone_drive.sizing import Tally, size_of
from hone_drive.stores import Context, candidates_for, iso


@dataclass(frozen=True)
class Cache:
    id: str
    title: str
    paths: dict[str, list[str]]  # os -> candidate paths ("any" for every OS)
    clean: str
    same_volume: bool = False


CACHES = [
    Cache(
        "uv",
        "uv packages",
        {
            "any": ["$UV_CACHE_DIR"],
            "linux": ["~/.cache/uv"],
            "macos": ["~/.cache/uv"],
            "windows": ["$LOCALAPPDATA/uv/cache"],
        },
        "uv cache clean",
        same_volume=True,
    ),
    Cache(
        "pip",
        "pip packages",
        {
            "linux": ["~/.cache/pip"],
            "macos": ["~/Library/Caches/pip"],
            "windows": ["$LOCALAPPDATA/pip/Cache"],
        },
        "pip cache purge",
    ),
    Cache(
        "npm",
        "npm packages",
        {"linux": ["~/.npm"], "macos": ["~/.npm"], "windows": ["$LOCALAPPDATA/npm-cache"]},
        "npm cache clean --force",
    ),
    Cache(
        "pnpm",
        "pnpm store",
        {
            "linux": ["~/.local/share/pnpm/store"],
            "macos": ["~/Library/pnpm/store"],
            "windows": ["$LOCALAPPDATA/pnpm/store"],
        },
        "pnpm store prune",
        same_volume=True,
    ),
    Cache(
        "conda",
        "conda packages",
        {"any": ["~/miniconda3/pkgs", "~/anaconda3/pkgs", "~/miniforge3/pkgs"]},
        "conda clean --all",
    ),
    Cache(
        "hf-xet",
        "Hugging Face transfer cache",
        {"any": ["$HF_HOME/xet", "~/.cache/huggingface/xet"]},
        "rm -rf ~/.cache/huggingface/xet",
    ),
    Cache("torch", "PyTorch hub", {"any": ["$TORCH_HOME", "~/.cache/torch"]}, "rm -rf ~/.cache/torch/hub"),
    Cache("whisper", "Whisper models", {"any": ["~/.cache/whisper"]}, "rm -rf ~/.cache/whisper"),
    Cache(
        "playwright",
        "Playwright browsers",
        {
            "linux": ["~/.cache/ms-playwright"],
            "macos": ["~/Library/Caches/ms-playwright"],
            "windows": ["$LOCALAPPDATA/ms-playwright"],
        },
        "npx playwright uninstall --all",
    ),
    Cache("pacman", "pacman packages", {"linux": ["/var/cache/pacman/pkg"]}, "sudo paccache -rk1"),
    Cache("apt", "apt packages", {"linux": ["/var/cache/apt/archives"]}, "sudo apt clean"),
    Cache("dnf", "dnf packages", {"linux": ["/var/cache/dnf"]}, "sudo dnf clean all"),
    Cache(
        "homebrew", "Homebrew downloads", {"macos": ["~/Library/Caches/Homebrew"]}, "brew cleanup --prune=all"
    ),
]
MIN_BYTES = 10 * 1000**2  # caches under 10 MB are not worth a line


def find_caches(ctx: Context) -> list[Store]:
    total = Tally()
    items: list[Item] = []
    for cache in CACHES:
        templates = cache.paths.get("any", []) + cache.paths.get(ctx.os_name, [])
        for path in candidates_for(ctx, templates):
            if not os.path.isdir(path):
                continue
            tally = size_of(path, ctx.deadline(), total.seen)
            total.bytes += tally.bytes
            total.unreadable += tally.unreadable
            total.partial |= tally.partial
            if tally.bytes >= MIN_BYTES:
                items.append(
                    Item(
                        id=cache.id,
                        names=[cache.title],
                        bytes=tally.bytes,
                        files=tally.files,
                        path=path,
                        accessed=iso(tally.newest_access),
                        details={"clean": cache.clean, "same_volume": str(cache.same_volume).lower()},
                    )
                )
            break  # the first existing candidate is the cache in use
    if not items:
        return []
    items.sort(key=lambda i: -(i.bytes or 0))
    return [
        Store(
            id="caches",
            title="Package and tool caches",
            bytes=total.bytes,
            reclaimable_bytes=sum(i.bytes or 0 for i in items),
            items=items,
            unreadable=total.unreadable[:20],
            partial=total.partial,
        )
    ]
