from __future__ import annotations

from hone_drive.advice import RULES, advise
from hone_drive.model import Accounting, Duplicate, Item, Measurement, Run, Store, TestName
from hone_drive.scanner import scan
from hone_drive.testing import reference_probe

EXT = "/run/media/user/PNYRP60PSSD"
HOME = "/home/user"


def measured(test: TestName, volume: str, gbs: float, label: str | None = None) -> Measurement:
    return Measurement(
        id=f"{test}@{volume}",
        test=test,
        volume=volume,
        bytes=int(gbs * 4e9),
        seconds=4.0,
        gbs=gbs,
        cache="direct",
        spread=0.05,
        label=label,
    )


def reference_run() -> Run:
    """The machine of history/0000 with its stores and measurements."""
    run = scan(probe=reference_probe(), finders={})
    run.created = "2026-10-18T19:00:00+00:00"
    run.stores = [
        Store(
            id="ollama",
            title="Ollama models",
            path="/usr/share/ollama/.ollama/models",
            volume="/",
            bytes=388_000_000_000,
            source="ollama api",
            items=[Item(id="a", names=["gemma4:31b"], bytes=19_000_000_000, volume="/")],
        ),
        Store(
            id="comfyui",
            title="ComfyUI models",
            path=f"{HOME}/ComfyUI/models",
            volume="/",
            bytes=123_000_000_000,
            items=[
                Item(
                    id="w",
                    names=["wan.safetensors"],
                    bytes=14_000_000_000,
                    files=1,
                    volume="/",
                    path=f"{HOME}/ComfyUI/models/w",
                    accessed="2026-10-17T00:00:00+00:00",
                )
            ],
        ),
        Store(
            id="caches",
            title="Package and tool caches",
            bytes=80_000_000_000,
            items=[
                Item(
                    id="uv",
                    names=["uv packages"],
                    bytes=72_000_000_000,
                    path=f"{HOME}/.cache/uv",
                    volume="/",
                    details={"clean": "uv cache clean", "same_volume": "true"},
                ),
                Item(
                    id="torch",
                    names=["PyTorch hub"],
                    bytes=1_300_000_000,
                    path=f"{HOME}/.cache/torch",
                    volume="/",
                    details={"clean": "rm -rf ~/.cache/torch/hub", "same_volume": "false"},
                ),
            ],
        ),
        Store(
            id="docker",
            title="Docker",
            path="/var/lib/containerd",
            volume="/",
            bytes=43_000_000_000,
            source="docker",
            items=[
                Item(
                    id="build-cache",
                    names=["Build Cache"],
                    bytes=25_000_000_000,
                    details={"clean": "docker builder prune", "reclaimable_bytes": "17000000000"},
                )
            ],
        ),
    ]
    run.measurements = [
        measured("seq-read", "/", 2.2),
        measured("seq-write", "/", 0.2),
        measured("seq-read", EXT, 0.97, "link-bound"),
    ]
    run.volumes[1].at_boot = False
    return run


def by_rule(run: Run) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for item in advise(run, HOME):
        found.setdefault(item.rule, []).append(item.title)
    return found


def test_the_reference_machine_gets_the_sessions_advice() -> None:
    found = by_rule(reference_run())
    assert found["A01"] == ["/ is 89% full"]
    assert f"Move Ollama models to {EXT}" in found["A02"]
    assert f"Move ComfyUI models to {EXT}" in found["A02"]
    assert found["A03"] == ["PNY RP60 PSSD could be faster on another cable or port"]
    assert found["A04"] == ["/ writes slowly because it is nearly full"]
    assert found["A05"] == [f"Reformat {EXT} as ext4 before moving models to it"]
    assert found["A06"] == [f"Mount {EXT} at boot"]
    assert "Clean uv packages" in found["A07"] and "Clean Build Cache" in found["A07"]
    assert found["A09"] == ["Keep wan.safetensors on /"]


def test_advice_cites_and_never_runs() -> None:
    run = reference_run()
    for item in advise(run, HOME):
        assert item.why and item.cites
        assert all(os == "linux" for os in item.commands)
    move = next(a for a in advise(run, HOME) if a.title == f"Move Ollama models to {EXT}")
    text = "\n".join(move.commands["linux"])
    assert "OLLAMA_MODELS=" in text and "chown -R ollama:ollama" in text and "rm -rf" in text.split("\n")[-1]
    assert "seq-read@/" in move.cites and f"seq-read@{EXT}" in move.cites
    assert any("would load in about" in w for w in move.why)


def test_ranking_warn_first_then_space() -> None:
    advice = advise(reference_run(), HOME)
    severities = [a.severity for a in advice]
    assert severities == sorted(severities, key=["warn", "suggest", "info"].index)


def test_unaccounted_and_duplicates() -> None:
    run = reference_run()
    run.accounting = [
        Accounting(
            volume="/",
            readable_bytes=1,
            unaccounted_bytes=10**11,
            hints=["x"],
            measure_command="sudo du -xh /",
            unreadable=["/root"],
        )
    ]
    run.duplicates = [
        Duplicate(
            sha256="ab" * 32,
            bytes=10**9,
            paths=["/a/blobs/sha256-x", "/b/m.gguf"],
            stores=["lmstudio", "ollama"],
        )
    ]
    found = {a.rule: a for a in advise(run, HOME)}
    assert found["A10"].commands["linux"] == ["sudo du -xh /"]
    assert found["A08"].commands["linux"] == ['ln -sf "/a/blobs/sha256-x" "/b/m.gguf"']
    assert found["A08"].gain.bytes_freed == 10**9


def test_nothing_to_say_about_a_healthy_machine() -> None:
    run = scan(probe=reference_probe(), finders={})
    run.volumes[0].used_bytes = run.volumes[0].total_bytes // 2
    run.drives[1].link.limited = False
    assert advise(run, HOME) == []
    assert set(RULES) == {f"A{n:02d}" for n in range(1, 11)}


def test_windows_and_macos_commands() -> None:
    for os_name in ("windows", "macos"):
        run = reference_run()
        run.machine.os = os_name  # type: ignore[assignment]
        advice = advise(run, HOME)
        move = next(a for a in advice if a.rule == "A02" and "Ollama" in a.title)
        assert "OLLAMA_MODELS" in "\n".join(move.commands[os_name])
        torch = next(a for a in advice if a.title == "Clean PyTorch hub")
        expected = "Remove-Item" if os_name == "windows" else "rm -rf"
        assert expected in torch.commands[os_name][0]
