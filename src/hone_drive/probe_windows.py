"""Windows facts from one PowerShell call to the storage and CIM cmdlets (design §3.2). Read only; no
administrator rights. Windows' standard tools don't report USB or Thunderbolt link speed (design §16)."""

from __future__ import annotations

import json
import platform
import socket
from dataclasses import dataclass, field
from typing import Any, cast

from hone_drive.links import unknown_link
from hone_drive.model import Bus, Drive, DriveKind, Gpu, MachineFacts, ProbeCommand, Volume
from hone_drive.system import Runner, SubprocessRunner
from hone_drive.volumes import Partitions, Usage, disk_usage, list_volumes, psutil_partitions

Json = dict[str, Any]
SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$o = [ordered]@{}
$o.cpu = (Get-CimInstance Win32_Processor | Select-Object -First 1).Name
$o.ram = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory
$o.os = (Get-CimInstance Win32_OperatingSystem).Caption
$o.gpus = @(Get-CimInstance Win32_VideoController | Select-Object Name, AdapterRAM)
$o.disks = @(Get-PhysicalDisk | Select-Object DeviceId, FriendlyName, SerialNumber, Size,
  @{n='BusType';e={"$($_.BusType)"}}, @{n='MediaType';e={"$($_.MediaType)"}})
$o.removable = @(Get-Disk | Select-Object Number, @{n='Removable';e={$_.BusType -in 'USB','SD','MMC'}})
$letter = @{n='Letter';e={"$($_.DriveLetter)"}}
$o.partitions = @(Get-Partition | Where-Object DriveLetter | Select-Object $letter, DiskNumber)
$o.volumes = @(Get-Volume | Where-Object DriveLetter | Select-Object $letter, FileSystemLabel, UniqueId)
$o | ConvertTo-Json -Depth 4 -Compress
"""
BUS: dict[str, Bus] = {
    "NVMe": "pcie",
    "SATA": "sata",
    "USB": "usb",
    "SD": "mmc",
    "MMC": "mmc",
    "Virtual": "virtual",
    "File Backed Virtual": "virtual",
    "RAID": "other",
    "SAS": "other",
    "SCSI": "other",
}
NO_LINK = "Windows' standard tools do not report the link speed"


def _list(value: Any) -> list[Json]:
    """ConvertTo-Json writes a one-element array as a plain object."""
    if value is None:
        return []
    values = cast(list[Any], value) if isinstance(value, list) else [value]
    return [cast(Json, v) for v in values if isinstance(v, dict)]


@dataclass
class WindowsProbe:
    run: Runner = field(default_factory=SubprocessRunner)
    partitions: Partitions = psutil_partitions
    usage: Usage = disk_usage
    hostname: str = field(default_factory=socket.gethostname)
    _data: Json | None = None

    def commands(self) -> list[ProbeCommand]:
        return list(self.run.log) if isinstance(self.run, SubprocessRunner) else []

    def _facts(self) -> Json:
        if self._data is None:
            out = self.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", SCRIPT], 60.0)
            try:
                self._data = cast(Json, json.loads(out.stdout)) if out.ok and out.stdout.strip() else {}
            except ValueError:
                self._data = {}
        return self._data

    def machine(self) -> MachineFacts:
        data = self._facts()
        ram = data.get("ram")
        facts = MachineFacts(
            os="windows",
            os_version=str(data.get("os") or f"Windows {platform.release()}"),
            hostname=self.hostname,
            cpu=cast(str | None, data.get("cpu")),
            ram_bytes=int(ram) if ram else None,
        )
        smi = self.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
        nvidia = [line.rpartition(",") for line in smi.stdout.strip().splitlines()] if smi.ok else []
        facts.gpus = [Gpu(name=n.strip(), memory_bytes=int(float(m)) * 1024 * 1024) for n, _, m in nvidia]
        for gpu in _list(data.get("gpus")):
            name = str(gpu.get("Name") or "GPU")
            if not any(name == g.name for g in facts.gpus):
                facts.gpus.append(
                    Gpu(name=name, unknown={"memory_bytes": "Windows reports at most 4 GB here"})
                )
        return facts

    def drives(self) -> list[Drive]:
        data = self._facts()
        removable = {str(d.get("Number")): bool(d.get("Removable")) for d in _list(data.get("removable"))}
        drives: list[Drive] = []
        for disk in _list(data.get("disks")):
            bus = BUS.get(str(disk.get("BusType")), "other")
            media = str(disk.get("MediaType") or "")
            disk_id = str(disk.get("DeviceId"))
            drive = Drive(
                id=disk_id,
                model=cast(str | None, disk.get("FriendlyName")),
                serial=(str(disk.get("SerialNumber") or "").strip() or None),
                size_bytes=cast(int | None, disk.get("Size")),
                kind=_kind(bus, media),
                external=bus == "usb" or removable.get(disk_id, False),
                rotational=None if media not in ("HDD", "SSD") else media == "HDD",
                link=unknown_link(bus, NO_LINK),
            )
            drives.append(drive)
        return drives

    def volumes(self) -> list[Volume]:
        data = self._facts()
        disk_of = {
            str(p.get("Letter")).upper(): str(p.get("DiskNumber")) for p in _list(data.get("partitions"))
        }
        labels = {str(v.get("Letter")).upper(): v for v in _list(data.get("volumes"))}
        volumes = list_volumes("windows", self.partitions, self.usage)
        for volume in volumes:
            letter = volume.mount[:1].upper()
            volume.drive_id = disk_of.get(letter)
            info = labels.get(letter, {})
            volume.label = cast(str | None, info.get("FileSystemLabel")) or None
            volume.uuid = cast(str | None, info.get("UniqueId"))
            volume.unknown["at_boot"] = "Windows mounts drives by letter when they are connected"
            if volume.drive_id is None:
                volume.unknown["drive_id"] = "Get-Partition did not list this drive letter"
        return volumes


def _kind(bus: Bus, media: str) -> DriveKind:
    if bus == "pcie":
        return "nvme"
    if bus == "usb":
        return "usb-hdd" if media == "HDD" else "usb-ssd"
    if bus == "sata":
        return "hdd" if media == "HDD" else "sata-ssd"
    if bus == "mmc":
        return "sd-card"
    return "virtual" if bus == "virtual" else "other"
