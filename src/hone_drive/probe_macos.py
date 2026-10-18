"""macOS facts from `diskutil`, `system_profiler` and `sysctl` (design §3.2). Read only; never as root."""

from __future__ import annotations

import json
import plistlib
import re
import socket
from dataclasses import dataclass, field
from typing import Any, cast

from hone_drive.links import pcie_link, unknown_link, usb_link
from hone_drive.model import Drive, DriveKind, Gpu, Link, MachineFacts, ProbeCommand, Volume
from hone_drive.system import Runner, SubprocessRunner
from hone_drive.volumes import Partitions, Usage, disk_usage, list_volumes, psutil_partitions

Json = dict[str, Any]
# system_profiler's USB speed names (before macOS 15) in Gbps.
USB_SPEEDS = {
    "low_speed": 0.0015,
    "full_speed": 0.012,
    "high_speed": 0.48,
    "super_speed": 5.0,
    "super_speed_plus": 10.0,
    "super_speed_plus_by2": 20.0,
}
SPEED_TEXT = re.compile(r"([\d.]+)\s*([GM])b/s")


@dataclass
class MacProbe:
    run: Runner = field(default_factory=SubprocessRunner)
    partitions: Partitions = psutil_partitions
    usage: Usage = disk_usage
    hostname: str = field(default_factory=socket.gethostname)
    _profiles: dict[str, Json] = field(default_factory=dict[str, Json])

    def commands(self) -> list[ProbeCommand]:
        return list(self.run.log) if isinstance(self.run, SubprocessRunner) else []

    def _plist(self, args: list[str]) -> Json:
        out = self.run(args)
        try:
            return cast(Json, plistlib.loads(out.stdout.encode())) if out.ok else {}
        except (plistlib.InvalidFileException, ValueError):
            return {}

    def _profile(self, data_type: str) -> list[Json]:
        if data_type not in self._profiles:
            out = self.run(["system_profiler", data_type, "-json"], 30.0)
            try:
                self._profiles[data_type] = cast(Json, json.loads(out.stdout)) if out.ok else {}
            except ValueError:
                self._profiles[data_type] = {}
        return cast(list[Json], self._profiles[data_type].get(data_type, []))

    def machine(self) -> MachineFacts:
        version = self.run(["sw_vers", "-productVersion"]).stdout.strip() or "?"
        cpu = self.run(["sysctl", "-n", "machdep.cpu.brand_string"]).stdout.strip() or None
        mem = self.run(["sysctl", "-n", "hw.memsize"]).stdout.strip()
        facts = MachineFacts(
            os="macos",
            os_version=f"macOS {version}",
            hostname=self.hostname,
            cpu=cpu,
            ram_bytes=int(mem) if mem.isdigit() else None,
        )
        for gpu in self._profile("SPDisplaysDataType"):
            vram = str(gpu.get("spdisplays_vram") or gpu.get("_spdisplays_vram") or "")
            match = re.match(r"(\d+)\s*([GM])B", vram)
            memory = int(match.group(1)) * (1024**3 if match.group(2) == "G" else 1024**2) if match else None
            entry = Gpu(name=str(gpu.get("sppci_model") or gpu.get("_name") or "GPU"), memory_bytes=memory)
            if memory is None:
                entry.unknown["memory_bytes"] = "unified memory: the GPU shares the machine's RAM"
            facts.gpus.append(entry)
        return facts

    def drives(self) -> list[Drive]:
        listing = self._plist(["diskutil", "list", "-plist", "physical"])
        return [self._drive(str(disk)) for disk in cast(list[Any], listing.get("WholeDisks", []))]

    def _drive(self, disk: str) -> Drive:
        info = self._plist(["diskutil", "info", "-plist", disk])
        protocol = str(info.get("BusProtocol") or info.get("Protocol") or "")
        solid = info.get("SolidState")
        link = self._link(disk, protocol)
        internal = bool(info.get("Internal", False))
        drive = Drive(
            id=disk,
            model=str(info.get("MediaName") or info.get("IORegistryEntryName") or "") or None,
            size_bytes=cast(int | None, info.get("TotalSize") or info.get("Size")),
            kind=_kind(protocol, solid),
            external=not internal or link.bus in ("usb", "thunderbolt"),
            rotational=None if solid is None else not solid,
            link=link,
        )
        drive.unknown["serial"] = "diskutil does not report serial numbers"
        return drive

    def _link(self, disk: str, protocol: str) -> Link:
        if protocol == "USB":
            return self._usb(disk)
        if protocol in ("PCI-Express", "PCI"):
            for item in self._nvme_items():
                if item.get("bsd_name") == disk:
                    gts = re.match(r"([\d.]+)", str(item.get("spnvme_linkspeed", "")))
                    width = re.search(r"(\d+)", str(item.get("spnvme_linkwidth", "")))
                    return pcie_link(
                        float(gts.group(1)) if gts else None,
                        int(width.group(1)) if width else None,
                        None,
                        None,
                    )
            return unknown_link("pcie", "system_profiler did not list this drive")
        if protocol == "Thunderbolt":
            return unknown_link("thunderbolt", "not read for Thunderbolt in 0.1")
        if protocol == "Apple Fabric":
            return unknown_link(
                "pcie",
                "Apple silicon's internal storage has no external link",
                "Apple silicon internal storage",
            )
        return unknown_link(
            "sata" if protocol == "SATA" else "other", f"not read for {protocol or 'this bus'}"
        )

    def _nvme_items(self) -> list[Json]:
        return [
            cast(Json, i)
            for c in self._profile("SPNVMeDataType")
            for i in cast(list[Any], c.get("_items", []))
        ]

    def _usb(self, disk: str) -> Link:
        for data_type in ("SPUSBHostDataType", "SPUSBDataType"):
            device = _find_media(cast(list[Any], self._profile(data_type)), disk)
            if device is not None:
                gbps = _usb_speed(device)
                link = usb_link(gbps, None, None, None)
                link.unknown["port_max_gbps"] = "macOS does not report the port's maximum speed"
                return link
        return unknown_link("usb", "system_profiler did not list this drive")

    def volumes(self) -> list[Volume]:
        volumes = list_volumes("macos", self.partitions, self.usage)
        for volume in volumes:
            info = self._plist(["diskutil", "info", "-plist", volume.device])
            stores = cast(list[Json], info.get("APFSPhysicalStores") or [])
            parent = (
                str(stores[0].get("APFSPhysicalStore", ""))
                if stores
                else str(info.get("ParentWholeDisk", ""))
            )
            volume.drive_id = re.sub(r"s\d+$", "", parent) or None
            volume.label = cast(str | None, info.get("VolumeName"))
            volume.uuid = cast(str | None, info.get("VolumeUUID"))
            volume.unknown["at_boot"] = "macOS mounts external volumes when a user logs in"
            if volume.drive_id is None:
                volume.unknown["drive_id"] = "diskutil did not name the disk below this volume"
        return volumes


