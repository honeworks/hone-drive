"""WindowsProbe against a hand-written fixture (tests/fixtures/windows; see its note)."""

from __future__ import annotations

from hone_drive.probe_windows import WindowsProbe, _list
from hone_drive.testing import RecordedSystem, recorded_probe
from tests.conftest import fixture


def test_machine_drives_and_volumes() -> None:
    probe = recorded_probe(fixture("windows/desktop-usb.json"))
    assert isinstance(probe, WindowsProbe)
    machine = probe.machine()
    assert machine.cpu.startswith("AMD Ryzen 9")  # type: ignore[union-attr]
    assert [g.name for g in machine.gpus] == ["NVIDIA GeForce RTX 4090"]
    drives = {d.id: d for d in probe.drives()}
    assert drives["0"].kind == "nvme" and not drives["0"].external
    assert drives["1"].kind == "usb-ssd" and drives["1"].external
    assert "negotiated_gbps" in drives["1"].link.unknown
    volumes = {v.mount: v for v in probe.volumes()}
    assert set(volumes) == {"C:\\", "E:\\"}  # the empty optical drive is skipped
    assert volumes["E:\\"].drive_id == "1" and volumes["E:\\"].label == "PNYRP60PSSD"
    assert volumes["C:\\"].traits.posix_permissions is True  # type: ignore[union-attr]


def test_powershell_missing() -> None:
    probe = WindowsProbe(run=RecordedSystem({}), partitions=list, usage=lambda m: (0, 0, 0))
    assert probe.machine().os_version.startswith(("Windows", "Microsoft"))
    assert probe.drives() == []


def test_single_objects_become_lists() -> None:
    assert _list(None) == [] and _list({"a": 1}) == [{"a": 1}] and _list([{"a": 1}, 3]) == [{"a": 1}]
