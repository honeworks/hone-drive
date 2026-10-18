"""Linux facts from sysfs, /proc and `nvidia-smi` (design §3.2). Read only; never as root."""

from __future__ import annotations

import platform
import re
import socket
from dataclasses import dataclass, field

from hone_drive.links import pcie_link, sata_link, unknown_link, usb_link
from hone_drive.model import Drive, DriveKind, Gpu, Link, MachineFacts, ProbeCommand, Volume
from hone_drive.system import Files, LocalFiles, Runner, SubprocessRunner, read_value
from hone_drive.volumes import Partitions, Usage, disk_usage, list_volumes, psutil_partitions

SKIP_BLOCK = ("loop", "ram", "zram", "sr", "nbd", "dm-", "md", "fd")
USB_DEVICE = re.compile(r"^\d+-[\d.]+$")
USB_ROOT = re.compile(r"^usb\d+$")
GPU_VENDORS = {"0x1002": "AMD GPU", "0x8086": "Intel GPU"}


@dataclass
class LinuxProbe:
    run: Runner = field(default_factory=SubprocessRunner)
    files: Files = field(default_factory=LocalFiles)
    partitions: Partitions = psutil_partitions
    usage: Usage = disk_usage
    hostname: str = field(default_factory=socket.gethostname)

    def commands(self) -> list[ProbeCommand]:
        return list(self.run.log) if isinstance(self.run, SubprocessRunner) else []

    # -- machine -------------------------------------------------------------------------------------

    def machine(self) -> MachineFacts:
        facts = MachineFacts(os="linux", os_version=self._os_version(), hostname=self.hostname)
        cpuinfo = self.files.read("/proc/cpuinfo") or ""
        cpu = re.search(r"^model name\s*:\s*(.+)$", cpuinfo, re.MULTILINE)
        facts.cpu = cpu.group(1).strip() if cpu else None
        mem = re.search(r"^MemTotal:\s*(\d+) kB", self.files.read("/proc/meminfo") or "", re.MULTILINE)
        facts.ram_bytes = int(mem.group(1)) * 1024 if mem else None
        for name, value in (("cpu", facts.cpu), ("ram_bytes", facts.ram_bytes)):
            if value is None:
                facts.unknown[name] = "/proc did not say"
        facts.gpus = self._gpus()
        return facts

    def _os_version(self) -> str:
        release = self.files.read("/etc/os-release") or ""
        pretty = re.search(r'^PRETTY_NAME="?([^"\n]+)"?', release, re.MULTILINE)
        kernel = read_value(self.files, "/proc/sys/kernel/osrelease") or platform.release()
        return f"{pretty.group(1) if pretty else 'Linux'} (kernel {kernel})"

    def _gpus(self) -> list[Gpu]:
        gpus: list[Gpu] = []
        out = self.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
        if out.ok:
            for line in out.stdout.strip().splitlines():
                name, _, mib = line.rpartition(",")
                gpus.append(Gpu(name=name.strip(), memory_bytes=int(float(mib)) * 1024 * 1024))
        for card in self.files.listdir("/sys/class/drm"):
            vendor = read_value(self.files, f"/sys/class/drm/{card}/device/vendor")
            if re.fullmatch(r"card\d+", card) and vendor in GPU_VENDORS:
                gpus.append(
                    Gpu(name=GPU_VENDORS[vendor], unknown={"memory_bytes": "only reported for NVIDIA"})
                )
        return gpus

    # -- drives --------------------------------------------------------------------------------------

    def drives(self) -> list[Drive]:
        names = [n for n in self.files.listdir("/sys/block") if not n.startswith(SKIP_BLOCK)]
        return [self._drive(name) for name in names]

    def _drive(self, name: str) -> Drive:
        base = f"/sys/block/{name}"
        path = self.files.realpath(base)
        sectors = read_value(self.files, f"{base}/size")
        rotational = read_value(self.files, f"{base}/queue/rotational")
        link = self._link(path)
        rot = rotational == "1" if rotational is not None else None
        drive = Drive(
            id=name,
            model=self._model(base, path),
            serial=self._serial(base, path),
            size_bytes=int(sectors) * 512 if sectors else None,
            kind=_kind(link, rot, name),
            external=link.bus in ("usb", "thunderbolt") or read_value(self.files, f"{base}/removable") == "1",
            rotational=rot,
            link=link,
        )
        for key in ("model", "serial", "size_bytes", "rotational"):
            if getattr(drive, key) is None:
                drive.unknown[key] = "sysfs did not say"
        return drive

    def _model(self, base: str, path: str) -> str | None:
        vendor = read_value(self.files, f"{base}/device/vendor") or ""
        model = read_value(self.files, f"{base}/device/model") or ""
        usb = _usb_dir(path)
        if usb and not model:
            model = read_value(self.files, f"{usb}/product") or ""
        text = f"{vendor} {model}".strip() if not vendor.startswith("0x") else model
        return " ".join(text.split()) or None

    def _serial(self, base: str, path: str) -> str | None:
        usb = _usb_dir(path)
        return read_value(self.files, f"{base}/device/serial") or (
            read_value(self.files, f"{usb}/serial") if usb else None
        )

    def _link(self, path: str) -> Link:
        if (usb := _usb_dir(path)) is not None:
            return self._usb(usb)
        if "/nvme/" in path:
            return self._pcie(path.split("/nvme/", maxsplit=1)[0])
        if (ata := re.search(r"/(ata\d+)/", path)) is not None:
            spd = read_value(self.files, f"/sys/class/ata_link/link{ata.group(1)[3:]}/sata_spd")
            return sata_link(float(spd.split()[0]) if spd and spd[0].isdigit() else None)
        if "/mmc_host/" in path:
            return unknown_link("mmc", "not read for SD and MMC cards")
        if "/virtio" in path or "/xen" in path or "/vmbus" in path:
            return unknown_link("virtual", "a virtual disk has no physical link")
        return unknown_link("other", "unrecognised device path " + path)

    def _usb(self, device: str) -> Link:
        root = device.rsplit("/", 1)[0]
        while root and not USB_ROOT.match(root.rsplit("/", 1)[-1]):
            root = root.rsplit("/", 1)[0]

        def num(path: str) -> float | None:
            value = read_value(self.files, path)
            return float(value) if value and value.replace(".", "", 1).isdigit() else None

        speed, port = num(f"{device}/speed"), num(f"{root}/speed") if root else None
        lanes, port_lanes = num(f"{device}/rx_lanes"), num(f"{root}/rx_lanes") if root else None
        return usb_link(
            speed / 1000 if speed else None,
            int(lanes) if lanes else None,
            port / 1000 if port else None,
            int(port_lanes) if port_lanes else None,
        )

    def _pcie(self, pci: str) -> Link:
        def gts(name: str) -> float | None:
            value = read_value(self.files, f"{pci}/{name}")
            match = re.match(r"([\d.]+) GT/s", value or "")
            return float(match.group(1)) if match else None

        def width(name: str) -> int | None:
            value = read_value(self.files, f"{pci}/{name}")
            return int(value) if value and value.isdigit() else None

        link = pcie_link(
            gts("current_link_speed"),
            width("current_link_width"),
            gts("max_link_speed"),
            width("max_link_width"),
        )
        if read_value(self.files, f"{pci}/removable") == "removable":
            link.bus = "thunderbolt"  # Linux marks PCIe devices behind Thunderbolt / USB4 as removable
        return link

    # -- volumes -------------------------------------------------------------------------------------

    def volumes(self) -> list[Volume]:
        volumes = list_volumes("linux", self.partitions, self.usage)
        labels = self._by("/dev/disk/by-label")
        uuids = self._by("/dev/disk/by-uuid")
        for volume in volumes:
            real = self.files.realpath(volume.device)
            volume.drive_id = self._disk_of(real.rsplit("/", 1)[-1])
            volume.label, volume.uuid = labels.get(real), uuids.get(real)
            if volume.drive_id is None:
                volume.unknown["drive_id"] = "not backed by a local block device"
        return volumes

    def _by(self, folder: str) -> dict[str, str]:
        return {
            self.files.realpath(f"{folder}/{n}"): n.replace("\\x20", " ") for n in self.files.listdir(folder)
        }

    def _disk_of(self, name: str, depth: int = 0) -> str | None:
        if not name or depth > 5:
            return None
        if read_value(self.files, f"/sys/class/block/{name}/partition") is not None:
            return self.files.realpath(f"/sys/class/block/{name}").rsplit("/", 2)[-2]
        slaves = self.files.listdir(f"/sys/class/block/{name}/slaves")
        if slaves:
            return self._disk_of(slaves[0], depth + 1)  # LUKS, LVM: follow to the partition below
        return name if self.files.realpath(f"/sys/block/{name}") != f"/sys/block/{name}" else None


def _usb_dir(path: str) -> str | None:
    parts = path.split("/")
    for index in range(len(parts) - 1, 0, -1):
        if USB_DEVICE.match(parts[index]):
            return "/".join(parts[: index + 1])
    return None


def _kind(link: Link, rotational: bool | None, name: str) -> DriveKind:
    if link.bus in ("pcie", "thunderbolt") or name.startswith("nvme"):
        return "nvme"
    if link.bus == "usb":
        return "usb-hdd" if rotational else "usb-ssd"
    if link.bus == "sata":
        return "hdd" if rotational else "sata-ssd"
    if link.bus == "mmc":
        return "sd-card"
    return "virtual" if link.bus == "virtual" else "other"
