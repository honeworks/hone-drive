"""The model shelf (change 0003): move models to a drive, prove every copy, and only then remove them
from the computer; bring them back the same way. Models are used from the computer; a drive is where they
are kept meanwhile. Nothing changes unless one of these functions is called.

    from hone_drive import scan, shelf
    run = scan()
    print(shelf.check(run, "ollama"))                  # can hone-drive do this here? the fix if not
    the_plan = shelf.plan(run, ["ollama:gemma4:31b"], to="/run/media/user/PNYRP60PSSD")
    shelf.move(run, the_plan)                          # copy, check, remove from the computer
    shelf.contents(run)                                # every drive's shelf, connected or not
    shelf.restore(run, ["ollama:gemma4:31b"])          # copy back, check; the drive keeps its copy
"""

from __future__ import annotations

import os

from hone_drive import access
from hone_drive.access import Access
from hone_drive.model import Run
from hone_drive.shelf_models import TOOLS, key_of, local_models
from hone_drive.shelf_move import Options, Plan, Planned, Result, Tick, context, move, plan, target
from hone_drive.shelf_restore import OnDrive, contents, find, plan_restore, remove, verify
from hone_drive.shelf_restore import restore as restore_plans


def restore(run: Run, keys: list[str], drive: str | None = None, options: Options | None = None) -> Result:
    """Bring `keys` back to where they were on the computer, checked; the drive keeps its copy."""
    return restore_plans(run, plan_restore(run, keys, drive), options)


def check(run: Run, tool: str) -> Access:
    """Whether hone-drive can read, write and remove `tool`'s model files on this machine, and the command
    that fixes it here when it can't (it never runs it)."""
    folders: set[str] = set()
    for store in run.stores:
        if store.id == tool and store.path:
            folders.add(store.path)
            if tool == "ollama":
                folders |= {os.path.join(store.path, "blobs"), os.path.join(store.path, "manifests")}
    for model in local_models(run).values():
        if model.tool == tool:
            folders |= set(model.folders())
    if not folders:
        return Access(ok=True)
    return access.check(sorted(folders), [], run.machine.os)


__all__ = [
    "TOOLS",
    "Access",
    "OnDrive",
    "Options",
    "Plan",
    "Planned",
    "Result",
    "Tick",
    "check",
    "contents",
    "context",
    "find",
    "key_of",
    "local_models",
    "move",
    "plan",
    "plan_restore",
    "remove",
    "restore",
    "target",
    "verify",
]
