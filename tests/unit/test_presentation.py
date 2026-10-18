from __future__ import annotations

from hone_drive import presentation as p
from hone_drive.advice import advise
from hone_drive.links import unknown_link
from hone_drive.scanner import scan
from hone_drive.testing import reference_probe
from hone_drive.views import run_view
from tests.unit.test_advice import reference_run


def test_names_people_recognise() -> None:
    run = reference_run()
    assert p.drive_names(run) == {"nvme0n1": "Internal drive", "sda": "PNY RP60 PSSD"}
    assert p.volume_names(run)["/"] == "Internal drive"
    second = run.drives[0].model_copy(update={"id": "nvme1n1"})
    run.drives.append(second)
    assert p.drive_names(run)["nvme1n1"] == "Internal drive 2"


def test_tones_and_connections() -> None:
    assert p.space_tone(0.5) == ("good", "plenty of room")
    assert p.space_tone(0.8)[0] == "warn"
    assert p.space_tone(0.9) == ("bad", "almost full")
    assert p.space_tone(0.97) == ("bad", "full")
    run = reference_run()
    assert p.connection(run.drives[1])["title"] == "Not at full speed"
    assert p.connection(run.drives[0])["tone"] == "good"
    run.drives[0].link = unknown_link("usb", "Windows' standard tools do not report the link speed")
    assert p.connection(run.drives[0])["detail"].startswith("Windows' standard tools")


def test_headlines() -> None:
    run = reference_run()
    the_plan = p.plan(run, advise(run, "/home/user"))
    assert p.headline(run, the_plan)["tone"] == "bad"
    run.volumes[0].used_bytes = run.volumes[0].total_bytes // 2
    assert p.headline(run, p.plan(run, []))["action"] == "drives"
    run.drives[1].link.limited = False
    assert p.headline(run, p.plan(run, []))["title"] == "Your drives look healthy"


def test_plan_groups_advice_into_steps() -> None:
    run = reference_run()
    the_plan = p.plan(run, advise(run, "/home/user"))
    assert [f["rule"] for f in the_plan["findings"]] == ["A01", "A04"]
    steps = {s["key"]: s for s in the_plan["steps"]}
    assert steps["prepare"]["title"] == "Prepare PNY RP60 PSSD for models"
    assert {t["rule"] for t in steps["clean"]["tasks"]} == {"A07"}
    assert the_plan["gain"] == steps["clean"]["gain"] + steps["move"]["gain"]
    assert all(t["commands"] for t in steps["move"]["tasks"] if t["rule"] == "A02")


def test_view_without_measurements_or_external_drive() -> None:
    run = scan(probe=reference_probe(), finders={})
    run.drives = run.drives[:1]
    run.volumes = run.volumes[:1]
    run.volumes[0].system = True  # the reference machine is Linux; on Windows its "/" is not this OS's root
    view = run_view(run, "/home/user")
    assert view["drives"][0]["speed"] is None and view["drives"][0]["test_folder"] == "~"
    assert view["models"]["rows"] == [] and view["biggest"] == []
    assert view["volumes"][0]["segments"][-1]["kind"] == "other"
