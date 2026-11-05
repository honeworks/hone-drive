"""`hone-drive shelf`: move models to a drive and bring them back (change 0003). Exit code 0 when every
model was done, 1 when one was refused or failed, 2 on an error (as the other commands)."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from typing import Any

from hone_drive import shelf, text
from hone_drive.errors import NotFound
from hone_drive.model import Run
from hone_drive.shelf_move import Plan, Result, Tick


def _out(line: str = "") -> None:
    sys.stdout.write(line + "\n")


def _shelf_run() -> Run:
    """A fresh scan: what is on the computer now (a move or a restore saves the run it leaves behind)."""
    from hone_drive.cli import with_history  # noqa: PLC0415 - cli imports this module
    from hone_drive.scanner import scan  # noqa: PLC0415

    return with_history(scan())


def resolve(run: Run, given: list[str]) -> list[str]:
    """Keys from what the user typed: a key (`ollama:gemma4:31b`) or a name only one model has."""
    known = set(shelf.local_models(run)) | {k for d in shelf.contents(run) for k in d.shelf.models}
    keys: list[str] = []
    for name in given:
        if name in known:
            keys.append(name)
            continue
        matches = sorted(k for k in known if k.split(":", 1)[1] == name or k.endswith(f"/{name}"))
        if len(matches) != 1:
            hint = f"; did you mean one of {matches}?" if matches else "; `hone-drive shelf` lists them"
            raise NotFound(f"no single model called {name!r}{hint}")
        keys.append(matches[0])
    return keys


def cmd_list(args: argparse.Namespace) -> int:
    run = _shelf_run()
    local = shelf.local_models(run)
    drives = shelf.contents(run)
    if args.json:
        _out(json.dumps([{"mount": d.mount, **d.shelf.model_dump(mode="json")} for d in drives], indent=1))
        return 0
    if not drives:
        _out("No models on any drive yet. `hone-drive shelf move MODEL` puts one there.")
    for found in drives:
        where = found.mount or "not connected"
        _out(f"{found.shelf.name} ({where})")
        for key, entry in sorted(found.shelf.models.items()):
            size = text.size(sum(f.bytes for f in entry.files))
            state = "also on this computer" if key in local else "only here"
            state = state if entry.complete else "copy not complete"
            _out(f"  {size:>9}  {key}  ({state})")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    run = _shelf_run()
    worst = 0
    for tool in [args.tool] if args.tool else shelf.TOOLS:
        found = shelf.check(run, tool)
        _out(f"{tool}: " + ("ready" if found.ok else "; ".join(found.problems[:2])))
        for line in found.fix:
            _out(f"    {line}" if line.startswith("#") else f"    $ {line}")
        worst = worst or (0 if found.ok else 1)
    return worst


def _print_plan(the_plan: Plan) -> None:
    arrow = "→" if the_plan.action == "move" else "←"
    for item in the_plan.models:
        if item.refused:
            _out(f"✗ {item.key}: {item.refused}")
            for line in item.fix:
                _out(f"    {line}" if line.startswith("#") else f"    $ {line}")
        else:
            copy = f"copies {text.size(item.copy_bytes)}" if item.copy_bytes else "already there"
            _out(f"✓ {item.key}  {text.size(item.bytes)} {arrow} {the_plan.drive} ({copy})")


def _tick(t: Tick) -> None:
    sys.stderr.write(f"\r{t.action} {t.model}: {100 * t.done // max(t.total, 1)}%   ")


def _report(result: Result, plans: list[Plan], as_json: bool) -> int:
    if as_json:
        _out(json.dumps({"result": asdict(result), "plans": [asdict(p) for p in plans]}, indent=1))
    else:
        sys.stderr.write("\n")
        for key in result.done:
            _out(f"done: {key}")
        for key, why in result.failed.items():
            _out(f"failed: {key}: {why}")
        if result.stopped:
            _out("stopped: what was finished is kept; run it again to go on")
    refused = any(p.refused for plan in plans for p in plan.models)
    return 1 if result.failed or result.stopped or refused else 0


def cmd_move(args: argparse.Namespace) -> int:
    run = _shelf_run()
    the_plan = shelf.plan(run, resolve(run, args.models), to=args.to)
    if not args.json:
        _print_plan(the_plan)
    if args.dry_run:
        return 1 if any(p.refused for p in the_plan.models) else 0
    options = shelf.Options(keep_local=args.keep_local, on_tick=None if args.json else _tick)
    return _finish(_report(shelf.move(run, the_plan, options), [the_plan], args.json))


def cmd_restore(args: argparse.Namespace) -> int:
    run = _shelf_run()
    plans = shelf.plan_restore(run, resolve(run, args.models), args.source)
    if not args.json:
        for the_plan in plans:
            _print_plan(the_plan)
    if args.dry_run:
        return 1 if any(p.refused for plan in plans for p in plan.models) else 0
    options = shelf.Options(on_tick=None if args.json else _tick)
    from hone_drive.shelf_restore import restore  # noqa: PLC0415

    return _finish(_report(restore(run, plans, options), plans, args.json))


def cmd_remove(args: argparse.Namespace) -> int:
    run = _shelf_run()
    return _report(shelf.remove(run, resolve(run, args.models), args.source, args.only_copy), [], False)


def cmd_verify(args: argparse.Namespace) -> int:
    damaged = shelf.verify(_shelf_run(), args.drive)
    for key, why in damaged.items():
        _out(f"damaged: {key}: {why}")
    _out("every copy matches its SHA-256" if not damaged else "move these to the drive again to repair them")
    return 1 if damaged else 0


def _finish(code: int) -> int:
    """Save a scan of what the move or restore left, so the dashboard and `runs` show it."""
    from hone_drive.cli import save_with_advice  # noqa: PLC0415

    save_with_advice(_shelf_run())
    return code


def add_parser(sub: Any) -> None:  # argparse's subparsers action (a private type)
    top = sub.add_parser("shelf", help="move models to a drive and bring them back (lists them by default)")
    top.add_argument("--json", action="store_true")
    top.set_defaults(func=cmd_list)
    shelf_sub = top.add_subparsers(dest="shelf_command")
    check = shelf_sub.add_parser("check", help="can hone-drive move this tool's models here? the fix if not")
    check.add_argument("tool", nargs="?", choices=shelf.TOOLS)
    check.set_defaults(func=cmd_check)
    move = shelf_sub.add_parser("move", help="copy models to a drive, check them, remove them here")
    move.add_argument("models", nargs="+", metavar="MODEL")
    move.add_argument("--to", metavar="DRIVE", help="a drive's name or mount point (default: emptiest)")
    move.add_argument("--keep-local", action="store_true", help="copy and check, but keep them here too")
    move.add_argument("--dry-run", action="store_true", help="say what would happen; change nothing")
    move.add_argument("--json", action="store_true")
    move.set_defaults(func=cmd_move)
    restore = shelf_sub.add_parser("restore", help="copy models back from a drive (the drive keeps them)")
    restore.add_argument("models", nargs="+", metavar="MODEL")
    restore.add_argument("--from", dest="source", metavar="DRIVE")
    restore.add_argument("--dry-run", action="store_true")
    restore.add_argument("--json", action="store_true")
    restore.set_defaults(func=cmd_restore)
    remove = shelf_sub.add_parser("remove", help="remove models from a drive (never from this computer)")
    remove.add_argument("models", nargs="+", metavar="MODEL")
    remove.add_argument("--from", dest="source", metavar="DRIVE")
    remove.add_argument("--only-copy", action="store_true", help="yes, even when it is the only copy")
    remove.set_defaults(func=cmd_remove)
    verify = shelf_sub.add_parser("verify", help="hash every copy on a drive again")
    verify.add_argument("drive", nargs="?")
    verify.set_defaults(func=cmd_verify)
