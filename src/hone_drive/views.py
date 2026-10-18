"""What the dashboard shows (design §10, change 0002), computed from a run so the page only draws.

Everything here is in the user's words: drives by name, verdicts with a tone, advice as a plan."""

from __future__ import annotations

from typing import Any

from hone_drive import presentation as p
from hone_drive.advice import advise
from hone_drive.compare import compare
from hone_drive.estimates import load_time
from hone_drive.filesystems import display_name
from hone_drive.model import Drive, Run, Volume

OTHER = "Everything else"
MIN_SEGMENT = 1e9  # stores under 1 GB (or 0.2% of the volume) are counted in "everything else"
BIGGEST = 12


def run_view(run: Run, home: str) -> dict[str, Any]:
    advice = run.advice or advise(run, home)
    the_plan = p.plan(run, advice)
    names = p.volume_names(run)
    return {
        "run": {
            "id": run.id,
            "created": run.created,
            "version": run.version,
            "machine": run.machine.model_dump(mode="json"),
            "os": run.machine.os,
        },
        "headline": p.headline(run, the_plan),
        "drives": [_drive(run, d, names) for d in run.drives],
        "volumes": [_volume(run, v, names) for v in run.volumes if v.total_bytes >= p.SMALL_VOLUME],
        "biggest": _biggest(run, names),
        "plan": the_plan,
        "models": _models(run, names),
        "measurements": [m.model_dump(mode="json") for m in run.measurements],
        "accounted": bool(run.accounting),
    }


def _drive(run: Run, drive: Drive, names: dict[str, str]) -> dict[str, Any]:
    volumes = [v for v in run.volumes if v.drive_id == drive.id]
    main = max(volumes, key=lambda v: v.total_bytes) if volumes else None
    return {
        "id": drive.id,
        "name": p.drive_names(run)[drive.id],
        "model": drive.model,
        "kind": p.KIND_LABELS.get(drive.kind, "drive"),
        "external": drive.external,
        "size": drive.size_bytes,
        "connection": p.connection(drive),
        "speed": p.speed(run, main) if main else None,
        "mount": main.mount if main else None,
        "test_folder": ("~" if main.system else main.mount) if main else None,
        "volumes": [_volume(run, v, names) for v in volumes],
        "link": drive.link.model_dump(mode="json"),
        "unknown": sorted({*drive.unknown.values(), *drive.link.unknown.values()}),
    }


def _volume(run: Run, volume: Volume, names: dict[str, str]) -> dict[str, Any]:
    tone, label = p.space_tone(volume.used_fraction)
    traits = volume.traits
    missing = (
        []
        if traits is None
        else [
            n
            for n, ok in (
                ("file owners", traits.posix_permissions),
                ("hard links", traits.hard_links),
                ("symlinks", traits.symlinks),
                ("a journal", traits.journaled),
                ("files over 4 GB", traits.large_files),
            )
            if not ok
        ]
    )
    return {
        "mount": volume.mount,
        "name": names[volume.mount],
        "used": volume.used_bytes,
        "total": volume.total_bytes,
        "free": volume.free_bytes,
        "fraction": round(volume.used_fraction, 4),
        "tone": tone,
        "tone_label": label,
        "filesystem": display_name(volume.filesystem),
        "missing": missing,
        "at_boot": volume.at_boot,
        "system": volume.system,
        "segments": _segments(run, volume),
    }


def _segments(run: Run, volume: Volume) -> list[dict[str, Any]]:
    """The volume's bar: each store on it, everything else, and space no scan could see."""
    segments: list[dict[str, Any]] = []
    for store in run.stores:
        if store.volume is None:  # caches: each item lives where its tool keeps it
            size = sum(i.bytes or 0 for i in store.items if i.volume == volume.mount)
        else:
            size = store.bytes or 0 if store.volume == volume.mount else 0
        if size >= max(MIN_SEGMENT, volume.total_bytes * 0.002):
            segments.append({"label": store.title, "bytes": size, "kind": store.id})
    acc = next((a for a in run.accounting if a.volume == volume.mount), None)
    unaccounted = acc.unaccounted_bytes if acc and acc.measure_command else 0
    rest = volume.used_bytes - sum(s["bytes"] for s in segments) - unaccounted
    if rest > 0:
        segments.append({"label": OTHER, "bytes": rest, "kind": "other"})
    if unaccounted:
        segments.append(
            {"label": "Hidden (needs administrator rights)", "bytes": unaccounted, "kind": "unaccounted"}
        )
    return segments


def _biggest(run: Run, names: dict[str, str]) -> list[dict[str, Any]]:
    """The biggest single things: models, caches, Docker parts."""
    rows: list[dict[str, Any]] = []
    for store in run.stores:
        for item in store.items:
            if item.bytes and not item.id.startswith("("):
                name = (
                    f"Docker {item.names[0].lower()}" if store.id == "docker" else ", ".join(item.names[:2])
                )
                rows.append(
                    {
                        "name": name,
                        "kind": store.title,
                        "store": store.id,
                        "bytes": item.bytes,
                        "where": names.get(item.volume or "", "?"),
                    }
                )
    return sorted(rows, key=lambda r: -r["bytes"])[:BIGGEST]


def _models(run: Run, names: dict[str, str]) -> dict[str, Any]:
    targets = [
        v
        for v in run.volumes
        if v.total_bytes >= p.SMALL_VOLUME
        and (any(m.volume == v.mount for m in run.measurements) or _external(run, v) or v.system)
    ]
    columns: list[dict[str, Any]] = [
        {
            "mount": v.mount,
            "name": names[v.mount],
            "measured": any(
                m.volume == v.mount and m.test in ("seq-read", "model-read") for m in run.measurements
            ),
        }
        for v in targets
    ]
    duplicated = {path for d in run.duplicates for path in d.paths}
    rows: list[dict[str, Any]] = []
    for store in run.stores:
        if store.id in ("docker", "caches"):
            continue
        for item in store.items:
            if not item.bytes or item.id.startswith("("):
                continue
            loads = {v.mount: e.seconds for v in targets if (e := load_time(item, v.mount, run)) is not None}
            rows.append(
                {
                    "names": item.names,
                    "store": store.title,
                    "store_id": store.id,
                    "bytes": item.bytes,
                    "where": names.get(item.volume or "", "?"),
                    "loads": loads,
                    "duplicate": item.path in duplicated,
                    "details": item.details,
                }
            )
    return {"columns": columns, "rows": sorted(rows, key=lambda r: -r["bytes"])}


def _external(run: Run, volume: Volume) -> bool:
    drive = run.drive(volume.drive_id)
    return drive is not None and drive.external


def compare_view(a: Run, b: Run) -> dict[str, Any]:
    return {"a": a.id, "b": b.id, "changes": [vars(c) for c in compare(a, b)]}
