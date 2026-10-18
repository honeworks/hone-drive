"""`scan()`: one look at the machine's storage (design §1 steps 1 and 2, §5)."""

from __future__ import annotations

import hashlib
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from hone_drive.accounting import account
from hone_drive.duplicates import find_duplicates
from hone_drive.model import Folder, ProbeCommand, Run
from hone_drive.ports import Probe
from hone_drive.stores import Context, Finder, all_finders
from hone_drive.system import SubprocessRunner
from hone_drive.volumes import mount_of

Progress = Callable[[str], None]


def os_name(platform: str | None = None) -> str:
    platform = platform or sys.platform
    if platform.startswith("linux"):
        return "linux"
    return "macos" if platform == "darwin" else "windows" if platform == "win32" else "linux"


def probe_for(platform: str | None = None) -> Probe:
    """The probe of this OS (design §3.1)."""
    name = os_name(platform)
    if name == "macos":
        from hone_drive.probe_macos import MacProbe  # noqa: PLC0415 - one OS's probe per process

        return MacProbe()
    if name == "windows":
        from hone_drive.probe_windows import WindowsProbe  # noqa: PLC0415

        return WindowsProbe()
    from hone_drive.probe_linux import LinuxProbe  # noqa: PLC0415

    return LinuxProbe()


def run_id(created: datetime, hostname: str) -> str:
    stamp = created.strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{hashlib.sha256(f'{stamp}{hostname}'.encode()).hexdigest()[:6]}"


def scan(
    *,
    probe: Probe | None = None,
    context: Context | None = None,
    finders: dict[str, Finder] | None = None,
    add: list[str] | None = None,
    accounting: bool = False,
    duplicates: bool = False,
    progress: Progress | None = None,
) -> Run:
    """Drives, volumes and stores; with `accounting`, a walk of each volume that looks for unaccounted
    space (minutes on a large disk); with `duplicates`, hashing of same-size files across stores."""
    from hone_drive import __version__  # noqa: PLC0415 - the package imports this module

    say: Progress = progress or (lambda _message: None)
    probe = probe or probe_for()
    say("machine and drives")
    machine = probe.machine()
    created = datetime.now(UTC)
    run = Run(
        id=run_id(created, machine.hostname),
        created=created.isoformat(timespec="seconds"),
        version=__version__,
        machine=machine,
        drives=probe.drives(),
        volumes=probe.volumes(),
    )
    ctx = context or Context(os_name=machine.os, home=str(Path.home()))
    ctx.extra = [*ctx.extra, *(add or [])]
    _mark_system(run, ctx.home)
    for store_id, find in (all_finders() if finders is None else finders).items():
        say(f"store {store_id}")
        run.stores += find(ctx)
    _place(run)
    if duplicates:
        say("duplicates")
        run.duplicates = find_duplicates(run.stores)
    if accounting:
        for volume in run.volumes:
            say(f"accounting {volume.mount}")
            run.accounting.append(account(volume, machine.os, explained=_explained(run, volume.mount)))
    run.commands = probe.commands() + _commands(ctx)
    return run


def _mark_system(run: Run, home: str) -> None:
    mounts = [v.mount for v in run.volumes]
    system = {mount_of(home, mounts), mount_of(os.path.abspath(os.sep), mounts)}
    for volume in run.volumes:
        volume.system = volume.mount in system


def _place(run: Run) -> None:
    """Each store and item gets the mount point of the volume holding it."""
    mounts = [v.mount for v in run.volumes]
    for store in run.stores:
        store.volume = mount_of(os.path.realpath(store.path), mounts) if store.path else None
        for item in store.items:
            item.volume = mount_of(os.path.realpath(item.path), mounts) if item.path else store.volume


def _explained(run: Run, mount: str) -> list[Folder]:
    """Stores on `mount` that the walk can't read but a server sized (Ollama's service folder, Docker)."""
    return [
        Folder(path=s.path, bytes=s.bytes)
        for s in run.stores
        if s.volume == mount and s.path and s.bytes and s.source != "files" and not os.access(s.path, os.R_OK)
    ]


def _commands(ctx: Context) -> list[ProbeCommand]:
    return list(ctx.run.log) if isinstance(ctx.run, SubprocessRunner) else []
