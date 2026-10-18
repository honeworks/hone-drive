from __future__ import annotations

import sys
from pathlib import Path

import pytest

from hone_drive.accounting import summarise
from hone_drive.model import Accounting, Folder, Item, Store, Volume
from hone_drive.scanner import os_name, probe_for, run_id, scan
from hone_drive.sizing import Tally
from hone_drive.stores import Context
from hone_drive.testing import reference_probe
from tests.conftest import FakeRunner, no_fetch


def fake_store(path: str, source: str = "files") -> Store:
    return Store(
        id="ollama",
        title="Ollama",
        path=path,
        bytes=388_000_000_000,
        source=source,
        items=[Item(id="m", names=["m:1"], bytes=1, path=None)],
    )


def test_scan_places_stores_and_marks_the_system_volume(home: Path) -> None:
    ext = str(home / "ssd")
    probe = reference_probe(external_mount=ext)
    ctx = Context(os_name="linux", home=str(home), env={}, run=FakeRunner(), fetch=no_fetch)
    messages: list[str] = []
    run = scan(
        probe=probe,
        context=ctx,
        finders={"x": lambda c: [fake_store(ext + "/models")]},
        progress=messages.append,
        add=["/somewhere"],
    )
    assert ctx.extra == ["/somewhere"]
    assert run.machine.hostname == "reference" and len(run.drives) == 2
    assert run.stores[0].volume == ext and run.stores[0].items[0].volume == ext
    if sys.platform != "win32":  # the reference machine's "/" is this OS's root only on POSIX
        assert run.volume("/") and run.volume("/").system  # type: ignore[union-attr]
    assert not run.volume(ext).system  # type: ignore[union-attr]
    assert run.drive("sda") and run.drive(None) is None
    assert messages[:2] == ["machine and drives", "store x"]


@pytest.mark.skipif(sys.platform == "win32", reason="Linux paths of the reference machine")
def test_accounting_subtracts_server_sized_stores(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    probe = reference_probe(external_mount=str(home))
    ctx = Context(os_name="linux", home=str(home), env={}, run=FakeRunner(), fetch=no_fetch)
    seen: list[tuple[str, list[str]]] = []

    def fake_account(volume: Volume, os_name: str, explained: list[Folder]) -> Accounting:
        seen.append((volume.mount, [f.path for f in explained]))
        return summarise(volume, os_name, Tally(), [], explained)

    monkeypatch.setattr("hone_drive.scanner.account", fake_account)
    monkeypatch.setattr("hone_drive.scanner.os.access", lambda path, mode: False)
    run = scan(
        probe=probe,
        context=ctx,
        finders={"x": lambda c: [fake_store("/usr/share/ollama", "ollama api")]},
        accounting=True,
        duplicates=True,
    )
    assert ("/", ["/usr/share/ollama"]) in seen
    assert run.accounting[0].unaccounted_bytes == 891_194_318_848 - 388_000_000_000
    assert run.duplicates == []


def test_os_names_ids_and_probes() -> None:
    assert os_name("linux") == "linux" and os_name("darwin") == "macos" and os_name("win32") == "windows"
    assert os_name("freebsd14") == "linux"
    assert type(probe_for("darwin")).__name__ == "MacProbe"
    assert type(probe_for("win32")).__name__ == "WindowsProbe"
    assert type(probe_for("linux")).__name__ == "LinuxProbe"
    from datetime import UTC, datetime

    assert run_id(datetime(2026, 10, 18, tzinfo=UTC), "h").startswith("20261018T000000Z-")
