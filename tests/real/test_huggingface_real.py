"""The shelf with the real Hugging Face cache (change 0003, `-m real`): `huggingface_hub` downloads a tiny
repo into a cache of its own (symlinked snapshots on Linux and macOS, copies on Windows without the right
to make links), the shelf moves it to the drive (an exFAT image on CI, which holds no links) and back, and
`huggingface_hub` then finds every file offline with the same bytes.

    scripts/gpu-lock.sh uv run --group real pytest -m real"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from hone_drive import shelf
from hone_drive.scanner import os_name
from hone_drive.shelf_busy import Busy
from hone_drive.stores import Context, find_huggingface
from tests.real.real_machine import ROOT, allowed, drive_folder, machine

pytestmark = pytest.mark.real
REPO = "hf-internal-testing/tiny-random-bert"
FILES = ["config.json", "model.safetensors", "tokenizer.json"]


def digest(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_a_real_hugging_face_repo_leaves_and_comes_back(monkeypatch: pytest.MonkeyPatch) -> None:
    allowed()
    hub_api = pytest.importorskip("huggingface_hub", reason="uv sync --group real")
    cache = ROOT / "hf" / "hub"
    shutil.rmtree(cache, ignore_errors=True)
    cache.mkdir(parents=True)
    drive = drive_folder()
    try:
        before = {f: digest(hub_api.hf_hub_download(REPO, f, cache_dir=str(cache))) for f in FILES}
        ctx = Context(os_name=os_name(), home=str(Path.home()), env={"HF_HUB_CACHE": str(cache)})
        run = machine([cache], drive, {"huggingface": find_huggingface}, ctx)
        key = "huggingface:models--" + REPO.replace("/", "--")
        the_run = run()
        the_plan = shelf.plan(the_run, [key], to=str(drive), ctx=ctx, busy=Busy)
        assert the_plan.models[0].refused is None, the_plan
        assert shelf.move(the_run, the_plan, ctx=ctx).done == [key]
        assert not (cache / ("models--" + REPO.replace("/", "--"))).exists()
        with pytest.raises(Exception):  # noqa: B017, PT011 - offline and gone: huggingface_hub can't find it
            hub_api.hf_hub_download(REPO, FILES[0], cache_dir=str(cache), local_files_only=True)
        assert shelf.restore(run(), [key]).done == [key]
        after = {
            f: digest(hub_api.hf_hub_download(REPO, f, cache_dir=str(cache), local_files_only=True))
            for f in FILES
        }
        assert after == before
        monkeypatch.setenv("HF_HUB_OFFLINE", "1")
        assert Path(hub_api.snapshot_download(REPO, cache_dir=str(cache), local_files_only=True)).is_dir()
    finally:
        shutil.rmtree(ROOT / "hf", ignore_errors=True)
        shutil.rmtree(drive, ignore_errors=True)
