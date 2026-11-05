"""Bringing models back from a drive, removing them from a drive, checking a drive, and what is on every
drive (change 0003). Bringing back only copies: it never removes anything from the drive."""

from __future__ import annotations

import contextlib
import os
import shutil
from dataclasses import dataclass

from hone_drive import access, text
from hone_drive import shelf_catalog as catalog
from hone_drive.errors import NotFound, ShelfDamaged, Stopped
from hone_drive.model import Run
from hone_drive.ollama_server import answers, bring_back, ollama_here
from hone_drive.shelf_catalog import Shelf, ShelfFile, ShelfModel
from hone_drive.shelf_copy import copy_checked, mtime_ns, read_back_cache, sha256_of, unchanged
from hone_drive.shelf_move import MARGIN, Options, Plan, Planned, Result, Tick, context, refusals
from hone_drive.stores import Context
from hone_drive.volumes import mount_of


@dataclass
class OnDrive:
    """A drive's shelf and where it is mounted now (None: not connected)."""

    shelf: Shelf
    mount: str | None


def contents(run: Run) -> list[OnDrive]:
    """Every shelf: the connected drives' own catalogs, and the copies of the others (owner's answer 5)."""
    found: dict[str, OnDrive] = {}
    for volume in run.volumes:
        shelf = catalog.load(volume.mount)
        if shelf is not None:
            shelf.mount = volume.mount
            found[shelf.drive] = OnDrive(shelf, volume.mount)
    for shelf in catalog.known():
        found.setdefault(shelf.drive, OnDrive(shelf, None))
    return sorted(found.values(), key=lambda d: d.shelf.name)


def find(run: Run, key: str, drive: str | None = None) -> OnDrive:
    """The drive holding `key` (a connected one first); `drive` narrows it by name or mount point."""
    holding = [d for d in contents(run) if key in d.shelf.models]
    if drive is not None:
        holding = [d for d in holding if drive in (d.shelf.name, d.mount, d.shelf.drive)]
    if not holding:
        raise NotFound(f"{key} is on no drive hone-drive knows; `hone-drive shelf` lists them")
    return sorted(holding, key=lambda d: d.mount is None)[0]


# -- bringing back -----------------------------------------------------------------------------------------


def plan_restore(
    run: Run, keys: list[str], drive: str | None = None, ctx: Context | None = None
) -> list[Plan]:
    """What `restore()` would do, one plan per drive; nothing changes."""
    ctx = ctx or context(run)
    plans: dict[str, Plan] = {}
    for key in keys:
        found = find(run, key, drive)
        entry = found.shelf.models[key]
        mount = found.mount or ""
        the_plan = plans.setdefault(found.shelf.drive, Plan("restore", mount, found.shelf.name, []))
        item = Planned(key, entry.names, entry.tool, sum(f.bytes for f in entry.files))
        item.refused, item.fix = _restore_refusal(run, found, entry, ctx)
        if item.refused is None:
            item.copy_bytes = _to_bring(entry, found.mount or "")
            local = mount_of(entry.root, [v.mount for v in run.volumes]) or entry.root
            if int(item.copy_bytes * 1.1) + MARGIN > _free(local):
                item.refused = f"not enough room on the computer: it needs {text.size(item.copy_bytes)}"
        the_plan.models.append(item)
    return list(plans.values())


def _restore_refusal(
    run: Run, found: OnDrive, entry: ShelfModel, ctx: Context
) -> tuple[str | None, list[str]]:
    if found.mount is None:
        return f"plug in {found.shelf.name} to bring it back", []
    if not entry.complete:
        return f"the copy on {found.shelf.name} is not complete; move it to the drive again first", []
    if via_server(entry, run.machine.os):
        if not answers(ctx):
            return "start Ollama: its folder isn't writable, so Ollama takes the model back itself", []
    else:
        checked = access.check(_folders(entry), [], run.machine.os)
        if not checked.ok:
            return checked.problems[0], checked.fix
    for f in entry.files if entry.tool != "ollama" else []:  # Ollama's blobs are named by their content
        path = _local(entry, f.rel)
        if os.path.exists(path) and not _on_computer(entry, f):
            return f"a different file is already at {path}; move or rename it first", []
    return None, []


def _to_bring(entry: ShelfModel, mount: str) -> int:
    if entry.tool == "ollama" and ollama_here(entry, mount):
        return 0
    return sum(f.bytes for f in entry.files if not _on_computer(entry, f))


def on_computer(entry: ShelfModel, mount: str) -> bool:
    """The model is on the computer as it is on the drive (an Ollama model: by the names Ollama has and the
    config and layers they list, whichever folder it keeps them in, D-025)."""
    if entry.tool == "ollama":
        return ollama_here(entry, mount)
    return all(_on_computer(entry, f) for f in entry.files)


def restore(
    run: Run, plans: list[Plan], options: Options | None = None, ctx: Context | None = None
) -> Result:
    """Copy each planned model back to where it was, checked against the catalog's SHA-256."""
    options, ctx = options or Options(), ctx or context(run)
    result = Result(failed=refusals(plans))
    for the_plan in plans:
        if not the_plan.ready:
            continue
        with catalog.locked(the_plan.mount):
            shelf = catalog.load(the_plan.mount)
            if shelf is None:
                raise NotFound(f"{the_plan.drive} has no shelf at {the_plan.mount}")
            for item in the_plan.ready:
                try:
                    entry = shelf.models[item.key]
                    if via_server(entry, run.machine.os):
                        _through_ollama(shelf, entry, ctx)
                    else:
                        _restore_one(shelf, entry, options)
                except Stopped:
                    result.stopped = True
                    return result
                except (ShelfDamaged, OSError) as exc:
                    result.failed[item.key] = str(exc)
                else:
                    result.done.append(item.key)
                finally:
                    catalog.save(shelf)
    return result


