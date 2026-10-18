from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from hone_drive import duplicates
from hone_drive.duplicates import find_duplicates, sha256_of
from hone_drive.model import Item, Store
from tests.conftest import write


@pytest.fixture(autouse=True)
def small(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(duplicates, "MIN_BYTES", 10)


def store(store_id: str, *paths: Path) -> Store:
    return Store(
        id=store_id,
        title=store_id,
        items=[Item(id=p.name, names=[p.name], path=str(p), files=1) for p in paths],
    )


def test_identical_bytes_in_two_stores(tmp_path: Path) -> None:
    a = write(tmp_path / "a" / "m.gguf", 100)
    b = write(tmp_path / "b" / "m.gguf", 100)
    found = find_duplicates([store("lmstudio", a), store("custom", b)])
    assert len(found) == 1 and found[0].stores == ["custom", "lmstudio"] and found[0].bytes == 100


def test_same_size_different_bytes_is_not_a_duplicate(tmp_path: Path) -> None:
    a = write(tmp_path / "a" / "model.gguf", 100)
    b = tmp_path / "b" / "model.safetensors"
    b.parent.mkdir()
    b.write_bytes(b"\2" * 100)
    assert find_duplicates([store("ollama-ish", a), store("comfyui", b)]) == []


def test_one_store_or_links_are_not_duplicates(tmp_path: Path) -> None:
    a = write(tmp_path / "a" / "x", 100)
    b = write(tmp_path / "a" / "y", 100)
    assert find_duplicates([store("one", a, b)]) == []
    if sys.platform != "win32":
        link = tmp_path / "c"
        os.symlink(a, link)
        assert find_duplicates([store("one", a), store("two", link)]) == []


def test_ollama_blob_names_are_their_hash(tmp_path: Path) -> None:
    blob = write(tmp_path / "models" / "blobs" / ("sha256-" + "0" * 64), 100)
    other = write(tmp_path / "hf" / "w.bin", 100)
    ollama = Store(id="ollama", title="o", path=str(tmp_path / "models"))
    real = sha256_of(str(other))
    assert real is not None
    blob.rename(blob.with_name("sha256-" + real))
    found = find_duplicates([ollama, store("huggingface", other)])
    assert found and found[0].sha256 == real
    assert sha256_of(str(tmp_path / "missing")) is None
