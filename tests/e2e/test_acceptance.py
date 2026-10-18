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


# -- benchmarks (milestone M2) -----------------------------------------------------------------------------

BENCH_SCRIPT = """
import os, sys
from hone_drive.benchmark import Options, bench
from hone_drive.model import Volume
from hone_drive.scanner import scan
from hone_drive.testing import FakeProbe, reference_probe
folder, mount = sys.argv[1], sys.argv[2]
volume = Volume(mount=mount, device="t", filesystem="t", total_bytes=1, used_bytes=0, free_bytes=1)
run = scan(probe=FakeProbe(reference_probe().facts, [], [volume]), finders={})
def die(tick):
    if tick.done >= 16 * 1024 * 1024:
        os._exit(9)  # killed mid-write: no finally block runs
bench(run, [folder], ["seq"], Options(size=64 * 1024 * 1024, on_tick=die))
"""


def bench_volume_run(tmp_path: Path) -> hd.Run:
    mounts = [p.mountpoint for p in psutil.disk_partitions(all=True)]
    from hone_drive.volumes import disk_usage, mount_of

    mount = mount_of(str(tmp_path.resolve()), mounts) or str(tmp_path)
    total, used, free = disk_usage(mount)
    volume = Volume(
        mount=mount, device="t", filesystem="t", total_bytes=total, used_bytes=used, free_bytes=free
    )
    return hd.scan(probe=FakeProbe(reference_probe().facts, [], [volume]), finders={})


def test_ac7_every_measurement_records_how_the_cache_was_bypassed(tmp_path: Path) -> None:
    run = hd.bench(bench_volume_run(tmp_path), [str(tmp_path)], ["seq"], hd.BenchOptions(size=32 * 1024**2))
    assert run.measurements
    for m in run.measurements:
        assert m.cache in ("direct", "fallback", "possibly-cached")
        if m.cache == "possibly-cached":
            assert any("memory" in w for w in m.warnings)


def test_ac8_the_temporary_file_is_gone_after_a_kill_and_the_next_start(tmp_path: Path) -> None:
    run = bench_volume_run(tmp_path)
    env = {**os.environ}
    done = subprocess.run(
        [sys.executable, "-c", BENCH_SCRIPT, str(tmp_path), run.volumes[0].mount],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 9, done.stderr
    assert list(tmp_path.glob(".hone-drive-bench-*")), "the killed run left its file"
    hd.bench(run, [str(tmp_path)], ["seq"], hd.BenchOptions(size=16 * 1024**2))
    assert not list(tmp_path.glob(".hone-drive-bench-*"))


def test_ac9_a_write_test_refuses_without_room_and_stops_when_space_runs_low(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = bench_volume_run(tmp_path)
    with pytest.raises(hd.UnsafeBenchmark, match="GB free"):
        hd.bench(run, [str(tmp_path)], ["seq"], hd.BenchOptions(size=10**17))
    monkeypatch.setattr("hone_drive.bench_safety.low_on_space", lambda folder: True)
    hd.bench(run, [str(tmp_path)], ["seq"], hd.BenchOptions(size=32 * 1024**2))
    assert [m.partial for m in run.measurements] == [True]


def test_ac10_load_times_use_the_measured_read_and_say_disk_time_only() -> None:
    run = hd.scan(probe=reference_probe(), finders={})
    item = hd.Item(id="m", names=["gemma4:31b"], bytes=19_000_000_000)
    by_link = hd.load_time(item, "/run/media/user/PNYRP60PSSD", run)
    assert by_link is not None and by_link.basis == "expected link speed" and "disk time only" in by_link.note
    run.measurements = [
        hd.Measurement(
            id="seq-read@/",
            test="seq-read",
            volume="/",
            bytes=4_000_000_000,
            seconds=1.82,
            gbs=2.2,
            cache="direct",
        )
    ]
    measured = hd.load_time(item, "/", run)
    assert measured is not None and measured.basis == "measured seq-read" and measured.cites == ["seq-read@/"]
    assert round(measured.seconds) == 9


# -- advice, reports, the dashboard and history (milestone M3) --------------------------------------------


def test_ac11_advice_never_runs_a_command_and_always_explains(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.unit.test_advice import reference_run

    ran: list[object] = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.append(a))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: ran.append(a))
    run = reference_run()
    advice = hd.advise(run, "/home/user")
    assert advice and ran == []
    for item in advice:
        assert item.rule and item.why and item.cites
        assert set(item.commands) <= {run.machine.os}


def test_ac13_a_report_removes_private_details() -> None:
    from tests.unit.test_advice import reference_run

    run = reference_run()
    run.machine.hostname = "workstation-7"
    run.drives[1].serial = "PNY1234567"
    run.volumes[1].uuid = "6A1F-0C2D"
    run.stores[1].path = "/home/alice/ComfyUI/models"
    text = hd.report(run, "md", home="/home/alice") + hd.report(run, "json", home="/home/alice")
    for private in ("workstation-7", "PNY1234567", "6A1F-0C2D", "alice"):
        assert private not in text
    assert "PNY1234567" in hd.report(run, "json", keep_private=True, home="/home/alice")


def test_ac14_the_dashboard_is_local_and_guarded(data_home: Path) -> None:
    import http.client

    from hone_drive.dashboard import start, stop

    server = start(0)
    try:
        assert server.server_address[0] == "127.0.0.1"
        port = server.server_address[1]

        def call(method: str, headers: dict[str, str]) -> int:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                method,
                "/api/runs" if method == "GET" else "/api/stop",
                body=b"{}" if method == "POST" else None,
                headers=headers,
            )
            status = conn.getresponse().status
            conn.close()
            return status

        assert call("GET", {"Host": f"127.0.0.1:{port}"}) == 200
        assert call("GET", {"Host": "rebind.example"}) == 403
        assert call("POST", {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"}) == 403
    finally:
        stop(server)


def test_ac15_compare_reports_a_renegotiated_link_and_a_real_speed_change() -> None:
    from tests.unit.test_advice import reference_run

    before, after = reference_run(), reference_run()
    after.drives[1].link.negotiated_gbps, after.drives[1].link.negotiated_lanes = 20.0, 2
    after.measurements[2] = after.measurements[2].model_copy(update={"gbs": 1.9})
    after.measurements[0] = after.measurements[0].model_copy(update={"gbs": 2.25})  # within ±5%: noise
    kinds = {(c.kind, c.subject) for c in hd.compare(before, after)}
    assert ("link", "PNY RP60 PSSD") in kinds
    assert ("speed", "seq-read@/run/media/user/PNYRP60PSSD") in kinds
    assert ("speed", "seq-read@/") not in kinds
