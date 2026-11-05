"""The model shelf (change 0003): what a model is per tool, moving, bringing back, removing, checking."""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

import pytest

from hone_drive import shelf, shelf_move, shelf_restore
from hone_drive import shelf_catalog as catalog
from hone_drive.errors import NotFound, ShelfBusy, ShelfDamaged, UnsupportedFormat
from hone_drive.shelf_busy import Busy
from hone_drive.shelf_copy import copy_checked
from tests.shelf_machine import Machine, build

GEMMA, TINY = "ollama:gemma:31b", "ollama:tiny:latest"
HF, WAN = "huggingface:models--org--name", "comfyui:diffusion_models/wan.safetensors"


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Machine:
    return build(tmp_path, monkeypatch)


def move(machine: Machine, *keys: str, **options: object) -> shelf.Result:
    run = machine.run()
    the_plan = shelf.plan(run, list(keys), to=str(machine.drive))
    assert not [p.refused for p in the_plan.models if p.refused], the_plan
    return shelf.move(run, the_plan, shelf.Options(**options))  # type: ignore[arg-type]


def test_what_a_model_is_per_tool(machine: Machine) -> None:
    found = shelf.local_models(machine.run())
    assert set(found) == {GEMMA, TINY, HF, WAN}
    gemma = found[GEMMA]
    assert gemma.files[-1] == "manifests/registry.ollama.ai/library/gemma/31b"  # written last on the way back
    assert len([f for f in gemma.files if f.startswith("blobs/")]) == 2 and gemma.replaceable
    tiny = found[TINY]  # Ollama 0.40: the manifests/ file, and a manifests-v2 link to the registry's bytes
    assert tiny.files[-1] == "manifests/registry.ollama.ai/library/tiny/latest"
    assert len([f for f in tiny.files if f.startswith("blobs/")]) == 3  # config, layer, the v2 manifest
    if tiny.links:
        assert tiny.links[0][0] == "manifests-v2/ollama.com/library/tiny/latest"
    hf = found[HF]
    assert "models--org--name/blobs/abc" in hf.files and hf.replaceable
    if hf.links:
        assert hf.links == [("models--org--name/snapshots/0123/model.safetensors", "../../blobs/abc")]
    assert found[WAN].files == ["wan.safetensors"] and not found[WAN].replaceable


def test_move_copies_checks_records_and_removes(machine: Machine) -> None:
    result = move(machine, GEMMA, WAN, HF)
    assert result.done == [GEMMA, WAN, HF] and not result.failed
    on_drive = catalog.load(str(machine.drive))
    assert on_drive is not None and all(on_drive.models[k].complete for k in (GEMMA, WAN, HF))
    assert not (machine.home / "ComfyUI" / "models" / "diffusion_models" / "wan.safetensors").exists()
    assert not (machine.home / ".cache" / "huggingface" / "hub" / "models--org--name").exists()
    assert machine.ollama.deleted == ["gemma:31b"]
    assert not (machine.models / "manifests" / "registry.ollama.ai" / "library" / "gemma").exists()
    entry = on_drive.models[WAN]
    copy = Path(catalog.drive_file(str(machine.drive), entry, "wan.safetensors"))
    assert copy.read_bytes() == b"c" * 150_000 and not list(machine.drive.rglob("*.part"))
    assert catalog.known()[0].models.keys() == on_drive.models.keys()  # the copy in the data folder


def test_a_blob_another_model_lists_stays_on_the_computer(machine: Machine) -> None:
    shared = json.loads((machine.models / "manifests/registry.ollama.ai/library/tiny/latest").read_text())
    shared_blob = machine.models / "blobs" / shared["config"]["digest"].replace(":", "-")
    move(machine, GEMMA)
    assert shared_blob.exists()  # tiny:latest still lists it (AC21)
    assert len(list((machine.models / "blobs").iterdir())) == 3  # tiny's config, layer and v2 manifest


