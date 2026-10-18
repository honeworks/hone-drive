"""LinuxProbe against the recorded reference laptop (tests/fixtures/linux)."""

from __future__ import annotations

from hone_drive.probe_linux import LinuxProbe
from hone_drive.system import Output
from hone_drive.testing import RecordedSystem, recorded_probe
from tests.conftest import fixture


def probe(name: str = "laptop-nvme-usb.json") -> LinuxProbe:
    result = recorded_probe(fixture(f"linux/{name}"))
    assert isinstance(result, LinuxProbe)
    return result


def test_machine_facts() -> None:
    machine = probe().machine()
    assert machine.cpu == "Intel(R) Core(TM) i9-14900HX"
    assert machine.ram_bytes and machine.ram_bytes > 30 * 1024**3
    assert machine.os_version.startswith("EndeavourOS (kernel 6.18")
    assert machine.gpus[0].name == "NVIDIA GeForce RTX 4060 Laptop GPU"
    assert machine.gpus[0].memory_bytes == 8188 * 1024 * 1024
    assert [g.name for g in machine.gpus[1:]] == ["Intel GPU"]


def test_nvme_drive_and_link() -> None:
    nvme = next(d for d in probe().drives() if d.id == "nvme0n1")
    assert nvme.kind == "nvme" and not nvme.external
    assert nvme.model == "WD PC SN560 SDDPNQE-1T00-1102"
    assert nvme.serial == "SERIAL1"
    assert nvme.link.name == "PCIe 4.0 x4" and nvme.link.limited is False


def test_usb_drive_is_external_and_limited() -> None:
    usb = next(d for d in probe().drives() if d.id == "sda")
    assert usb.kind == "usb-ssd" and usb.external
    assert usb.model == "PNY RP60 PSSD"
    assert usb.link.negotiated_gbps == 10.0 and usb.link.negotiated_lanes == 1
    assert usb.link.port_max_gbps == 20.0 and usb.link.port_max_lanes == 2
    assert usb.link.limited is True


def test_volumes_map_to_drives_with_labels() -> None:
    volumes = {v.mount: v for v in probe().volumes()}
    assert volumes["/"].drive_id == "nvme0n1" and volumes["/"].label == "endeavouros"
    assert volumes["/run/media/user/PNYRP60PSSD"].drive_id == "sda"
    assert volumes["/run/media/user/PNYRP60PSSD"].traits.posix_permissions is False  # type: ignore[union-attr]


def test_missing_facts_have_reasons() -> None:
    data = fixture("linux/laptop-nvme.json")
    data["files"] = {k: v for k, v in data["files"].items() if "cpuinfo" not in k and "meminfo" not in k}
    data["commands"] = []
    machine = recorded_probe(data).machine()
    assert machine.cpu is None and "cpu" in machine.unknown
    assert "ram_bytes" in machine.unknown
    assert all(g.name != "NVIDIA GeForce RTX 4060 Laptop GPU" for g in machine.gpus)


def test_other_buses() -> None:
    system = RecordedSystem(
        {
            "dirs": {"/sys/block": ["sdb", "mmcblk0", "vda", "loop0", "xyz"]},
            "links": {
                "/sys/block/sdb": "/sys/devices/pci0000:00/0000:00:17.0/ata3/host2/target2:0:0/2:0:0:0/block/sdb",
                "/sys/block/mmcblk0": "/sys/devices/platform/mmc_host/mmc0/mmc0:0001/block/mmcblk0",
                "/sys/block/vda": "/sys/devices/pci0000:00/0000:00:04.0/virtio1/block/vda",
                "/sys/block/xyz": "/sys/devices/strange/xyz",
            },
            "files": {
                "/sys/class/ata_link/link3/sata_spd": "6.0 Gbps\n",
                "/sys/block/sdb/queue/rotational": "1\n",
            },
        }
    )
    drives = {d.id: d for d in LinuxProbe(run=system, files=system).drives()}
    assert set(drives) == {"sdb", "mmcblk0", "vda", "xyz"}
    assert drives["sdb"].kind == "hdd" and drives["sdb"].link.name == "SATA 6 Gbps"
    assert drives["mmcblk0"].kind == "sd-card"
    assert drives["vda"].kind == "virtual"
    assert drives["xyz"].kind == "other" and "negotiated_gbps" in drives["xyz"].link.unknown
    assert "model" in drives["xyz"].unknown


def test_thunderbolt_nvme_is_external() -> None:
    pci = "/sys/devices/pci0000:00/0000:00:07.0/0000:20:00.0/0000:21:01.0/0000:22:00.0"
    system = RecordedSystem(
        {
            "dirs": {"/sys/block": ["nvme1n1"]},
            "links": {"/sys/block/nvme1n1": f"{pci}/nvme/nvme1/nvme1n1"},
            "files": {
                f"{pci}/removable": "removable\n",
                f"{pci}/current_link_speed": "8.0 GT/s PCIe\n",
                f"{pci}/current_link_width": "4\n",
            },
        }
    )
    drive = LinuxProbe(run=system, files=system).drives()[0]
    assert drive.link.bus == "thunderbolt" and drive.external and drive.kind == "nvme"


def test_encrypted_volume_follows_to_its_disk() -> None:
    system = RecordedSystem(
        {
            "dirs": {"/sys/block": ["nvme0n1"], "/sys/class/block/dm-0/slaves": ["nvme0n1p3"]},
            "links": {
                "/dev/mapper/root": "/dev/dm-0",
                "/sys/block/nvme0n1": "/sys/devices/x/nvme/nvme0/nvme0n1",
                "/sys/class/block/nvme0n1p3": "/sys/devices/x/nvme/nvme0/nvme0n1/nvme0n1p3",
            },
            "files": {"/sys/class/block/nvme0n1p3/partition": "3\n"},
            "partitions": [["/dev/mapper/root", "/", "btrfs", "rw"], ["nfs:/x", "/net", "nfs", "rw"]],
            "usage": {"/": [100, 40, 50], "/net": [10, 1, 9]},
        }
    )
    volumes = {
        v.mount: v
        for v in LinuxProbe(
            run=system, files=system, partitions=system.partitions, usage=system.usage
        ).volumes()
    }
    assert volumes["/"].drive_id == "nvme0n1"
    assert volumes["/net"].drive_id is None and "drive_id" in volumes["/net"].unknown


def test_commands_are_logged_only_by_the_real_runner() -> None:
    assert probe().commands() == []
    assert RecordedSystem({})(["x"]) == Output(None, "", "x is not installed")
