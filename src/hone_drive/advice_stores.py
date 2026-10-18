"""The advice rules about stores, filesystems and caches (design §8.1: A02 and A05 to A09)."""

from __future__ import annotations

from datetime import datetime, timedelta

from hone_drive import commands, text
from hone_drive.estimates import copy_time, load_time
from hone_drive.filesystems import display_name, native_filesystem
from hone_drive.model import Advice, Gain, Run, Store, Volume
from hone_drive.presentation import volume_names

MOVE_MIN = 20e9  # stores from 20 GB up are worth moving (A02)
CACHE_MIN = 1e9
DOCKER_MIN = 2e9
RECENT = timedelta(days=14)
SERVICE_STORES = ("ollama", "docker")
NOT_MODELS = ("docker", "caches")  # stores whose items are not loaded like models


def external_targets(run: Run) -> list[Volume]:
    """External, writable volumes, the emptiest first."""
    found = [
        v for v in run.volumes if not v.read_only and (d := run.drive(v.drive_id)) is not None and d.external
    ]
    return sorted(found, key=lambda v: -v.free_bytes)


def planned_moves(run: Run) -> list[tuple[Store, Volume, Volume]]:
    """(store, from, to) for each store A02 would move."""
    targets = external_targets(run)
    if not targets:
        return []
    target = targets[0]
    moves: list[tuple[Store, Volume, Volume]] = []
    room = target.free_bytes
    for store in sorted(run.stores, key=lambda s: -(s.bytes or 0)):
        source = run.volume(store.volume or "")
        if (
            store.id == "caches"
            or not store.bytes
            or store.bytes < MOVE_MIN
            or source is None
            or not store.path
        ):
            continue
        if source.mount == target.mount or source.used_fraction <= target.used_fraction:
            continue
        if store.bytes * 1.1 > room:
            continue
        room -= store.bytes
        moves.append((store, source, target))
    return moves


def a02_move_stores(run: Run, home: str) -> list[Advice]:
    found: list[Advice] = []
    names = volume_names(run)
    for store, source, target in planned_moves(run):
        why = [
            f"Frees {text.size(store.bytes)} on {names[source.mount]}; {names[target.mount]} has "
            f"{text.size(target.free_bytes)} free."
        ]
        cites = [f"volume:{source.mount}", f"volume:{target.mount}"]
        models = store.items if store.id not in NOT_MODELS else []
        for item in sorted(models, key=lambda i: -(i.bytes or 0))[:3]:
            now, then = load_time(item, source.mount, run), load_time(item, target.mount, run)
            if now and then:
                why.append(
                    f"{item.names[0]} ({text.size(item.bytes)}) would take about "
                    f"{text.seconds(then.seconds)} to load instead of {text.seconds(now.seconds)}."
                )
                cites += now.cites + then.cites
        copying = copy_time(store, source.mount, target.mount, run)
        if copying:
            why.append(f"Copying takes about {text.seconds(copying.seconds)}.")
        if models:
            why.append(
                "Only loading gets slower: once a model is loaded it runs from GPU memory and RAM "
                "at the same speed."
            )
        found.append(
            Advice(
                rule="A02",
                severity="suggest",
                title=f"Move {store.title} to {names[target.mount]}",
                why=why,
                cites=sorted(set(cites)),
                gain=Gain(bytes_freed=store.bytes),
                commands={run.machine.os: commands.move_store(store, target, run.machine.os, home)},
                risks=[
                    "Copy first and check the tool sees its models before deleting the old copy.",
                    f"The tool can't find its models while {names[target.mount]} is unplugged.",
                ],
            )
        )
    return found


def a05_filesystem(run: Run, home: str) -> list[Advice]:
    found: list[Advice] = []
    os_name = run.machine.os
    moving: dict[str, list[Store]] = {}
    for store, _, target in planned_moves(run):
        moving.setdefault(target.mount, []).append(store)
    for volume in external_targets(run):
        traits = volume.traits
        holds = any(s.volume == volume.mount for s in run.stores)
        if traits is None or (traits.posix_permissions and traits.journaled):
            continue
        if not holds and volume.mount not in moving:
            continue  # the filesystem only matters for a drive that holds or will hold a store
        services = [
            s.title for s in moving.get(volume.mount, []) if s.id in SERVICE_STORES and os_name != "windows"
        ]
        missing = [
            n
            for n, ok in (
                ("file owners", traits.posix_permissions),
                ("hard links", traits.hard_links),
                ("symlinks", traits.symlinks),
            )
            if not ok
        ]
        why = [
            f"It's formatted as {display_name(volume.filesystem)}, which can't store "
            + (", ".join(missing[:-1]) + " or " + missing[-1] if len(missing) > 1 else "".join(missing))
            + (" and has no journal to protect it when unplugged." if not traits.journaled else ".")
        ]
        if services:
            why.append(f"{' and '.join(services)} run as their own user and can't own files there.")
        why.append(
            f"{native_filesystem(os_name)} has all of these. exFAT is only worth keeping for a drive you "
            "move between Windows, macOS and Linux."
        )
        found.append(
            Advice(
                rule="A05",
                severity="warn" if services else "suggest",
                title=f"Reformat {volume_names(run)[volume.mount]} as {native_filesystem(os_name)}",
                why=why,
                cites=[f"volume:{volume.mount}"],
                commands={os_name: commands.reformat(volume, os_name)},
                risks=[
                    "Reformatting erases everything on the drive."
                    + (f" It holds {text.size(volume.used_bytes)} now." if volume.used_bytes > 1e9 else "")
                ],
            )
        )
    return found


