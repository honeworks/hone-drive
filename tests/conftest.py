"""Shared fixtures: recorded machines, a fake home folder with model stores, a data folder per test."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hone_drive.stores import Context
from hone_drive.system import Output

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeRunner:
    """Answers commands from a dict keyed by the command's first words; unknown commands are missing."""

    def __init__(self, answers: dict[str, Output] | None = None) -> None:
        self.answers = answers or {}
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], timeout: float = 10.0) -> Output:
        self.calls.append(args)
        for prefix, out in self.answers.items():
            if " ".join(args).startswith(prefix):
                return out
        return Output(None, "", f"{args[0]} is not installed")


def no_fetch(_url: str) -> bytes | None:
    return None


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    return path


@pytest.fixture
def ctx(home: Path) -> Context:
    return Context(os_name="linux", home=str(home), env={"HOME": str(home)}, run=FakeRunner(), fetch=no_fetch)


@pytest.fixture(autouse=True)
def data_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test writes runs into its own folder, never the user's."""
    path = tmp_path / "data"
    monkeypatch.setenv("HONE_DRIVE_HOME", str(path))
    return path


def write(path: Path, size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(b"\1" * size)
    return path
