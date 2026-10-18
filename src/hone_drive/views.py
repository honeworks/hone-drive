"""What the dashboard shows (design §10), computed from a run so the page only draws."""

from __future__ import annotations

from typing import Any

from hone_drive import links
from hone_drive.advice import advise
from hone_drive.compare import compare
from hone_drive.estimates import load_time
from hone_drive.model import Drive, Run, Volume

OTHER = "other files"
MIN_SEGMENT = 1e9  # stores under 1 GB (or 0.2% of the volume) are counted in "other files"


def run_view(run: Run, home: str) -> dict[str, Any]:
    advice = run.advice or advise(run, home)
    measured = sorted({m.volume for m in run.measurements if m.test in ("seq-read", "model-read")})
    return {
        "run": run.model_dump(mode="json"),
        "advice": [a.model_dump(mode="json") for a in advice],
        "drives": [_drive(run, d) for d in run.drives],
        "volumes": [_volume(run, v) for v in run.volumes],
        "models": _models(run, measured),
        "measured_volumes": measured,
    }


def _drive(run: Run, drive: Drive) -> dict[str, Any]:
    return {
        "id": drive.id,
        "link_text": links.describe(drive.link),
        "volumes": [v.mount for v in run.volumes if v.drive_id == drive.id],
    }


def _volume(run: Run, volume: Volume) -> dict[str, Any]:
    """The volume's bar: each store on it, the rest of what was counted, and the unaccounted space."""
    segments: list[dict[str, Any]] = []
    for store in run.stores:
        if store.volume is None:  # caches: each item lives where its tool keeps it
            size = sum(i.bytes or 0 for i in store.items if i.volume == volume.mount)
        else:
            size = store.bytes or 0 if store.volume == volume.mount else 0
        if size >= max(MIN_SEGMENT, volume.total_bytes * 0.002):
            segments.append({"label": store.title, "bytes": size, "kind": store.id})
    acc = next((a for a in run.accounting if a.volume == volume.mount), None)
    known = sum(s["bytes"] for s in segments)
    unaccounted = acc.unaccounted_bytes if acc else None
    rest = volume.used_bytes - known - (unaccounted or 0)
    if rest > 0:
        segments.append({"label": OTHER, "bytes": rest, "kind": "other"})
    if unaccounted:
        segments.append({"label": "not accounted for", "bytes": unaccounted, "kind": "unaccounted"})
    return {"mount": volume.mount, "segments": segments, "accounted": acc is not None}


def _models(run: Run, measured: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    duplicated = {p for d in run.duplicates for p in d.paths}
    for store in run.stores:
        if store.id in ("docker", "caches"):
            continue
        for item in store.items:
            if not item.bytes or item.id == "(other files)":
                continue
            loads: dict[str, Any] = {}
            for mount in sorted({*measured, *(v.mount for v in run.volumes if _external(run, v))}):
                estimate = load_time(item, mount, run)
                if estimate is not None:
                    loads[mount] = {
                        "seconds": estimate.seconds,
                        "low": estimate.low,
                        "high": estimate.high,
                        "basis": estimate.basis,
                    }
            rows.append(
                {
                    "store": store.title,
                    "store_id": store.id,
                    "names": item.names,
                    "bytes": item.bytes,
                    "volume": item.volume,
                    "path": item.path,
                    "accessed": item.accessed,
                    "loads": loads,
                    "duplicate": item.path in duplicated,
                    "details": item.details,
                }
            )
    return sorted(rows, key=lambda r: -r["bytes"])


def _external(run: Run, volume: Volume) -> bool:
    drive = run.drive(volume.drive_id)
    return drive is not None and drive.external


def compare_view(a: Run, b: Run) -> dict[str, Any]:
    return {"a": a.id, "b": b.id, "changes": [vars(c) for c in compare(a, b)]}
