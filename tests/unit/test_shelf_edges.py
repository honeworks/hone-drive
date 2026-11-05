"""The shelf's edge cases (change 0003): a drive mounted elsewhere, a damaged catalog, a file that changes
while it is copied, the in-use check's mapped files, links on Windows, the way back through Ollama's
server when its folder is read-only, and what happens when Ollama refuses."""

from __future__ import annotations

import errno
import json
import os
import socket
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hone_drive import access, ollama_server, shelf, shelf_busy, shelf_models, shelf_move, shelf_restore
from hone_drive import shelf_catalog as catalog
from hone_drive.errors import ShelfDamaged
from hone_drive.ollama_server import manifest_digests
from tests.shelf_machine import Machine, build

GEMMA, TINY = "ollama:gemma:31b", "ollama:tiny:latest"
HF, WAN = "huggingface:models--org--name", "comfyui:diffusion_models/wan.safetensors"
POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0, reason="POSIX permissions, not as root"
)


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Machine:
    return build(tmp_path, monkeypatch)


def move(machine: Machine, *keys: str) -> shelf.Result:
    run = machine.run()
    return shelf.move(run, shelf.plan(run, list(keys), to=str(machine.drive)))


def wan(machine: Machine) -> Path:
    return machine.home / "ComfyUI" / "models" / "diffusion_models" / "wan.safetensors"


# -- the drive and its catalog ------------------------------------------------------------------------------


def test_a_drive_mounted_elsewhere_is_read_and_written_where_it_is(machine: Machine) -> None:
    assert move(machine, WAN, GEMMA).done == [WAN, GEMMA]
    elsewhere = machine.drive.rename(machine.home.parent / "mounted-elsewhere")

    def run_now() -> Any:
        found = machine.run()
        found.volumes[1].mount = found.volumes[1].device = str(elsewhere)
        return found

    assert shelf.restore(run_now(), [WAN]).done == [WAN] and wan(machine).exists()
    assert shelf.remove(run_now(), [WAN]).done == [WAN]
    on_drive = catalog.load(str(elsewhere))
    assert on_drive is not None and on_drive.mount == str(elsewhere) and set(on_drive.models) == {GEMMA}
    assert not machine.drive.exists()  # nothing was written where the drive used to be


@pytest.mark.parametrize("text", ["{not json", "[1, 2]", '{"format": 1, "drive": "x"}'])
def test_a_damaged_catalog_is_an_error_never_an_empty_shelf(machine: Machine, text: str) -> None:
    assert move(machine, WAN).done == [WAN]
    path = machine.drive / "hone-drive" / "shelf.json"
    path.write_text(text)
    with pytest.raises(ShelfDamaged):
        catalog.load(str(machine.drive))
    with pytest.raises(ShelfDamaged):
        move(machine, GEMMA)  # would otherwise start a new, empty catalog over it
    assert path.read_text() == text