def a06_mount_at_boot(run: Run, home: str) -> list[Advice]:
    holding = {s.volume for s in run.stores} | {t.mount for _, _, t in planned_moves(run)}
    found: list[Advice] = []
    for volume in external_targets(run):
        if volume.at_boot is not False or volume.mount not in holding:
            continue
        found.append(
            Advice(
                rule="A06",
                severity="suggest",
                title=f"Make {volume_names(run)[volume.mount]} available at startup",
                why=[
                    "Right now it's mounted when you log in, after Ollama and Docker have started, so they "
                    "wouldn't find their models.",
                    "The `nofail` option lets the computer start normally when the drive is unplugged.",
                ],
                cites=[f"volume:{volume.mount}"],
                commands={run.machine.os: commands.mount_at_boot(volume, run.machine.os)},
                risks=["Mount it under a fixed path (as below) and use that path in the tools' settings."],
            )
        )
    return found


def a07_caches(run: Run, home: str) -> list[Advice]:
    found: list[Advice] = []
    os_name = run.machine.os
    for store in run.stores:
        for item in store.items:
            command = item.details.get("clean", "")
            reclaim = (
                int(item.details.get("reclaimable_bytes", item.bytes or 0))
                if store.id == "docker"
                else item.bytes
            )
            limit = DOCKER_MIN if store.id == "docker" else CACHE_MIN
            if not command or not reclaim or reclaim < limit:
                continue
            lines = commands.clean_cache(command, item.path or "", os_name)
            if not lines:
                continue
            why = [f"Frees {text.size(reclaim)}. Tools download what they need again later."]
            if item.details.get("same_volume") == "true":
                why.append(
                    "Clean it, don't move it: it links files into your projects, which only works on the "
                    "same drive."
                )
            found.append(
                Advice(
                    rule="A07",
                    severity="suggest",
                    title=f"Clean {_cache_name(store.id, item.names[0])}",
                    why=why,
                    cites=[f"store:{store.id}"],
                    gain=Gain(bytes_freed=reclaim),
                    commands={os_name: lines},
                )
            )
    return found


def a08_duplicates(run: Run, home: str) -> list[Advice]:
    found: list[Advice] = []
    os_name = run.machine.os
    for dup in run.duplicates:
        keep, *others = sorted(dup.paths, key=lambda p: ("blobs" not in p, p))
        lines = [
            (f'del "{o}" && mklink "{o}" "{keep}"' if os_name == "windows" else f'ln -sf "{keep}" "{o}"')
            for o in others
        ]
        found.append(
            Advice(
                rule="A08",
                severity="suggest",
                title=f"One file stored {len(dup.paths)} times ({text.size(dup.bytes)} each)",
                why=[
                    f"These files have the same bytes (SHA-256 {dup.sha256[:12]}…) in "
                    f"{', '.join(dup.stores)}.",
                    f"Keep {keep} and replace the others with links to it.",
                ],
                cites=[f"duplicate:{dup.sha256[:12]}"],
                gain=Gain(bytes_freed=dup.bytes * len(others)),
                commands={os_name: lines},
                risks=["A link breaks if the kept file is deleted or its drive is unplugged."],
            )
        )
    return found


def a09_keep_fast(run: Run, home: str) -> list[Advice]:
    found: list[Advice] = []
    now = datetime.fromisoformat(run.created)
    names = volume_names(run)
    for store, source, target in planned_moves(run):
        for item in store.items:
            if not item.accessed or not item.bytes or item.bytes < 5e9:
                continue
            if now - datetime.fromisoformat(item.accessed) > RECENT:
                continue
            here, there = load_time(item, source.mount, run), load_time(item, target.mount, run)
            if here is None or there is None or there.seconds < 2 * here.seconds:
                continue
            found.append(
                Advice(
                    rule="A09",
                    severity="info",
                    title=f"Keep {item.names[0]} on {names[source.mount]}",
                    why=[
                        f"You used it in the last two weeks. It loads in {text.seconds(here.seconds)} from "
                        f"{names[source.mount]} but would take {text.seconds(there.seconds)} from "
                        f"{names[target.mount]}.",
                        "When moving the rest of the store, leave this file where it is: "
                        + (
                            "ComfyUI reads both folders."
                            if store.id == "comfyui"
                            else "move it back afterwards."
                        ),
                    ],
                    cites=sorted(set(here.cites + there.cites)),
                    gain=Gain(seconds_saved_per_load=round(there.seconds - here.seconds, 1)),
                )
            )
    return found


def _cache_name(store_id: str, name: str) -> str:
    return f"Docker {name.lower()}" if store_id == "docker" else name
