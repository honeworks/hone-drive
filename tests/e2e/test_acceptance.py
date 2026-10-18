"""The guarantees of design §15, through the public API and the command line only."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import psutil
import pytest

import hone_drive as hd
from hone_drive.model import Volume
from hone_drive.stores import Context, all_finders
from hone_drive.testing import FakeProbe, recorded_probe, reference_probe
from tests.conftest import FakeRunner, fixture, no_fetch, write

MB = 1000**2


def context(home: Path) -> Context:
    return Context(os_name="linux", home=str(home), env={"HOME": str(home)}, run=FakeRunner(), fetch=no_fetch)


def finders(*names: str) -> dict[str, hd.scanner.Finder]:  # type: ignore[name-defined]
    return {k: v for k, v in all_finders().items() if k in names}


def test_ac1_volumes_match_the_os_figures() -> None:
    run = hd.scan(finders={})
    for part in psutil.disk_partitions(all=False):
        volume = run.volume(part.mountpoint)
        if volume is None:
            continue
        usage = psutil.disk_usage(part.mountpoint)
        assert abs(volume.total_bytes - usage.total) <= usage.total * 0.01
        assert abs(volume.used_bytes - usage.used) <= usage.total * 0.01
        assert abs(volume.free_bytes - usage.free) <= usage.total * 0.01
    assert run.volumes, "at least the system volume"


@pytest.mark.parametrize("name", ["windows/desktop-usb.json", "macos/m3-usb.json", "linux/laptop-nvme.json"])
def test_ac2_unknown_facts_are_none_with_a_reason(name: str) -> None:
    run = hd.scan(probe=recorded_probe(fixture(name)), finders={})
    for drive in run.drives:
        for key in ("negotiated_gbps", "port_max_gbps", "limited"):
            if getattr(drive.link, key) is None:
                assert drive.link.unknown.get(key), (drive.id, key)
        for key in ("model", "size_bytes"):
            assert getattr(drive, key) is not None or drive.unknown.get(key)
        assert drive.link.negotiated_gbps != 0
    for gpu in run.machine.gpus:
        assert gpu.memory_bytes is not None or gpu.unknown.get("memory_bytes")


def test_ac3_a_limited_usb_link_is_marked() -> None:
    run = hd.scan(probe=recorded_probe(fixture("linux/laptop-nvme-usb.json")), finders={})
    usb = run.drive("sda")
    assert usb is not None and usb.link.limited is True
    assert (usb.link.negotiated_gbps, usb.link.port_max_gbps, usb.link.negotiated_lanes) == (10.0, 20.0, 1)


def test_ac4_two_ollama_names_for_one_model_count_once(home: Path) -> None:
    models = home / ".ollama" / "models"
    digest = "a" * 64
    write(models / "blobs" / f"sha256-{digest}", 60 * MB)
    manifest = json.dumps({"config": {"digest": f"sha256:{digest}", "size": 60 * MB}, "layers": []})
    for name in ("registry.ollama.ai/library/gemma/31b", "hf.co/user/gemma/Q4"):
        (models / "manifests" / name).parent.mkdir(parents=True, exist_ok=True)
        (models / "manifests" / name).write_text(manifest)
    run = hd.scan(probe=reference_probe(), context=context(home), finders=finders("ollama"))
    store = next(s for s in run.stores if s.id == "ollama" and s.path == str(models))
    assert len(store.items) == 1 and len(store.items[0].names) == 2
    assert store.bytes is not None and store.bytes < 2 * 60 * MB


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions, not root")
def test_ac5_unreadable_folders_are_named_never_empty(home: Path) -> None:
    folder = home / "models"
    write(folder / "open" / "a.bin", 60 * MB)
    write(folder / "locked" / "b.bin", 60 * MB)
    (folder / "locked").chmod(0)
    try:
        run = hd.scan(
            probe=reference_probe(), context=context(home), finders=finders("custom"), add=[str(folder)]
        )
    finally:
        (folder / "locked").chmod(0o755)
    store = run.stores[0]
    assert str(folder / "locked") in store.unreadable
    assert store.bytes is not None and 60 * MB <= store.bytes < 120 * MB


def test_ac6_unaccounted_space_is_reported_with_hiding_places(tmp_path: Path) -> None:
    write(tmp_path / "seen.bin", 1 * MB)
    volume = Volume(
        mount=str(tmp_path),
        device="/dev/x",
        filesystem="ext4",
        total_bytes=1000 * MB,
        used_bytes=830 * MB,
        free_bytes=170 * MB,
    )
    probe = FakeProbe(reference_probe().facts, [], [volume])
    run = hd.scan(probe=probe, finders={}, accounting=True)
    acc = run.accounting[0]
    assert acc.unaccounted_bytes >= 820 * MB
    assert acc.hints and acc.measure_command and str(tmp_path) in acc.measure_command


def test_ac12_only_identical_bytes_are_duplicates(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(home / ".lmstudio" / "models" / "qwen.gguf", 60 * MB)
    write(home / "mine" / "qwen-copy.gguf", 60 * MB)
    other = home / "mine" / "qwen.safetensors"
    other.write_bytes(b"\2" * (60 * MB))
    write(home / ".lmstudio" / "models" / "x.safetensors", 60 * MB - 1)
    monkeypatch.setattr("hone_drive.duplicates.MIN_BYTES", 50 * MB)
    run = hd.scan(
        probe=reference_probe(),
        context=context(home),
        finders=finders("lmstudio", "custom"),
        add=[str(home / "mine")],
        duplicates=True,
    )
    assert len(run.duplicates) == 1
    assert {Path(p).name for p in run.duplicates[0].paths} == {"qwen.gguf", "qwen-copy.gguf"}


def test_ac16_scan_json_runs_without_rights_or_extra_tools(tmp_path: Path) -> None:
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "USERPROFILE": str(tmp_path),
        "HONE_DRIVE_HOME": str(tmp_path / "d"),
        "OLLAMA_HOST": "127.0.0.1:9",
    }
    done = subprocess.run(
        [sys.executable, "-m", "hone_drive", "scan", "--json"],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    run = hd.Run.model_validate_json(done.stdout)
    assert run.volumes and run.machine.os in ("linux", "macos", "windows")
    assert (tmp_path / "d" / "runs" / f"{run.id}.json").is_file()
