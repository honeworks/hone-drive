from __future__ import annotations

from hone_drive.compare import compare
from hone_drive.model import Item, Measurement, Store
from hone_drive.report import markdown, redacted, report
from hone_drive.scanner import scan
from hone_drive.testing import reference_probe
from tests.unit.test_advice import reference_run


def test_compare_finds_links_volumes_and_speeds() -> None:
    a = reference_run()
    b = reference_run()
    b.drives[1].link.negotiated_gbps = 20.0
    b.drives[1].link.negotiated_lanes = 2
    b.drives[1].link.limited = False
    b.volumes[0].used_bytes -= 400_000_000_000
    b.measurements[0] = b.measurements[0].model_copy(update={"gbs": 3.0})
    b.measurements[2] = b.measurements[2].model_copy(update={"gbs": 0.99})  # within the spread
    changes = {(c.kind, c.subject): c for c in compare(a, b)}
    assert changes[("link", "PNY RP60 PSSD")].before == "10 of 20 Gbps, 1 of 2 lanes"
    assert changes[("volume", "/")].note.startswith("-400 GB")
    assert ("speed", "seq-read@/") in changes
    assert ("speed", "seq-read@/run/media/user/PNYRP60PSSD") not in changes


def test_compare_drives_added_and_removed() -> None:
    a = scan(probe=reference_probe(), finders={})
    b = scan(probe=reference_probe(), finders={})
    b.drives = b.drives[:1]
    assert [c.note for c in compare(a, b)] == ["removed"]
    assert [c.note for c in compare(b, a)] == ["added"]


def test_report_removes_private_details() -> None:
    run = reference_run()
    run.machine.hostname = "deadman"
    run.drives[0].serial = "24206T400124"
    run.volumes[0].uuid = "97876789-fede-40b1-8b50-e88abc490d09"
    run.stores.append(
        Store(
            id="custom",
            title="Folder mine",
            path="/home/bahman/secret-project",
            items=[Item(id="x", names=["x"], path="/home/bahman/secret-project/x")],
        )
    )
    run.stores[1].path = "/home/bahman/ComfyUI/models"
    for fmt in ("md", "json"):
        text = report(run, fmt, home="/home/bahman")
        for private in ("deadman", "24206T400124", "97876789", "bahman", "secret-project"):
            assert private not in text, (fmt, private)
    assert "~/ComfyUI/models" in report(run, "json", home="/home/bahman")
    assert "24206T400124" in report(run, "json", keep_private=True, home="/home/bahman")


def test_markdown_sections() -> None:
    run = reference_run()
    run.measurements.append(
        Measurement(
            id="x", test="random-read", volume="/", bytes=1, seconds=1, gbs=0.3, cache="direct", iops=7e4
        )
    )
    text = markdown(redacted(run, "/home/user"))
    for heading in ("## Drives", "## Volumes", "## Measurements", "## Stores"):
        assert heading in text
    assert "(limited)" in text
