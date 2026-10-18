"""Fakes, recorded machines and the `Probe` contract checker (design §12, §14).

- `FakeProbe`: a machine described in data; `reference_probe()` is the machine of history/0000.
- `RecordedSystem` replays what a real machine answered (files, commands, partitions) on any OS, so a
  Linux probe is tested on Windows CI and the other way round.
- `record(os_name)` captures a fixture from this machine with serials, UUIDs and names replaced:
  `python -m hone_drive.testing out.json`.
"""

from __future__ import annotations

import json
import re
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from hone_drive.links import pcie_link, usb_link
from hone_drive.model import Drive, Link, MachineFacts, ProbeCommand, Volume
from hone_drive.ports import Probe
from hone_drive.system import TIMEOUT_S, LocalFiles, Output, SubprocessRunner
from hone_drive.volumes import Partition, disk_usage, psutil_partitions

GB = 1000**3


@dataclass
class FakeProbe:
    facts: MachineFacts
    drive_list: list[Drive] = field(default_factory=list[Drive])
    volume_list: list[Volume] = field(default_factory=list[Volume])

    def machine(self) -> MachineFacts:
        return self.facts.model_copy(deep=True)

    def drives(self) -> list[Drive]:
        return [d.model_copy(deep=True) for d in self.drive_list]

    def volumes(self) -> list[Volume]:
        return [v.model_copy(deep=True) for v in self.volume_list]

    def commands(self) -> list[ProbeCommand]:
        return []


def reference_probe(external_mount: str = "/run/media/user/PNYRP60PSSD") -> FakeProbe:
    """The laptop of history/0000: a 94%-full NVMe and a 20 Gbps USB SSD linked at 10 Gbps on one lane."""
    from hone_drive.filesystems import traits  # noqa: PLC0415

    machine = MachineFacts(
        os="linux",
        os_version="EndeavourOS (kernel 6.18)",
        hostname="reference",
        cpu="Intel(R) Core(TM) i9-14900HX",
        ram_bytes=33_239_351_296,
    )
    nvme = Drive(
        id="nvme0n1",
        model="WD PC SN560 SDDPNQE-1T00-1102",
        size_bytes=1_024_209_543_168,
        kind="nvme",
        external=False,
        rotational=False,
        link=pcie_link(16.0, 4, 16.0, 4),
    )
    usb = Drive(
        id="sda",
        model="PNY RP60 PSSD",
        size_bytes=1_024_209_543_168,
        kind="usb-ssd",
        external=True,
        rotational=False,
        link=usb_link(10.0, 1, 20.0, 2),
    )
    root = Volume(
        mount="/",
        device="/dev/nvme0n1p2",
        drive_id="nvme0n1",
        filesystem="ext4",
        total_bytes=1_004_864_958_464,
        used_bytes=891_194_318_848,
        free_bytes=62_550_867_968,
        system=True,
        traits=traits("ext4", "linux"),
    )
    ext = Volume(
        mount=external_mount,
        device="/dev/sda1",
        drive_id="sda",
        filesystem="exfat",
        label="PNYRP60PSSD",
        total_bytes=1_024_208_404_480,
        used_bytes=2_293_760,
        free_bytes=1_024_206_110_720,
        traits=traits("exfat", "linux"),
    )
    return FakeProbe(machine, [nvme, usb], [root, ext])


# -- recorded machines -------------------------------------------------------------------------------------


@dataclass
class RecordedSystem:
    """A machine's answers from a fixture: `files`, `links` (realpath), `dirs`, `commands` (an argument
    `*` matches any value, e.g. a long PowerShell script), `partitions`, `usage`."""

    data: dict[str, Any]
    log: list[ProbeCommand] = field(default_factory=list[ProbeCommand])

    def read(self, path: str) -> str | None:
        return cast(dict[str, str], self.data.get("files", {})).get(path)

    def realpath(self, path: str) -> str:
        return cast(dict[str, str], self.data.get("links", {})).get(path, path)

    def listdir(self, path: str) -> list[str]:
        return list(cast(dict[str, list[str]], self.data.get("dirs", {})).get(path, []))

    def __call__(self, args: list[str], timeout: float = TIMEOUT_S) -> Output:
        for command in cast(list[dict[str, Any]], self.data.get("commands", [])):
            if len(command["args"]) == len(args) and all(
                a in ("*", b) for a, b in zip(command["args"], args, strict=True)
            ):
                return Output(command.get("code"), command.get("stdout", ""))
        return Output(None, "", f"{args[0]} is not installed")

    def partitions(self) -> list[Partition]:
        return [Partition(*p) for p in cast(list[list[str]], self.data.get("partitions", []))]

    def usage(self, mount: str) -> tuple[int, int, int]:
        total, used, free = cast(dict[str, list[int]], self.data["usage"])[mount]
        return total, used, free


