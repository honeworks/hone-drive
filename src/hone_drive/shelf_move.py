"""Moving models to a drive (change 0003): copy, prove the copy, and only then remove them from the
computer. `plan()` says what would happen and changes nothing; `move()` does it. The public face is
`hone_drive.shelf`."""

from __future__ import annotations

import os
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from hone_drive import access, text
from hone_drive import shelf_catalog as catalog
from hone_drive.errors import NotFound, ShelfDamaged, ShelfRefused, Stopped
from hone_drive.model import Run, Volume
from hone_drive.presentation import volume_names
from hone_drive.shelf_busy import Busy, in_use, look
from hone_drive.shelf_catalog import Shelf, ShelfFile, ShelfLink, ShelfModel
from hone_drive.shelf_copy import copy_checked, mtime_ns, read_back_cache, sha256_of, unchanged
from hone_drive.shelf_models import LocalModel, local_models, remove_local
from hone_drive.stores import Context

GIB = 1024**3
MARGIN = 2 * GIB  # free space a move leaves on the drive, as the benchmark's safety rule 2
FAT32_LIMIT = 4 * GIB - 1


@dataclass(frozen=True)
class Tick:
    """Progress of the model being copied or checked, for the dashboard's bar."""

    action: str  # "move", "restore", "check"
    model: str
    done: int
    total: int


@dataclass
class Options:
    stop: threading.Event = field(default_factory=threading.Event)
    on_tick: Callable[[Tick], None] | None = None
    keep_local: bool = False  # copy and check, but leave the computer's copy


@dataclass
class Planned:
    key: str
    names: list[str]
    tool: str
    bytes: int
    copy_bytes: int = 0  # what still has to be copied (the rest is on the drive, unchanged)
    refused: str | None = None
    fix: list[str] = field(default_factory=list[str])  # commands that fix an access problem
    replaceable: bool = False  # can be downloaded again if the drive is lost


@dataclass
class Plan:
    action: Literal["move", "restore"]
    mount: str
    drive: str  # the drive's name
    models: list[Planned]

    @property
    def ready(self) -> list[Planned]:
        return [p for p in self.models if p.refused is None]


@dataclass
class Result:
    done: list[str] = field(default_factory=list[str])
    failed: dict[str, str] = field(default_factory=dict[str, str])
    stopped: bool = False


def context(run: Run) -> Context:
    return Context(os_name=run.machine.os, home=os.path.expanduser("~"))


def target(run: Run, to: str | None) -> Volume:
    """The drive to move to: a mount point or a drive name; by default the emptiest external drive."""
    from hone_drive.advice_stores import external_targets  # noqa: PLC0415 - advice imports presentation

    names = volume_names(run)
    candidates = external_targets(run)
    if to is None:
        if not candidates:
            raise NotFound("no external drive is connected: plug one in and scan again")
        return candidates[0]
    found = next((v for v in run.volumes if to in (v.mount, names.get(v.mount))), None)
    if found is None:
        raise NotFound(f"no drive {to!r} in this scan; `hone-drive scan` lists them")
    return found


def plan(
    run: Run,
    keys: list[str],
    to: str | None = None,
    ctx: Context | None = None,
    busy: Callable[[], Busy] | None = None,
) -> Plan:
    """What `move()` would do with `keys` (see `shelf_models.key_of`); nothing changes."""
    ctx = ctx or context(run)
    volume = target(run, to)
    shelf = catalog.load(volume.mount)
    local = local_models(run)
    opened = (busy or look)()
    free = shutil.disk_usage(volume.mount).free
    drive_access = access.check([str(catalog.folder(volume.mount))], [], run.machine.os)
    result = Plan("move", volume.mount, volume_names(run)[volume.mount], [])
    for key in keys:
        model = local.get(key)
        if model is None:
            result.models.append(Planned(key, [key], key.split(":")[0], 0, refused="not on this computer"))
            continue
        item = Planned(key, model.names, model.tool, model.bytes, replaceable=model.replaceable)
        item.copy_bytes = _to_copy(model, shelf, volume.mount)
        item.refused, item.fix = _refusal(model, volume, ctx, opened, drive_access)
        if item.refused is None and int(item.copy_bytes * 1.1) + MARGIN > free:
            item.refused = (
                f"not enough room on the drive: it needs {text.size(item.copy_bytes)} free plus 2 GB"
            )
        if item.refused is None:
            free -= item.copy_bytes
        result.models.append(item)
    return result


