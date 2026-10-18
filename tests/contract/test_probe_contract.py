"""Every probe and the fake keep the promises of design §3.1 (hone_drive.testing.check_probe)."""

from __future__ import annotations

import pytest

from hone_drive.ports import Probe
from hone_drive.testing import check_probe, recorded_probe, reference_probe
from tests.conftest import fixture


@pytest.mark.parametrize(
    "name",
    ["linux/laptop-nvme.json", "linux/laptop-nvme-usb.json", "macos/m3-usb.json", "windows/desktop-usb.json"],
)
def test_recorded_probes(name: str) -> None:
    probe = recorded_probe(fixture(name))
    assert isinstance(probe, Probe)
    check_probe(probe)


def test_fake_probe() -> None:
    check_probe(reference_probe())
