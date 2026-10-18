"""MacProbe against a hand-written fixture (tests/fixtures/macos; see its note)."""

from __future__ import annotations

from hone_drive.probe_macos import MacProbe, _usb_speed
from hone_drive.testing import RecordedSystem, recorded_probe
from tests.conftest import fixture


def test_machine_drives_and_volumes() -> None:
    probe = recorded_probe(fixture("macos/m3-usb.json"))
    assert isinstance(probe, MacProbe)
    machine = probe.machine()
    assert machine.os_version == "macOS 15.1" and machine.cpu == "Apple M3 Pro"
    assert "memory_bytes" in machine.gpus[0].unknown
    drives = {d.id: d for d in probe.drives()}
    assert not drives["disk0"].external and "negotiated_gbps" in drives["disk0"].link.unknown
    assert drives["disk4"].external and drives["disk4"].link.negotiated_gbps == 10.0
    assert "port_max_gbps" in drives["disk4"].link.unknown
    volumes = {v.mount: v for v in probe.volumes()}
    assert set(volumes) == {"/", "/Volumes/PNYRP60PSSD"}  # /System/Volumes/* are parts of the system volume
    assert volumes["/"].drive_id == "disk0" and volumes["/"].label == "Macintosh HD"
    assert volumes["/Volumes/PNYRP60PSSD"].drive_id == "disk4"


def test_old_usb_data_type_and_nvme_link() -> None:
    data = fixture("macos/m3-usb.json")
    data["commands"] = [c for c in data["commands"] if "SPUSBHostDataType" not in c["args"]]
    data["commands"].append(
        {
            "args": ["system_profiler", "SPUSBDataType", "-json"],
            "code": 0,
            "stdout": '{"SPUSBDataType": [{"_items": [{"_name": "hub", "_items": [{"device_speed": "super_speed_plus_by2", '
            '"Media": [{"bsd_name": "disk4"}]}]}]}]}',
        }
    )
    for command in data["commands"]:
        if command["args"] == ["diskutil", "info", "-plist", "disk0"]:
            command["stdout"] = command["stdout"].replace("Apple Fabric", "PCI-Express")
        if command["args"][1] == "SPNVMeDataType":
            command["stdout"] = (
                '{"SPNVMeDataType": [{"_items": [{"bsd_name": "disk0", '
                '"spnvme_linkspeed": "8.0 GT/s", "spnvme_linkwidth": "x4"}]}]}'
            )
    drives = {d.id: d for d in recorded_probe(data).drives()}
    assert drives["disk4"].link.negotiated_gbps == 20.0
    assert drives["disk0"].link.name == "PCIe 3.0 x4"


def test_tools_that_fail_leave_reasons() -> None:
    probe = MacProbe(run=RecordedSystem({}), partitions=list, usage=lambda m: (0, 0, 0))
    assert probe.machine().ram_bytes is None
    assert probe.drives() == []
    assert probe.volumes() == []


def test_usb_speed_text() -> None:
    assert _usb_speed({"speed": "Up to 5 Gb/s"}) == 5.0
    assert _usb_speed({"speed": "480 Mb/s"}) == 0.48
    assert _usb_speed({"x": 1}) is None
