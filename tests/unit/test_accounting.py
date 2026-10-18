from __future__ import annotations

from pathlib import Path

from hone_drive.accounting import account, summarise
from hone_drive.model import Folder, Volume
from hone_drive.sizing import Tally
from tests.conftest import write


def volume(mount: str, used: int, total: int = 1_000) -> Volume:
    return Volume(
        mount=mount,
        device="/dev/x",
        filesystem="ext4",
        total_bytes=total,
        used_bytes=used,
        free_bytes=total - used,
    )


def test_unaccounted_space_gets_hints_and_a_command() -> None:
    acc = summarise(
        volume("/", 830),
        "linux",
        Tally(bytes=424, unreadable=["/usr/share/ollama", "/usr/share/ollama/x"]),
        [],
    )
    assert acc.unaccounted_bytes == 406
    assert any("Ollama" in h for h in acc.hints)
    assert acc.measure_command and acc.measure_command.startswith("sudo du -xh")
    assert acc.unreadable == ["/usr/share/ollama"]


def test_space_a_server_explains_is_subtracted() -> None:
    acc = summarise(
        volume("/", 830), "linux", Tally(bytes=424), [], [Folder(path="/usr/share/ollama", bytes=388)]
    )
    assert acc.unaccounted_bytes == 18 and acc.hints == [] and acc.measure_command is None


def test_each_os_has_its_command() -> None:
    assert "Get-ChildItem C:\\" in (
        summarise(volume("C:\\", 900), "windows", Tally(), []).measure_command or ""
    )
    assert (summarise(volume("/", 900), "macos", Tally(), []).measure_command or "").startswith(
        "sudo du -xh -d 3"
    )


def test_account_walks_a_folder(tmp_path: Path) -> None:
    write(tmp_path / "a" / "b" / "f.bin", 100_000)
    seen: list[int] = []
    acc = account(volume(str(tmp_path), used=200_000, total=10_000_000), "linux", progress=seen.append)
    assert acc.readable_bytes >= 100_000
    assert [f.path for f in acc.top_folders][:2] == [str(tmp_path / "a"), str(tmp_path / "a" / "b")]
