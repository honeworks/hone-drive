"""Links: names, expected throughput and whether a link is limited (design §4.1, §4.2, decision D-004)."""

from __future__ import annotations

from hone_drive.model import Bus, Link

# Usable throughput in GB/s (10^9 bytes/s) after protocol overhead, by USB signalling rate in Gbps.
USB_USABLE_GBS = {0.48: 0.04, 5.0: 0.45, 10.0: 1.05, 20.0: 2.0, 40.0: 3.8, 80.0: 7.0}
USB_NAMES = {
    0.48: "USB 2.0 (480 Mbps)",
    5.0: "USB 3.2 Gen 1 (5 Gbps)",
    10.0: "USB 3.2 Gen 2 (10 Gbps)",
    20.0: "USB 3.2 Gen 2x2 (20 Gbps)",
    40.0: "USB4 (40 Gbps)",
    80.0: "USB4 v2 (80 Gbps)",
}
# PCIe: GT/s per lane -> (generation, usable GB/s per lane).
PCIE = {2.5: (1, 0.25), 5.0: (2, 0.5), 8.0: (3, 0.985), 16.0: (4, 1.969), 32.0: (5, 3.938), 64.0: (6, 7.563)}
SATA_USABLE_GBS = {1.5: 0.15, 3.0: 0.28, 6.0: 0.55}
LINK_BOUND_WITHIN = 0.15  # a measured read within 15% of the link's figure is link-bound (§4.2)


SPEED_FACTS = ("negotiated_gbps", "port_max_gbps", "limited")


def unknown_link(bus: Bus, reason: str, name: str | None = None) -> Link:
    """A link whose speeds the OS can't tell; every speed fact gets the reason (design §2, AC2)."""
    return Link(bus=bus, name=name, unknown=dict.fromkeys(SPEED_FACTS, reason))


def usb_link(gbps: float | None, lanes: int | None, port_gbps: float | None, port_lanes: int | None) -> Link:
    """A USB link from the negotiated and the port's signalling rates (sysfs reports 20000 for Gen 2x2)."""
    link = Link(
        bus="usb",
        negotiated_gbps=gbps,
        negotiated_lanes=lanes,
        port_max_gbps=port_gbps,
        port_max_lanes=port_lanes,
    )
    if gbps is None:
        return unknown_link("usb", "the USB device did not report its speed")
    link.name = USB_NAMES.get(gbps, f"USB ({gbps:g} Gbps)")
    link.expected_gbs = USB_USABLE_GBS.get(gbps)
    return mark_limited(link)


def pcie_link(gts: float | None, width: int | None, max_gts: float | None, max_width: int | None) -> Link:
    """A PCIe link from the negotiated and the maximum transfer rate (GT/s) and width."""
    link = Link(bus="pcie", negotiated_lanes=width, port_max_lanes=max_width)
    if gts is None or width is None or gts not in PCIE:
        return unknown_link("pcie", "the PCIe link speed was not reported")
    gen, per_lane = PCIE[gts]
    link.name = f"PCIe {gen}.0 x{width}"
    link.negotiated_gbps = round(per_lane * 8 * width, 1)
    link.expected_gbs = round(per_lane * width * 0.9, 2)
    if max_gts in PCIE and max_width:
        link.port_max_gbps = round(PCIE[max_gts][1] * 8 * max_width, 1)
        link.device_max_gbps = link.port_max_gbps  # sysfs max_link_* is what both ends support
        link.limited = (max_gts, max_width) != (gts, width)
    else:
        link.unknown["port_max_gbps"] = link.unknown["limited"] = "the maximum PCIe link was not reported"
    return link


def sata_link(gbps: float | None) -> Link:
    if gbps is None:
        return unknown_link("sata", "the SATA link speed was not reported")
    link = Link(bus="sata", negotiated_gbps=gbps, name=f"SATA {gbps:g} Gbps")
    link.unknown["port_max_gbps"] = link.unknown["limited"] = "SATA ports' maximum is not read in 0.1"
    link.expected_gbs = SATA_USABLE_GBS.get(gbps)
    return link


def mark_limited(link: Link) -> Link:
    """Limited: negotiated below the port's maximum, and the device isn't known to be the cap."""
    if link.negotiated_gbps is None or link.port_max_gbps is None:
        link.unknown.setdefault("port_max_gbps", "the port's maximum speed is unknown")
        link.unknown.setdefault("limited", "the port's maximum speed is unknown")
        return link
    port_effective = link.port_max_gbps
    below_port = link.negotiated_gbps < port_effective
    if link.device_max_gbps is not None:
        link.limited = below_port and link.negotiated_gbps < link.device_max_gbps
    elif below_port:
        link.limited = True
        link.unknown["device_max_gbps"] = (
            "the drive does not report its maximum; the cable or the drive may be the cap"
        )
    else:
        link.limited = False
    return link


def bound_label(measured_gbs: float, link: Link) -> str | None:
    """`link-bound` when a read reaches the link's usable figure, `drive-bound` when well below it."""
    if link.expected_gbs is None:
        return None
    return "link-bound" if measured_gbs >= link.expected_gbs * (1 - LINK_BOUND_WITHIN) else "drive-bound"


def describe(link: Link) -> str:
    """ "10 of 20 Gbps, 1 of 2 lanes" for a limited link; the link's name otherwise."""
    if link.limited and link.negotiated_gbps is not None and link.port_max_gbps is not None:
        text = f"{link.negotiated_gbps:g} of {link.port_max_gbps:g} Gbps"
        if link.negotiated_lanes and link.port_max_lanes and link.port_max_lanes > link.negotiated_lanes:
            text += f", {link.negotiated_lanes} of {link.port_max_lanes} lanes"
        return text
    return link.name or link.bus
