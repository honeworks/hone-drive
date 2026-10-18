"""Runs on disk (design §9): one JSON file per run in the data folder, written atomically."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from hone_drive.errors import NotFound, UnsupportedFormat
from hone_drive.model import RUN_FORMAT, Run


def data_home(env: dict[str, str] | None = None, platform: str | None = None) -> Path:
    """`HONE_DRIVE_HOME`, else the OS's place for application data."""
    env = dict(os.environ) if env is None else env
    platform = platform or sys.platform
    if env.get("HONE_DRIVE_HOME"):
        return Path(env["HONE_DRIVE_HOME"])
    home = Path(env.get("HOME") or env.get("USERPROFILE") or Path.home())
    if platform == "darwin":
        return home / "Library" / "Application Support" / "hone-drive"
    if platform == "win32":
        return Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local") / "hone-drive"
    return Path(env.get("XDG_DATA_HOME") or home / ".local" / "share") / "hone-drive"


def runs_dir(home: Path | None = None) -> Path:
    return (home or data_home()) / "runs"


def save_run(run: Run, home: Path | None = None) -> Path:
    folder = runs_dir(home)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{run.id}.json"
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".run-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(run.model_dump_json(indent=1))
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


def load_run(run_id: str, home: Path | None = None) -> Run:
    """A stored run by id, or `latest` for the newest."""
    if run_id == "latest":
        ids = list_runs(home)
        if not ids:
            raise NotFound("no runs yet: run `hone-drive scan` first")
        run_id = ids[0]
    path = runs_dir(home) / f"{run_id}.json"
    if not path.is_file():
        raise NotFound(f"no run {run_id!r} in {runs_dir(home)}; `hone-drive runs` lists them")
    data = json.loads(path.read_text(encoding="utf-8"))
    if int(data.get("format", 0)) > RUN_FORMAT:
        raise UnsupportedFormat(f"run {run_id} was written by a newer hone-drive; upgrade to read it")
    return Run.model_validate(data)


def list_runs(home: Path | None = None) -> list[str]:
    """Run ids, newest first."""
    folder = runs_dir(home)
    if not folder.is_dir():
        return []
    return sorted((p.stem for p in folder.glob("*.json")), reverse=True)
