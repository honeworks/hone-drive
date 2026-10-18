"""The command line (design §11, decision D-002): a short summary for people, or the run's JSON."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from hone_drive import links, text
from hone_drive.errors import HoneDriveError
from hone_drive.model import Run
from hone_drive.runs import list_runs, load_run, save_run

Command = Callable[[argparse.Namespace], int]


def _out(line: str = "") -> None:
    sys.stdout.write(line + "\n")


def _err(line: str) -> None:
    sys.stderr.write(line + "\n")


def summary(run: Run) -> list[str]:
    lines = [f"{run.machine.os_version} · {run.machine.cpu or '?'} · RAM {text.size(run.machine.ram_bytes)}"]
    lines += [
        f"GPU {g.name} {text.size(g.memory_bytes) if g.memory_bytes else ''}".rstrip()
        for g in run.machine.gpus
    ]
    lines.append("")
    for drive in run.drives:
        where = "external" if drive.external else "internal"
        lines.append(
            f"{drive.id}: {drive.model or '?'} · {text.size(drive.size_bytes)} · {drive.kind}, {where} · "
            f"{links.describe(drive.link)}" + ("  ← LIMITED" if drive.link.limited else "")
        )
    lines.append("")
    for volume in run.volumes:
        lines.append(
            f"{volume.mount}: {volume.filesystem} · {text.size(volume.used_bytes)} used of "
            f"{text.size(volume.total_bytes)} ({text.percent(volume.used_fraction)}) · "
            f"{text.size(volume.free_bytes)} free"
        )
    lines.append("")
    for store in sorted(run.stores, key=lambda s: -(s.bytes or 0)):
        note = " (via the Ollama server: its folder is not readable)" if store.source == "ollama api" else ""
        lines.append(f"{text.size(store.bytes):>9}  {store.title}  {store.path or '?'}{note}")
    for acc in run.accounting:
        if acc.measure_command:
            lines += [
                "",
                f"{text.size(acc.unaccounted_bytes)} of {acc.volume} could not be accounted for. "
                f"To measure it: {acc.measure_command}",
            ]
    return lines


def cmd_scan(args: argparse.Namespace) -> int:
    from hone_drive.scanner import Progress, scan  # noqa: PLC0415 - keeps `--help` fast

    progress: Progress | None = None if args.json else (lambda message: _err(f"… {message}"))
    run = scan(
        add=[str(Path(p).expanduser()) for p in args.add],
        accounting=args.account,
        duplicates=args.duplicates,
        progress=progress,
    )
    path = save_run(run)
    if args.json:
        _out(run.model_dump_json(indent=1))
    else:
        for line in summary(run):
            _out(line)
        _out(f"\nsaved as run {run.id} ({path})")
    return 0


def parse_size(value: str) -> int:
    """`4G`, `512M`, `1.5G` (binary units, as the benchmark writes in 16 MiB chunks)."""
    units = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    value = value.strip().upper().removesuffix("B").removesuffix("I")
    if value and value[-1] in units:
        return int(float(value[:-1]) * units[value[-1]])
    return int(value)


def measurements(run: Run) -> list[str]:
    lines: list[str] = []
    for m in run.measurements:
        extra = f" · {m.iops:,.0f} IOPS at 32 in flight, {m.latency_ms} ms one at a time" if m.iops else ""
        label = f" · {m.label}" if m.label else ""
        cache = "" if m.cache == "direct" else f" · cache: {m.cache}"
        stopped = "  (stopped early)" if m.partial else ""
        lines.append(f"{m.test:>15} {m.volume}  {text.speed(m.gbs)}{extra}{label}{cache}{stopped}")
    return lines


def cmd_bench(args: argparse.Namespace) -> int:
    from hone_drive.benchmark import Options, Tick, bench, default_folders  # noqa: PLC0415
    from hone_drive.scanner import Progress, scan  # noqa: PLC0415

    progress: Progress | None = None if args.json else (lambda message: _err(f"… {message}"))
    run = load_run(args.run) if args.run else scan(progress=progress)
    folders: list[str] = args.paths or default_folders(run, str(Path.home()))

    def tick(t: Tick) -> None:
        sys.stderr.write(f"\r{t.test} {t.volume}: {100 * t.done // max(t.total, 1)}% {text.speed(t.gbs)}   ")

    options = Options(size=parse_size(args.size), model_files=args.model, on_tick=None if args.json else tick)
    bench(run, folders, args.tests.split(","), options)
    save_run(run)
    if args.json:
        _out(run.model_dump_json(indent=1))
        return 0
    _err("")
    for line in measurements(run):
        _out(line)
    _out(f"\nsaved as run {run.id}")
    return 0


def cmd_runs(args: argparse.Namespace) -> int:
    for run_id in list_runs():
        _out(run_id)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    run = load_run(args.run)
    if args.json:
        _out(run.model_dump_json(indent=1))
    else:
        for line in [*summary(run), "", *measurements(run)]:
            _out(line)
    return 0


def parser() -> argparse.ArgumentParser:
    from hone_drive import __version__  # noqa: PLC0415

    top = argparse.ArgumentParser(prog="hone-drive", description="A storage dashboard for AI developers.")
    top.add_argument("--version", action="version", version=f"hone-drive {__version__}")
    sub = top.add_subparsers(dest="command")
    scan = sub.add_parser("scan", help="find drives, volumes and model stores; save the run")
    scan.add_argument("--add", action="append", default=[], metavar="PATH", help="also size this folder")
    scan.add_argument("--account", action="store_true", help="walk each volume for unaccounted space (slow)")
    scan.add_argument("--duplicates", action="store_true", help="hash same-size files across stores")
    scan.add_argument("--json", action="store_true")
    scan.set_defaults(func=cmd_scan)
    bench = sub.add_parser("bench", help="measure drives with the page cache bypassed")
    bench.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help="folders on the drives to test (default: home and every external drive)",
    )
    bench.add_argument("--tests", default="seq", help="comma-separated: seq, random, sustained, model")
    bench.add_argument("--size", default="4G", help="size of the test file (default 4G)")
    bench.add_argument("--model", action="append", default=[], metavar="FILE", help="a model file to read")
    bench.add_argument("--run", help="add the measurements to this stored run instead of a new scan")
    bench.add_argument("--json", action="store_true")
    bench.set_defaults(func=cmd_bench)
    sub.add_parser("runs", help="list stored runs, newest first").set_defaults(func=cmd_runs)
    show = sub.add_parser("show", help="show a stored run")
    show.add_argument("run", nargs="?", default="latest")
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=cmd_show)
    return top


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    func: Command | None = getattr(args, "func", None)
    if func is None:
        args = parser().parse_args(["scan"])  # the dashboard becomes the default in milestone M3
        func = cmd_scan
    try:
        return func(args)
    except HoneDriveError as exc:
        _err(f"hone-drive: {exc}")
        return 2