def _kind(protocol: str, solid: object) -> DriveKind:
    if protocol in ("PCI-Express", "PCI", "Apple Fabric", "Thunderbolt"):
        return "nvme"
    if protocol == "USB":
        return "usb-hdd" if solid is False else "usb-ssd"
    if protocol == "SATA":
        return "hdd" if solid is False else "sata-ssd"
    return "sd-card" if protocol == "Secure Digital" else "other"


def _find_media(nodes: list[Any], disk: str) -> Json | None:
    """The USB device entry whose media (at any depth) is `disk`."""
    for node in nodes:
        if not isinstance(node, dict):
            continue
        entry = cast(Json, node)
        if _has_bsd(cast(list[Any], entry.get("Media", [])), disk):
            return entry
        found = _find_media(cast(list[Any], entry.get("_items", [])), disk)
        if found is not None:
            return found
    return None


def _has_bsd(media: list[Any], disk: str) -> bool:
    return any(isinstance(m, dict) and cast(Json, m).get("bsd_name") == disk for m in media)


def _usb_speed(device: Json) -> float | None:
    for value in device.values():
        if isinstance(value, str):
            if value in USB_SPEEDS:
                return USB_SPEEDS[value]
            match = SPEED_TEXT.search(value)
            if match:
                return float(match.group(1)) / (1 if match.group(2) == "G" else 1000)
    return None
