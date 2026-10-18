"""Docker (design §5.1), sized by Docker itself: its data root is usually readable only by root, and on
macOS and Windows it lives inside a virtual machine's disk image. With the containerd image store, the
images are under containerd's root, not Docker's (history §1)."""

from __future__ import annotations

import json
import re
from typing import Any, cast

from hone_drive.model import Item, Store
from hone_drive.stores import Context

UNITS = {"B": 1, "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4, "PB": 1000**5}
DATA_ROOTS = {
    "macos": "~/Library/Containers/com.docker.docker/Data/vms/0/data",
    "windows": "$LOCALAPPDATA/Docker/wsl",
}
CLEAN = {
    "Images": "docker image prune -a",
    "Containers": "docker container prune",
    "Local Volumes": "docker volume prune",
    "Build Cache": "docker builder prune",
}


def parse_size(text: str) -> int | None:
    """Docker's sizes ("11.38GB", "512kB", "0B"), decimal units, as bytes."""
    match = re.match(r"\s*([\d.]+)\s*([kKMGTP]?B)", text or "")
    return int(float(match.group(1)) * UNITS[match.group(2).upper()]) if match else None


def find_docker(ctx: Context) -> list[Store]:
    out = ctx.run(["docker", "system", "df", "--format", "json"], 20.0)
    if not out.ok:
        return []  # Docker is not installed or not running: nothing to report
    items: list[Item] = []
    reclaimable = 0
    for line in out.stdout.splitlines():
        try:
            row = cast(dict[str, Any], json.loads(line))
        except ValueError:
            continue
        kind = str(row.get("Type", "?"))
        size = parse_size(str(row.get("Size", "")))
        free = parse_size(str(row.get("Reclaimable", ""))) or 0
        reclaimable += free
        items.append(
            Item(
                id=kind.lower().replace(" ", "-"),
                names=[kind],
                bytes=size,
                details={
                    "count": str(row.get("TotalCount", "")),
                    "active": str(row.get("Active", "")),
                    "reclaimable_bytes": str(free),
                    "clean": CLEAN.get(kind, ""),
                },
            )
        )
    store = Store(
        id="docker",
        title="Docker",
        bytes=sum(i.bytes or 0 for i in items),
        reclaimable_bytes=reclaimable,
        items=items,
        source="docker",
        clean={"any": "docker system prune"},
    )
    store.path = _data_root(ctx)
    if store.path is None:
        store.unknown["path"] = "Docker did not say where it keeps its data"
    return [store]


def _data_root(ctx: Context) -> str | None:
    if ctx.os_name in DATA_ROOTS:
        template = DATA_ROOTS[ctx.os_name]
        return template.replace("~", ctx.home, 1).replace("$LOCALAPPDATA", ctx.local_app_data())
    info = ctx.run(["docker", "info", "--format", "{{.DockerRootDir}} {{json .DriverStatus}}"], 20.0)
    if not info.ok or not info.stdout.strip():
        return None
    root, _, status = info.stdout.strip().partition(" ")
    if "io.containerd.snapshotter" in status:
        return "/var/lib/containerd"  # the containerd image store keeps image layers there
    return root
