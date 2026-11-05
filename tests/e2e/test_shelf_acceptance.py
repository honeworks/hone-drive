"""The guarantees of change 0003 (design §15, AC17 to AC26), through the public API and the CLI."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from hone_drive import cli, cli_shelf, shelf
from hone_drive import shelf_catalog as catalog
from tests.shelf_machine import Machine, build

ROOT = Path(__file__).resolve().parents[2]
GEMMA, TINY = "ollama:gemma:31b", "ollama:tiny:latest"
WAN = "comfyui:diffusion_models/wan.safetensors"
KILL = """
import os, sys
from pathlib import Path
import pytest
from hone_drive import shelf
from tests.shelf_machine import attach
machine = attach(Path(sys.argv[1]), pytest.MonkeyPatch())
run = machine.run()
def die(tick):
    os._exit(9)  # killed mid-copy: no finally block runs
options = shelf.Options(on_tick=die)
if sys.argv[2] == "move":
    shelf.move(run, shelf.plan(run, sys.argv[3:], to=str(machine.drive)), options)
else:
    shelf.restore(run, sys.argv[3:], options=options)
"""


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Machine:
    return build(tmp_path, monkeypatch)


def wan(machine: Machine) -> Path:
    return machine.home / "ComfyUI" / "models" / "diffusion_models" / "wan.safetensors"


def moved(machine: Machine, *keys: str) -> shelf.Result:
    run = machine.run()
    return shelf.move(run, shelf.plan(run, list(keys), to=str(machine.drive)))


def test_ac17_nothing_leaves_the_computer_before_its_copy_is_proven(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hone_drive import shelf_copy

    real = shelf_copy.sha256_of

    def disagrees(path: str, direct: bool, *args: object, **kwargs: object) -> str:
        return "0" * 64 if direct else real(path, direct, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(shelf_copy, "sha256_of", disagrees)  # the drive's copy reads back wrong
    result = moved(machine, WAN)
    assert WAN in result.failed and wan(machine).exists()
    on_drive = catalog.load(str(machine.drive))
    assert on_drive is not None and not on_drive.models[WAN].complete
    monkeypatch.setattr(shelf_copy, "sha256_of", real)
    assert moved(machine, WAN).done == [WAN] and not wan(machine).exists()


def test_ac18_moving_twice_is_moving_once_and_bringing_back_keeps_the_drive(machine: Machine) -> None:
    assert moved(machine, WAN).done == [WAN]
    assert moved(machine, WAN).failed == {} and not wan(machine).exists()
    assert shelf.restore(machine.run(), [WAN]).done == [WAN]
    assert shelf.restore(machine.run(), [WAN]).done == [WAN] and wan(machine).exists()
    on_drive = catalog.load(str(machine.drive))
    assert on_drive is not None and WAN in on_drive.models
    the_run = machine.run()
    assert shelf.plan(the_run, [WAN], to=str(machine.drive)).models[0].copy_bytes == 0


@pytest.mark.parametrize("action", ["move", "restore"])
def test_ac19_a_killed_move_or_restore_leaves_every_model_whole(machine: Machine, action: str) -> None:
    if action == "restore":
        assert moved(machine, GEMMA).done == [GEMMA]
    done = subprocess.run(
        [sys.executable, "-c", KILL, str(machine.home.parent), action, GEMMA],
        cwd=ROOT, capture_output=True, text=True, timeout=120, check=False,
    )  # fmt: skip
    assert done.returncode == 9, done.stderr
    manifest = machine.models / "manifests" / "registry.ollama.ai" / "library" / "gemma" / "31b"
    on_drive = catalog.load(str(machine.drive))
    assert on_drive is not None
    if action == "move":
        assert manifest.exists() and not on_drive.models[GEMMA].complete  # still whole on the computer
        assert moved(machine, GEMMA).done == [GEMMA]
    else:
        assert not manifest.exists() and on_drive.models[GEMMA].complete  # still whole on the drive
        assert shelf.restore(machine.run(), [GEMMA]).done == [GEMMA] and manifest.exists()
    assert not list(machine.home.parent.rglob("*.part"))


def test_ac20_a_model_in_use_is_refused_and_nothing_changes(machine: Machine) -> None:
    machine.ollama.loaded = ["gemma:31b"]
    the_run = machine.run()
    the_plan = shelf.plan(the_run, [GEMMA], to=str(machine.drive))
    assert the_plan.models[0].refused and shelf.move(the_run, the_plan).done == []
    assert not (machine.drive / "hone-drive" / "files").exists()


def test_ac21_a_blob_another_model_lists_stays(machine: Machine) -> None:
    before = {p.name for p in (machine.models / "blobs").iterdir()}
    moved(machine, GEMMA)
    after = {p.name for p in (machine.models / "blobs").iterdir()}
    assert len(before - after) == 1 and len(after) == 2  # gemma's own blob went, the shared one stayed


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_ac22_no_access_gives_the_fix_and_never_runs_it(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[object] = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.append(a))
    folder = wan(machine).parent
    folder.chmod(0o500)
    try:
        found = shelf.check(machine.run(), "comfyui")
    finally:
        folder.chmod(0o700)
    assert not found.ok and found.fix and ran == []


def test_ac23_a_model_brought_back_is_where_its_tool_looks(machine: Machine) -> None:
    before = shelf.local_models(machine.run())
    moved(machine, GEMMA, WAN, "huggingface:models--org--name")
    shelf.restore(machine.run(), [GEMMA, WAN, "huggingface:models--org--name"])
    after = shelf.local_models(machine.run())
    for key in (GEMMA, WAN, "huggingface:models--org--name"):
        assert after[key].files == before[key].files and after[key].links == before[key].links
    ollama = next(s for s in machine.run().stores if s.id == "ollama")
    assert {n for i in ollama.items for n in i.names} >= {"gemma:31b", "tiny:latest"}


def test_ac24_an_unplugged_drive_is_listed_and_found_by_its_id(machine: Machine) -> None:
    moved(machine, WAN)
    the_run = machine.run()
    the_run.volumes = the_run.volumes[:1]
    listed = shelf.contents(the_run)
    assert [(d.mount, list(d.shelf.models)) for d in listed] == [(None, [WAN])]
    elsewhere = machine.drive.rename(machine.home.parent / "mounted-elsewhere")
    the_run = machine.run()
    the_run.volumes[1].mount = the_run.volumes[1].device = str(elsewhere)
    assert [d.mount for d in shelf.contents(the_run)] == [str(elsewhere)]


def test_ac25_no_room_or_a_filesystem_that_cant_hold_it_is_refused(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hone_drive import shelf_move

    the_run = machine.run()
    monkeypatch.setattr(shelf_move, "MARGIN", 10**15)
    assert "room" in (shelf.plan(the_run, [WAN], to=str(machine.drive)).models[0].refused or "")
    monkeypatch.setattr(shelf_move, "MARGIN", 0)
    monkeypatch.setattr(shelf_move, "FAT32_LIMIT", 10)
    the_run.volume(str(machine.drive)).traits.large_files = False  # type: ignore[union-attr]
    assert "4 GB" in (shelf.plan(the_run, [WAN], to=str(machine.drive)).models[0].refused or "")


def test_ac26_removing_from_a_drive_needs_a_yes_for_the_only_copy(
    machine: Machine, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    moved(machine, WAN)
    monkeypatch.setattr(cli_shelf, "_shelf_run", machine.run)
    assert cli.main(["shelf", "remove", WAN]) == 1 and "only on" in capsys.readouterr().out
    assert cli.main(["shelf", "remove", WAN, "--only-copy"]) == 0
    on_drive = catalog.load(str(machine.drive))
    assert on_drive is not None and WAN not in on_drive.models