def _restore_one(shelf: Shelf, entry: ShelfModel, options: Options) -> None:
    # Ollama: blobs before manifests, so Ollama never lists a model whose blobs are missing
    ordered = sorted(entry.files, key=lambda f: f.rel.startswith("manifests/"))
    total, done = max(sum(f.bytes for f in entry.files), 1), 0
    for f in ordered:
        path = _local(entry, f.rel)

        def tick(n: int, base: int = done) -> None:
            if options.on_tick:
                options.on_tick(Tick("restore", entry.names[0], base + n, total))

        if not _on_computer(entry, f):
            copy_checked(catalog.drive_file(shelf.mount, entry, f.rel), path, options.stop, tick, f.sha256)
        f.local_ns = mtime_ns(path)
        done += f.bytes
    for link in entry.links:
        path = _local(entry, link.rel)
        if not os.path.lexists(path):
            _link(path, link.target)


VIA_SERVER: str = "auto"  # "always": the tests' way to take the server's path on a folder they could write


def via_server(entry: ShelfModel, os_name: str) -> bool:
    """Ollama takes the model back itself when this user can read its folder but not write it."""
    if entry.tool != "ollama":
        return False
    return VIA_SERVER == "always" or not access.check(_folders(entry), [], os_name).ok


def _folders(entry: ShelfModel) -> list[str]:
    return sorted({os.path.dirname(_local(entry, f.rel)) for f in entry.files} | {entry.root})


def _through_ollama(shelf: Shelf, entry: ShelfModel, ctx: Context) -> None:
    """Ollama pulls the drive's copy from hone-drive and checks every blob (decision D-024)."""
    bring_back(entry, shelf.mount, ctx)
    for f in entry.files:
        path = _local(entry, f.rel)
        f.local_ns = mtime_ns(path) if os.path.exists(path) else None


def _link(path: str, target: str) -> None:
    """A Hugging Face snapshot link; a copy where the OS refuses links (Windows without the right)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    target = target.replace("/", os.sep)  # the catalog says "/"; a Windows link needs "\\"
    try:
        os.symlink(target, path)
    except OSError:
        shutil.copyfile(os.path.join(os.path.dirname(path), target), path)


def _on_computer(entry: ShelfModel, f: ShelfFile) -> bool:
    path = _local(entry, f.rel)
    if unchanged(path, f, "local"):
        return True
    if not os.path.isfile(path) or os.path.getsize(path) != f.bytes:
        return False
    return sha256_of(path, direct=False, stop=Options().stop) == f.sha256


def _local(entry: ShelfModel, rel: str) -> str:
    return os.path.join(entry.root, *rel.split("/"))


def _free(path: str) -> int:
    while not os.path.exists(path):
        path = os.path.dirname(path)
    return shutil.disk_usage(path).free


# -- removing from a drive, checking a drive ---------------------------------------------------------------


def remove(run: Run, keys: list[str], drive: str | None = None, only_copy: bool = False) -> Result:
    """Remove models from a drive; never touches the computer's copy. Refused for a model whose only copy
    is on the drive unless `only_copy` confirms it will be gone (guarantee 26)."""
    result = Result()
    for key in keys:
        found = find(run, key, drive)
        if found.mount is None:
            result.failed[key] = f"plug in {found.shelf.name} to remove it"
            continue
        entry = found.shelf.models[key]
        if not only_copy and not on_computer(entry, found.mount):
            name = entry.names[0]
            result.failed[key] = (
                f"{name} is only on {found.shelf.name}: removing it loses it. Bring it back first, "
                "or confirm that it will be gone"
            )
            continue
        with catalog.locked(found.mount):
            _remove_one(catalog.load(found.mount) or found.shelf, key)
        result.done.append(key)
    return result


def _remove_one(shelf: Shelf, key: str) -> None:
    entry = shelf.models.pop(key)
    kept = {
        catalog.drive_file(shelf.mount, other, f.rel) for other in shelf.models.values() for f in other.files
    }
    catalog.save(shelf)  # out of the catalog first: a crash leaves files no entry lists, never the reverse
    for f in entry.files:
        path = catalog.drive_file(shelf.mount, entry, f.rel)
        if path not in kept:
            with contextlib.suppress(FileNotFoundError):
                os.remove(path)


def verify(run: Run, drive: str | None = None, options: Options | None = None) -> dict[str, str]:
    """ "Check drive": hash every file on connected shelves again; what is damaged or missing, by model."""
    options = options or Options()
    damaged: dict[str, str] = {}
    for found in contents(run):
        if found.mount is None or (drive is not None and drive not in (found.shelf.name, found.mount)):
            continue
        with catalog.locked(found.mount):
            for entry in found.shelf.models.values():
                for f in entry.files:
                    path = catalog.drive_file(found.mount, entry, f.rel)
                    if not os.path.isfile(path) or sha256_of(path, True, options.stop) != f.sha256:
                        damaged[entry.key] = f"{f.rel} is missing or damaged on {found.shelf.name}"
                        entry.complete = False
                    else:
                        f.drive_ns, f.cache = mtime_ns(path), read_back_cache(path)
            catalog.save(found.shelf)
    return damaged