def test_moving_again_copies_nothing(machine: Machine, monkeypatch: pytest.MonkeyPatch) -> None:
    move(machine, WAN, keep_local=True)
    run = machine.run()
    assert shelf.plan(run, [WAN], to=str(machine.drive)).models[0].copy_bytes == 0

    def no_copy(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("copied again")

    monkeypatch.setattr(shelf_move, "copy_checked", no_copy)
    monkeypatch.setattr(shelf_move, "sha256_of", no_copy)  # the catalog is trusted: nothing is read
    assert shelf.move(run, shelf.plan(run, [WAN], to=str(machine.drive))).done == [WAN]
    assert not (machine.home / "ComfyUI" / "models" / "diffusion_models" / "wan.safetensors").exists()


def test_a_changed_time_makes_it_hash_both_and_still_not_copy(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    move(machine, WAN, keep_local=True)
    local = machine.home / "ComfyUI" / "models" / "diffusion_models" / "wan.safetensors"
    os.utime(local, ns=(1, 1))
    run = machine.run()
    assert shelf.plan(run, [WAN], to=str(machine.drive)).models[0].copy_bytes == 150_000
    monkeypatch.setattr(shelf_move, "copy_checked", lambda *_a, **_k: pytest.fail("copied again"))
    assert shelf.move(run, shelf.plan(run, [WAN], to=str(machine.drive))).done == [WAN]


def test_a_bad_copy_removes_nothing(machine: Machine, monkeypatch: pytest.MonkeyPatch) -> None:
    real = shelf_move.copy_checked

    def damaged(source: str, target: str, *args: object, **kwargs: object) -> object:
        raise ShelfDamaged(f"the copy at {target} doesn't match {source}")

    monkeypatch.setattr(shelf_move, "copy_checked", damaged)
    result = move(machine, WAN)
    assert WAN in result.failed and result.done == []
    assert (machine.home / "ComfyUI" / "models" / "diffusion_models" / "wan.safetensors").exists()
    on_drive = catalog.load(str(machine.drive))
    assert on_drive is not None and not on_drive.models[WAN].complete
    monkeypatch.setattr(shelf_move, "copy_checked", real)
    assert move(machine, WAN).done == [WAN]


def test_copy_checked_refuses_a_source_that_is_not_what_the_catalog_says(tmp_path: Path) -> None:
    source = tmp_path / "a"
    source.write_bytes(b"x" * 10)
    with pytest.raises(ShelfDamaged):
        copy_checked(str(source), str(tmp_path / "b"), threading.Event(), expected="0" * 64)
    assert not list(tmp_path.glob("b*"))


def test_stop_keeps_the_computers_copy(machine: Machine) -> None:
    stop = threading.Event()
    result = move(machine, GEMMA, WAN, stop=stop, on_tick=lambda _t: stop.set())
    assert result.stopped and result.done == []
    assert (machine.models / "manifests" / "registry.ollama.ai" / "library" / "gemma" / "31b").exists()
    assert not list(machine.drive.rglob("*.part"))


def test_refusals(machine: Machine, monkeypatch: pytest.MonkeyPatch) -> None:
    machine.ollama.loaded = ["gemma:31b"]
    machine.ollama.comfyui_running = True
    run = machine.run()
    planned = {p.key: p for p in shelf.plan(run, [GEMMA, WAN, "ollama:none"], to=str(machine.drive)).models}
    assert "ollama stop gemma:31b" in (planned[GEMMA].refused or "")
    assert "ComfyUI is running" in (planned[WAN].refused or "")
    assert planned["ollama:none"].refused == "not on this computer"
    machine.ollama.loaded, machine.ollama.comfyui_running = [], False
    local = str(machine.home / "ComfyUI" / "models" / "diffusion_models" / "wan.safetensors")
    opened = shelf.plan(run, [WAN], to=str(machine.drive), busy=lambda: Busy(files={os.path.realpath(local)}))
    assert "open" in (opened.models[0].refused or "")
    monkeypatch.setattr(shelf_move, "MARGIN", 10**15)
    assert "not enough room" in (shelf.plan(run, [WAN], to=str(machine.drive)).models[0].refused or "")
    monkeypatch.setattr(shelf_move, "MARGIN", 0)
    monkeypatch.setattr(shelf_move, "FAT32_LIMIT", 1000)
    run.volume(str(machine.drive)).traits.large_files = False  # type: ignore[union-attr]
    assert "FAT32" in (shelf.plan(run, [WAN], to=str(machine.drive)).models[0].refused or "")
    with pytest.raises(NotFound):
        shelf.plan(run, [WAN], to="no such drive")


def test_restore_brings_it_back_and_keeps_the_drives_copy(machine: Machine) -> None:
    move(machine, GEMMA, HF, WAN)
    run = machine.run()
    order: list[str] = []
    real = shelf_restore.copy_checked

    def recorded(source: str, target: str, *args: object, **kwargs: object) -> object:
        order.append(target)
        return real(source, target, *args, **kwargs)  # type: ignore[arg-type]

    shelf_restore.copy_checked = recorded  # type: ignore[assignment]
    try:
        result = shelf.restore(run, [GEMMA, HF, WAN])
    finally:
        shelf_restore.copy_checked = real
    assert result.done == [GEMMA, HF, WAN]
    gemma = [t for t in order if ".ollama" in t]
    assert "manifests" in gemma[-1] and all("blobs" in t for t in gemma[:-1])  # the manifest last
    restored = shelf.local_models(machine.run())
    assert set(restored) == {GEMMA, TINY, HF, WAN}
    snapshot = machine.home / ".cache/huggingface/hub/models--org--name/snapshots/0123/model.safetensors"
    assert snapshot.read_bytes() == b"w" * 200_000
    on_drive = catalog.load(str(machine.drive))
    assert on_drive is not None and set(on_drive.models) == {GEMMA, HF, WAN}  # never removed by a restore
    assert shelf.restore(machine.run(), [WAN]).done == [WAN]  # again: nothing to copy, same state


def test_restore_refusals(machine: Machine) -> None:
    move(machine, WAN)
    local = machine.home / "ComfyUI" / "models" / "diffusion_models" / "wan.safetensors"
    local.write_bytes(b"something else")
    run = machine.run()
    assert "different file" in (shelf.plan_restore(run, [WAN])[0].models[0].refused or "")
    local.unlink()
    run.volumes = run.volumes[:1]  # the drive is unplugged
    assert "plug in" in (shelf.plan_restore(run, [WAN])[0].models[0].refused or "")
    assert [d.mount for d in shelf.contents(run)] == [None]  # still listed, from the data folder
    with pytest.raises(NotFound):
        shelf.plan_restore(run, ["comfyui:nothing"])


def test_remove_from_the_drive(machine: Machine) -> None:
    move(machine, GEMMA, TINY)
    run = machine.run()
    refused = shelf.remove(run, [GEMMA])
    assert GEMMA in refused.failed and "only on" in refused.failed[GEMMA]
    shelf.restore(run, [GEMMA])
    assert shelf.remove(machine.run(), [GEMMA]).done == [GEMMA]  # on the computer too: allowed
    on_drive = catalog.load(str(machine.drive))
    assert on_drive is not None and set(on_drive.models) == {TINY}
    tiny = on_drive.models[TINY]
    assert all(Path(catalog.drive_file(str(machine.drive), tiny, f.rel)).exists() for f in tiny.files)
    assert shelf.remove(machine.run(), [TINY], only_copy=True).done == [TINY]
    assert not [p for p in machine.drive.rglob("*") if p.is_file() and p.parent.name == "blobs"]
    assert (machine.models / "manifests/registry.ollama.ai/library/gemma/31b").exists()  # untouched


def test_verify_finds_a_damaged_copy(machine: Machine) -> None:
    move(machine, WAN)
    assert shelf.verify(machine.run()) == {}
    on_drive = catalog.load(str(machine.drive))
    assert on_drive is not None
    Path(catalog.drive_file(str(machine.drive), on_drive.models[WAN], "wan.safetensors")).write_bytes(b"bad")
    assert WAN in shelf.verify(machine.run())
    assert "not complete" in (shelf.plan_restore(machine.run(), [WAN])[0].models[0].refused or "")


def test_catalog_formats_and_lock(tmp_path: Path) -> None:
    mount = str(tmp_path)
    first = catalog.open_shelf(mount, "PNY")
    assert first.drive == catalog.drive_id(mount) and len(first.drive) == 32
    catalog.save(first)
    assert catalog.load(mount) == first
    data = json.loads((tmp_path / "hone-drive" / "shelf.json").read_text())
    (tmp_path / "hone-drive" / "shelf.json").write_text(json.dumps({**data, "format": 99}))
    with pytest.raises(UnsupportedFormat):
        catalog.load(mount)
    (tmp_path / "hone-drive" / "shelf.lock").write_text("another-computer 1")
    with pytest.raises(ShelfBusy, match="another computer"), catalog.locked(mount):
        pass


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="POSIX permissions, not as root")
def test_check_finds_what_this_user_cant_change(machine: Machine) -> None:
    run = machine.run()
    assert shelf.check(run, "comfyui").ok
    folder = machine.home / "ComfyUI" / "models" / "diffusion_models"
    folder.chmod(0o500)
    try:
        found = shelf.check(run, "comfyui")
        assert not found.ok and found.problems and found.fix == [f'chmod -R u+rwX "{folder}"']
        planned = shelf.plan(run, [WAN], to=str(machine.drive)).models[0]
        assert planned.refused and planned.fix
    finally:
        folder.chmod(0o700)
    assert shelf.check(run, "nothing").ok


def test_the_fix_is_built_for_this_system(monkeypatch: pytest.MonkeyPatch) -> None:
    from hone_drive import access

    monkeypatch.setattr(access, "owner_of", lambda _p: "ollama")
    tools = {"setfacl": "/usr/bin/setfacl", "pacman": "/usr/bin/pacman"}
    linux = access.fix_for("/usr/share/ollama", "linux", "bahman", tools.get)
    assert linux[0] == 'sudo setfacl -R -m u:bahman:rwX,d:u:bahman:rwX,d:u:ollama:rwX "/usr/share/ollama"'
    assert "to undo it" in linux[1]
    no_acl = access.fix_for("/usr/share/ollama", "linux", "bahman", {"apt": "/usr/bin/apt"}.get)
    assert no_acl[0] == "sudo apt install acl" and no_acl[1].startswith("sudo setfacl")
    monkeypatch.setattr(access, "owner_of", lambda _p: "bahman")
    assert access.fix_for("/x", "linux", "bahman") == ['chmod -R u+rwX "/x"']
    assert access.fix_for("/x", "macos", "me")[0].startswith('sudo chmod -R +a "user:me allow read,write')
    assert access.fix_for("C:\\m", "windows", "me")[0].startswith('icacls "C:\\m" /grant "me:(OI)(CI)M" /T')
    monkeypatch.setattr(access, "owner_of", lambda _p: "ollama")
    read = access.fix_for("/usr/share/ollama", "linux", "bahman", tools.get, write=False)
    assert read[0] == 'sudo setfacl -R -m u:bahman:rX,d:u:bahman:rX "/usr/share/ollama"'  # reading only
    assert "RX" in access.fix_for("C:\\m", "windows", "me", write=False)[0]
    assert "allow read,list" in access.fix_for("/m", "macos", "me", write=False)[0]


def test_ollama_sized_by_its_server_needs_the_fix_first(machine: Machine) -> None:
    run = machine.run()
    ollama = next(s for s in run.stores if s.id == "ollama")
    ollama.source = "ollama api"  # what a scan finds when the folder belongs to the service's user
    planned = {p.key: p for p in shelf.plan(run, [GEMMA], to=str(machine.drive)).models}
    assert "can't read" in (planned[GEMMA].refused or "")


def test_refusals_are_reported_with_the_result(machine: Machine) -> None:
    move(machine, WAN)
    run = machine.run()
    run.volumes = run.volumes[:1]  # unplugged
    result = shelf.restore(run, [WAN])
    assert result.done == [] and "plug in" in result.failed[WAN]
    assert not Path("hone-drive").exists()  # no lock taken on an empty mount point
    machine.ollama.loaded = ["gemma:31b"]
    run = machine.run()
    refused = shelf.move(run, shelf.plan(run, [GEMMA], to=str(machine.drive)))
    assert "loaded" in refused.failed[GEMMA]


def test_ollama_takes_a_model_back_itself_when_its_folder_is_read_only(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Decision D-024: Ollama's service makes its folders 0755, so another user can't write into them;
    hone-drive serves the drive's copy as a registry and Ollama pulls, copies and drops the temporary name."""
    machine.ollama.running = True
    move(machine, GEMMA)
    manifest = machine.models / "manifests" / "registry.ollama.ai" / "library" / "gemma" / "31b"
    assert not manifest.exists()
    monkeypatch.setattr(shelf_restore, "VIA_SERVER", "always")
    result = shelf.restore(machine.run(), [GEMMA])
    assert result.done == [GEMMA], result
    assert manifest.exists() and machine.ollama.deleted[-1].startswith("127.0.0.1:")  # the temporary name
    temporary = [
        p for p in (machine.models / "manifests").rglob("*") if p.is_file() and "127.0.0.1" in str(p)
    ]
    assert temporary == []
    assert set(shelf.local_models(machine.run())) >= {GEMMA, TINY}


def test_bringing_back_through_ollama_needs_it_running(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    move(machine, GEMMA)
    monkeypatch.setattr(shelf_restore, "VIA_SERVER", "always")
    planned = shelf.plan_restore(machine.run(), [GEMMA])[0].models[0]
    assert planned.refused and "start Ollama" in planned.refused


def test_ollama_040_models_leave_and_come_back_whole(machine: Machine) -> None:
    """Decision D-025: both of Ollama's folders, and the blob a manifests-v2 link points to."""
    v2 = machine.models / "manifests-v2" / "ollama.com" / "library" / "tiny" / "latest"
    before = v2.read_bytes()
    assert move(machine, TINY).done == [TINY]
    assert not os.path.lexists(v2) and machine.ollama.deleted == ["tiny:latest"]
    assert shelf.restore(machine.run(), [TINY]).done == [TINY]
    assert v2.read_bytes() == before and os.path.islink(v2) == (sys.platform != "win32" or os.path.islink(v2))
    assert TINY in shelf.local_models(machine.run())
    assert shelf.remove(machine.run(), [TINY]).done == [TINY]  # on the computer: the drive may let go


@pytest.mark.parametrize("route", ["auto", "always"])
def test_an_ollama_040_model_known_only_by_a_manifests_v2_file(
    machine: Machine, monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    """Ollama 0.40 on Windows keeps manifests-v2 as files, and a copied name has no manifests/ file."""
    legacy = machine.models / "manifests" / "registry.ollama.ai" / "library" / "tiny" / "latest"
    v2 = machine.models / "manifests-v2" / "ollama.com" / "library" / "tiny" / "latest"
    raw = v2.read_bytes()
    legacy.unlink()
    v2.unlink()
    v2.write_bytes(raw)
    machine.ollama.running = route == "always"
    monkeypatch.setattr(shelf_restore, "VIA_SERVER", route)
    assert move(machine, TINY).done == [TINY]
    restored = shelf.restore(machine.run(), [TINY])
    assert restored.done == [TINY], restored
    assert TINY in shelf.local_models(machine.run())
    assert shelf.remove(machine.run(), [TINY]).done == [TINY]