def recorded_probe(data: dict[str, Any]) -> Probe:
    """The probe of the fixture's OS, answering from the fixture."""
    system = RecordedSystem(data)
    host = str(data.get("hostname", "host"))
    if data["os"] == "linux":
        from hone_drive.probe_linux import LinuxProbe  # noqa: PLC0415

        return LinuxProbe(
            run=system, files=system, partitions=system.partitions, usage=system.usage, hostname=host
        )
    if data["os"] == "macos":
        from hone_drive.probe_macos import MacProbe  # noqa: PLC0415

        return MacProbe(run=system, partitions=system.partitions, usage=system.usage, hostname=host)
    from hone_drive.probe_windows import WindowsProbe  # noqa: PLC0415

    return WindowsProbe(run=system, partitions=system.partitions, usage=system.usage, hostname=host)


@dataclass
class _Recorder(LocalFiles):
    data: dict[str, Any] = field(default_factory=dict[str, Any])
    runner: SubprocessRunner = field(default_factory=SubprocessRunner)

    def read(self, path: str) -> str | None:
        text = super().read(path)
        if text is not None:
            self.data.setdefault("files", {})[path] = text
        return text

    def realpath(self, path: str) -> str:
        real = super().realpath(path)
        if real != path:
            self.data.setdefault("links", {})[path] = real
        return real

    def listdir(self, path: str) -> list[str]:
        names = super().listdir(path)
        self.data.setdefault("dirs", {})[path] = names
        return names

    def __call__(self, args: list[str], timeout: float = TIMEOUT_S) -> Output:
        out = self.runner(args, timeout)
        self.data.setdefault("commands", []).append({"args": args, "code": out.code, "stdout": out.stdout})
        return out


def record(os_name: str) -> dict[str, Any]:
    """Run this machine's probe, keeping every answer, then replace private details (CONTRIBUTING "Tests")."""
    rec = _Recorder(data={"os": os_name, "hostname": socket.gethostname()})
    parts = psutil_partitions()
    rec.data["partitions"] = [[p.device, p.mount, p.filesystem, p.options] for p in parts]
    rec.data["usage"] = {}
    for p in parts:
        try:
            rec.data["usage"][p.mount] = list(disk_usage(p.mount))
        except OSError:
            continue
    probe = _live_probe(os_name, rec)
    drives = probe.drives()
    probe.machine()
    probe.volumes()
    return redact(rec.data, [d.serial for d in drives if d.serial])


def _live_probe(os_name: str, rec: _Recorder) -> Probe:
    if os_name == "linux":
        from hone_drive.probe_linux import LinuxProbe  # noqa: PLC0415

        return LinuxProbe(run=rec, files=rec)
    if os_name == "macos":
        from hone_drive.probe_macos import MacProbe  # noqa: PLC0415

        return MacProbe(run=rec)
    from hone_drive.probe_windows import WindowsProbe  # noqa: PLC0415

    return WindowsProbe(run=rec)


def redact(data: dict[str, Any], serials: list[str]) -> dict[str, Any]:
    files = cast(dict[str, str], data.get("files", {}))
    if "/proc/cpuinfo" in files:
        files["/proc/cpuinfo"] = files["/proc/cpuinfo"].split("\n\n")[0] + "\n"  # one processor is enough
    text = json.dumps(data)
    for index, serial in enumerate(serials, 1):
        text = text.replace(serial, f"SERIAL{index}")
    text = text.replace(str(data.get("hostname", "\0")), "host")
    home = str(Path.home())
    user = Path(home).name
    text = text.replace(json.dumps(home)[1:-1], "/home/user").replace(f"/{user}/", "/user/")
    text = re.sub(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        "00000000-0000-0000-0000-000000000000",
        text,
    )
    return cast(dict[str, Any], json.loads(text))


# -- the contract ------------------------------------------------------------------------------------------


def check_probe(probe: Probe) -> None:
    """Assert the promises of design §3.1 and §2: every unknown fact has a reason; volumes add up."""
    machine = probe.machine()
    assert machine.os in ("linux", "macos", "windows")
    drives = probe.drives()
    assert len({d.id for d in drives}) == len(drives), "drive ids must be unique"
    for drive in drives:
        for key in ("model", "size_bytes"):
            assert getattr(drive, key) is not None or key in drive.unknown, (drive.id, key)
        assert (
            drive.link.negotiated_gbps is not None
            or drive.link.name
            or "negotiated_gbps" in drive.link.unknown
        )
        _check_link(drive.link)
    ids = {d.id for d in drives}
    for volume in probe.volumes():
        assert volume.used_bytes + volume.free_bytes <= volume.total_bytes * 1.0001, volume.mount
        assert volume.drive_id in ids or "drive_id" in volume.unknown, volume.mount
    assert isinstance(probe.commands(), list)


def _check_link(link: Link) -> None:
    if link.limited:
        assert link.negotiated_gbps is not None and link.port_max_gbps is not None


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        sys.stderr.write("usage: python -m hone_drive.testing OUT.json\n")
        return 2
    from hone_drive.scanner import os_name  # noqa: PLC0415

    data = record(os_name())
    Path(args[0]).write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    sys.stdout.write(
        f"recorded {len(data.get('files', {}))} files and {len(data.get('commands', []))} commands "
        f"to {args[0]}; check it for anything private before sharing it\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