def _refusal(
    model: LocalModel, volume: Volume, ctx: Context, opened: Busy, drive: access.Access
) -> tuple[str | None, list[str]]:
    if not drive.ok:
        return f"hone-drive can't write on this drive: {drive.problems[0]}", drive.fix
    if not model.readable:
        found = access.check([model.root], [], ctx.os_name)
        return f"hone-drive can't read {model.tool}'s folder yet", found.fix
    if (reason := in_use(model, ctx, opened)) is not None:
        return reason, []
    found = access.check(model.folders(), [model.path(r) for r in model.files], ctx.os_name)
    if not found.ok:
        return found.problems[0], found.fix
    traits = volume.traits
    large = any(os.path.getsize(model.path(r)) > FAT32_LIMIT for r in model.files)
    if traits is not None and not traits.large_files and large:
        return "the drive's filesystem (FAT32) can't hold files over 4 GB", []
    return None, []


def _to_copy(model: LocalModel, shelf: Shelf | None, mount: str) -> int:
    known = index(shelf, model.tool, model.root)
    total = 0
    for rel in model.files:
        entry = known.get(rel)
        if entry is None or not _same(model, entry, mount, rel):
            total += os.path.getsize(model.path(rel))
    return total


def _same(model: LocalModel, entry: ShelfFile, mount: str, rel: str) -> bool:
    """The catalog's word that both copies are unchanged since they were checked ("Trust catalog")."""
    probe = ShelfModel(key="", tool=model.tool, names=[], root=model.root)
    return unchanged(catalog.drive_file(mount, probe, rel), entry, "drive") and unchanged(
        model.path(rel), entry, "local"
    )


def index(shelf: Shelf | None, tool: str, root: str) -> dict[str, ShelfFile]:
    """Every checked file on the drive from one tool's folder (an Ollama blob two models list is one)."""
    found: dict[str, ShelfFile] = {}
    entries: list[ShelfModel] = list(shelf.models.values()) if shelf else []
    for entry in entries:
        if entry.tool == tool and entry.root == root:
            found.update({f.rel: f for f in entry.files})
    return found


# -- moving -------------------------------------------------------------------------------------------------


def move(run: Run, the_plan: Plan, options: Options | None = None, ctx: Context | None = None) -> Result:
    """Copy each planned model to the drive, check it, record it, and then remove it from the computer."""
    options, ctx = options or Options(), ctx or context(run)
    local = local_models(run)
    result = Result()
    with catalog.locked(the_plan.mount):
        shelf = catalog.open_shelf(the_plan.mount, the_plan.drive)
        for item in the_plan.ready:
            try:
                _move_one(shelf, local[item.key], options, ctx)
            except Stopped:
                result.stopped = True
                break
            except (ShelfDamaged, ShelfRefused, OSError) as exc:
                result.failed[item.key] = str(exc)
            else:
                result.done.append(item.key)
    return result


def _move_one(shelf: Shelf, model: LocalModel, options: Options, ctx: Context) -> None:
    entry = shelf.models.get(model.key) or ShelfModel(
        key=model.key, tool=model.tool, names=model.names, root=model.root, replaceable=model.replaceable
    )
    entry.names, entry.complete = model.names, False
    shelf.models[model.key] = entry
    catalog.save(shelf)
    known = index(shelf, model.tool, model.root)
    done = 0
    files: list[ShelfFile] = []
    for rel in model.files:
        size = os.path.getsize(model.path(rel))

        def tick(n: int, base: int = done) -> None:
            if options.on_tick:
                options.on_tick(Tick("move", model.names[0], base + n, max(model.bytes, 1)))

        files.append(_place(model, entry, rel, known.get(rel), shelf.mount, options, tick))
        done += size
        entry.files = files
        catalog.save(shelf)  # each checked file is recorded at once: a crash keeps what is proven
    entry.links = [ShelfLink(rel=rel, target=target) for rel, target in model.links]
    entry.complete, entry.copied = True, datetime.now(UTC).isoformat(timespec="seconds")
    catalog.save(shelf)
    if not options.keep_local:
        remove_local(model, ctx)


def _place(
    model: LocalModel,
    entry: ShelfModel,
    rel: str,
    known: ShelfFile | None,
    mount: str,
    options: Options,
    tick: Callable[[int], None],
) -> ShelfFile:
    """One file on the drive: trusted from the catalog, found equal by hashing both, or copied and checked."""
    source, dest = model.path(rel), catalog.drive_file(mount, entry, rel)
    if known is not None and unchanged(dest, known, "drive") and unchanged(source, known, "local"):
        return known
    size = os.path.getsize(source)
    if os.path.exists(dest) and os.path.getsize(dest) == size:
        mine = sha256_of(source, direct=False, stop=options.stop)
        if sha256_of(dest, direct=True, stop=options.stop, progress=tick) == mine:
            return ShelfFile(
                rel=rel, bytes=size, sha256=mine, drive_ns=mtime_ns(dest), local_ns=mtime_ns(source),
                cache=read_back_cache(dest),
            )  # fmt: skip
    digest, cache = copy_checked(source, dest, options.stop, tick)
    return ShelfFile(
        rel=rel, bytes=size, sha256=digest, drive_ns=mtime_ns(dest), local_ns=mtime_ns(source), cache=cache
    )
