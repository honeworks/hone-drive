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


def test_files_inside_a_hugging_face_repo_are_candidates(tmp_path: Path) -> None:
    """Design §5.3: any file over the minimum, also inside a repo of many files."""
    repo = tmp_path / "hub" / "models--org--name"
    blob = write(repo / "blobs" / "abc", 100)
    write(repo / "blobs" / "small", 5)
    comfy = write(tmp_path / "comfy" / "model.safetensors", 100)
    hf = Store(
        id="huggingface", title="hf", items=[Item(id=repo.name, names=["org/name"], path=str(repo), files=2)]
    )
    found = find_duplicates([hf, store("comfyui", comfy)])
    assert len(found) == 1 and found[0].paths == sorted([str(blob), str(comfy)])


def test_only_hugging_face_folders_are_walked(tmp_path: Path) -> None:
    """Other stores list their large files one by one; a folder item there is not walked."""
    folder = tmp_path / "lm"
    write(folder / "a.gguf", 100)
    other = write(tmp_path / "b" / "a.gguf", 100)
    lm = Store(id="lmstudio", title="lm", items=[Item(id="x", names=["x"], path=str(folder), files=1)])
    assert find_duplicates([lm, store("custom", other)]) == []