def test_a_file_that_changes_while_it_is_copied_is_not_removed(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = shelf_move.copy_checked

    def copy_then_write(source: str, *args: Any, **kwargs: Any) -> Any:
        found = real(source, *args, **kwargs)
        with open(source, "ab") as handle:  # something writes the file meanwhile
            handle.write(b"more")
        return found

    monkeypatch.setattr(shelf_move, "copy_checked", copy_then_write)
    result = move(machine, WAN)
    assert "changed while" in result.failed[WAN] and wan(machine).exists()
    on_drive = catalog.load(str(machine.drive))
    assert on_drive is not None and not on_drive.models[WAN].complete


def test_moving_a_model_already_gone_to_the_drive_is_done(machine: Machine) -> None:
    assert move(machine, WAN).done == [WAN]
    run = machine.run()
    planned = shelf.plan(run, [WAN, "comfyui:nothing"], to=str(machine.drive)).models
    assert planned[0].refused is None and planned[0].copy_bytes == 0
    assert planned[1].refused == "not on this computer"
    result = shelf.move(run, shelf.plan(run, [WAN], to=str(machine.drive)))
    assert result.done == [WAN] and result.failed == {}


# -- in use: mapped files ----------------------------------------------------------------------------------


def test_look_sees_mapped_files_and_survives_what_psutil_cant_do(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import psutil

    mapped = tmp_path / "model.gguf"
    mapped.write_bytes(b"x")

    def refuses(**_kwargs: Any) -> Any:
        raise psutil.AccessDenied(1)

    info = {"username": "me", "name": "llama-server", "cmdline": [], "cwd": "/"}
    procs = [
        SimpleNamespace(
            pid=1, info=info, open_files=list, memory_maps=lambda grouped: [SimpleNamespace(path=str(mapped))]
        ),
        SimpleNamespace(pid=2, info=info, open_files=list),  # no memory_maps on this system
        SimpleNamespace(pid=3, info=info, open_files=list, memory_maps=refuses),
        SimpleNamespace(pid=4, info={**info, "username": "else"}, open_files=list),
    ]
    monkeypatch.setattr(psutil, "process_iter", lambda _attrs: iter(procs))
    monkeypatch.setattr(psutil, "Process", lambda: SimpleNamespace(username=lambda: "me"))
    assert shelf_busy.look().files == {os.path.realpath(mapped)}


# -- links written with "/" and made with the OS's separator ---------------------------------------------------


def test_links_are_kept_with_slashes_and_made_with_the_os_separator(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = machine.home / ".cache" / "huggingface" / "hub" / "models--org--name"
    if not (repo / "snapshots" / "0123" / "model.safetensors").is_symlink():
        pytest.skip("this system made a copy, not a link")
    monkeypatch.setattr(shelf_models.os, "readlink", lambda _p: "..\\..\\blobs\\abc")
    monkeypatch.setattr(shelf_models.os, "sep", "\\")
    found = shelf.local_models(machine.run())[HF]
    assert found.links == [("models--org--name/snapshots/0123/model.safetensors", "../../blobs/abc")]
    made: list[str] = []
    monkeypatch.setattr(shelf_restore.os, "symlink", lambda target, _path: made.append(target))
    shelf_restore._link(str(machine.home / "x" / "link"), "../../blobs/abc")  # pyright: ignore[reportPrivateUsage]
    assert made == ["..\\..\\blobs\\abc"]


# -- Ollama: the way back through its server ---------------------------------------------------------------


@POSIX_ONLY
def test_a_read_only_ollama_folder_brings_models_back_through_the_server(machine: Machine) -> None:
    """What decides the route in use (decision D-024): a folder this user may read but not write."""
    machine.ollama.running = True
    assert move(machine, GEMMA).done == [GEMMA]
    for name in (
        "blobs",
        "manifests",
        "manifests/registry.ollama.ai",
        "manifests/registry.ollama.ai/library",
    ):
        (machine.models / name).chmod(0o555)
    try:
        assert not access.check([str(machine.models / "blobs")], [], "linux").ok
        result = shelf.restore(machine.run(), [GEMMA])
        assert result.done == [GEMMA], result
        assert machine.ollama.pulled and machine.ollama.pulled[0].startswith("127.0.0.1:")
    finally:
        for path in [machine.models, *machine.models.rglob("*")]:
            if path.is_dir():
                path.chmod(0o755)


def test_a_writable_ollama_folder_gets_its_files_directly(machine: Machine) -> None:
    machine.ollama.running = True
    assert move(machine, GEMMA).done == [GEMMA]
    assert shelf.restore(machine.run(), [GEMMA]).done == [GEMMA]
    assert machine.ollama.pulled == []


@POSIX_ONLY
def test_check_for_reading_only(tmp_path: Path) -> None:
    folder = tmp_path / "service"
    folder.mkdir()
    folder.chmod(0o000)
    try:
        found = access.check([str(folder / "models")], [], "linux", write=False)
        assert not found.ok and found.problems == [f"can't read {folder}"] and "rX" in found.fix[0]
    finally:
        folder.chmod(0o755)
    assert access.check([str(folder)], [], "linux", write=False).ok


def test_ollama_refusing_leaves_no_stray_name(machine: Machine, monkeypatch: pytest.MonkeyPatch) -> None:
    machine.ollama.running = machine.ollama.fail_pull = True
    assert move(machine, GEMMA).done == [GEMMA]
    monkeypatch.setattr(shelf_restore, "VIA_SERVER", "always")
    seen: list[Any] = []
    real = ollama_server._Registry  # pyright: ignore[reportPrivateUsage]

    def kept(*args: Any) -> Any:
        seen.append(real(*args))
        return seen[-1]

    monkeypatch.setattr(ollama_server, "_Registry", kept)
    result = shelf.restore(machine.run(), [GEMMA])
    assert "could not take back gemma:31b" in result.failed[GEMMA]
    assert machine.ollama.deleted[-1].startswith("127.0.0.1:")  # the temporary name is dropped
    with (
        pytest.raises(ConnectionRefusedError),
        socket.create_connection(("127.0.0.1", seen[0].port), timeout=1),
    ):
        pass  # and the registry is closed


def test_ollama_listing_other_layers_is_not_the_model(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    machine.ollama.running = machine.ollama.wrong_copy = True
    assert move(machine, GEMMA).done == [GEMMA]
    monkeypatch.setattr(shelf_restore, "VIA_SERVER", "always")
    result = shelf.restore(machine.run(), [GEMMA])
    assert "does not list gemma:31b with the drive's layers" in result.failed[GEMMA]
    run = machine.run()
    refused = shelf.remove(run, [GEMMA])
    assert GEMMA in refused.failed  # not "on the computer": the drive's copy stays


@pytest.mark.parametrize(
    "data",
    [[], {}, {"config": {}, "layers": []}, {"config": {"digest": "sha256:a"}, "layers": []},
     {"config": {"digest": "sha256:a"}, "layers": [{"size": 1}]}, {"config": "x", "layers": [{"digest": "d"}]}],
)  # fmt: skip
def test_a_manifest_without_its_digests_is_never_the_same_model(data: object) -> None:
    with pytest.raises(ValueError, match="manifest"):
        manifest_digests(data)
    ok = {"config": {"digest": "sha256:a"}, "layers": [{"digest": "sha256:b"}]}
    assert manifest_digests(json.loads(json.dumps(ok, indent=3))) == ("sha256:a", "sha256:b")


def test_ollama_here_needs_every_name_and_blob(machine: Machine) -> None:
    assert move(machine, TINY).done == [TINY]
    assert shelf.restore(machine.run(), [TINY]).done == [TINY]
    on_drive = catalog.load(str(machine.drive))
    assert on_drive is not None
    entry = on_drive.models[TINY]
    assert ollama_server.ollama_here(entry, str(machine.drive))
    layer = next(
        machine.models.joinpath(f.rel) for f in entry.files if f.rel.startswith("blobs/") and f.bytes > 1000
    )
    layer.rename(layer.with_suffix(".away"))
    assert not ollama_server.ollama_here(entry, str(machine.drive))
    layer.with_suffix(".away").rename(layer)
    entry.names.append("tiny:other")
    assert not ollama_server.ollama_here(entry, str(machine.drive))


# -- the registry's ports ----------------------------------------------------------------------------------


def test_the_registry_listens_on_both_loopbacks_or_says_why_not(monkeypatch: pytest.MonkeyPatch) -> None:
    from http.server import BaseHTTPRequestHandler

    calls: list[int] = []
    real = ollama_server._Server6  # pyright: ignore[reportPrivateUsage]

    def busy_once(*args: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise OSError(errno.EADDRINUSE, "in use")
        return real(*args)

    listen = ollama_server._listen  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(ollama_server, "_Server6", busy_once)
    try:
        servers = listen(BaseHTTPRequestHandler)
    except OSError:
        pytest.skip("no IPv6 loopback here")
    assert len(calls) == 2 and len(servers) == 2  # tried again on another port
    for server in servers:
        server.server_close()

    def no_ipv6(*_args: Any) -> Any:
        raise OSError(errno.EAFNOSUPPORT, "no IPv6")

    monkeypatch.setattr(ollama_server, "_Server6", no_ipv6)
    only = listen(BaseHTTPRequestHandler)
    assert len(only) == 1
    only[0].server_close()

    def denied(*_args: Any) -> Any:
        raise OSError(errno.EACCES, "denied")

    monkeypatch.setattr(ollama_server, "_Server6", denied)
    with pytest.raises(OSError, match="denied"):
        listen(BaseHTTPRequestHandler)
