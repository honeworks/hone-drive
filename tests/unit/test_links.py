from hone_drive import links


def test_usb_link_below_the_port_is_limited_with_lanes() -> None:
    link = links.usb_link(10.0, 1, 20.0, 2)
    assert link.limited is True
    assert link.name == "USB 3.2 Gen 2 (10 Gbps)"
    assert link.expected_gbs == 1.05
    assert links.describe(link) == "10 of 20 Gbps, 1 of 2 lanes"
    assert "device_max_gbps" in link.unknown


def test_usb_link_at_the_port_speed_is_not_limited() -> None:
    link = links.usb_link(10.0, 1, 10.0, 1)
    assert link.limited is False
    assert links.describe(link) == "USB 3.2 Gen 2 (10 Gbps)"


def test_usb_link_device_maximum_caps_it() -> None:
    link = links.usb_link(10.0, 1, 20.0, 2).model_copy(update={"device_max_gbps": 10.0, "limited": None})
    assert links.mark_limited(link).limited is False


def test_usb_link_without_speeds_says_why() -> None:
    assert "negotiated_gbps" in links.usb_link(None, None, None, None).unknown
    assert "limited" in links.usb_link(5.0, None, None, None).unknown


def test_pcie_link() -> None:
    link = links.pcie_link(16.0, 4, 16.0, 4)
    assert link.name == "PCIe 4.0 x4"
    assert link.limited is False
    assert links.pcie_link(8.0, 2, 16.0, 4).limited is True
    assert "negotiated_gbps" in links.pcie_link(None, None, None, None).unknown


def test_sata_link() -> None:
    assert links.sata_link(6.0).expected_gbs == 0.55
    assert "negotiated_gbps" in links.sata_link(None).unknown


def test_bound_label() -> None:
    usb = links.usb_link(10.0, 1, 20.0, 2)
    assert links.bound_label(0.98, usb) == "link-bound"
    assert links.bound_label(0.4, usb) == "drive-bound"
    assert links.bound_label(1.0, links.sata_link(None)) is None
