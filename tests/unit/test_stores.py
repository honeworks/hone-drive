from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from hone_drive import store_caches
from hone_drive.store_caches import find_caches
from hone_drive.store_comfyui import extra_model_paths, find_comfyui
from hone_drive.store_docker import find_docker, parse_size
from hone_drive.store_ollama import find_ollama
from hone_drive.stores import (
    Context,
    all_finders,
    blocked,
    candidates_for,
    find_custom,
    find_folder_store,
    find_huggingface,
)
from hone_drive.system import Output
from tests.conftest import FakeRunner, write

MB = 1000**2


@pytest.fixture(autouse=True)
def only_the_fake_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep this machine's own service folders and system caches out of the tests."""
    monkeypatch.setattr(
        "hone_drive.store_ollama.DEFAULTS", {"linux": ["~/.ollama/models"], "macos": [], "windows": []}
    )
    home_only = [
        c for c in store_caches.CACHES if all(p.startswith(("~", "$")) for ps in c.paths.values() for p in ps)
    ]
    monkeypatch.setattr("hone_drive.store_caches.CACHES", home_only)


def ollama_models(root: Path) -> Path:
    """Two names for one model (one manifest) and one other model, plus an unreferenced blob."""
    models = root / "models"
    blob_a, blob_b = "a" * 64, "b" * 64
    write(models / "blobs" / f"sha256-{blob_a}", 60 * MB)
    write(models / "blobs" / f"sha256-{blob_b}", 51 * MB)
    write(models / "blobs" / f"sha256-{'c' * 64}", 1000)
    manifest = {
        "config": {"digest": f"sha256:{blob_b}", "size": 51 * MB},
        "layers": [{"digest": f"sha256:{blob_a}", "size": 60 * MB}],
    }
    other = {"config": {"digest": f"sha256:{blob_b}", "size": 51 * MB}, "layers": []}
    lib = models / "manifests" / "registry.ollama.ai" / "library"
    for path, data in (
        (lib / "gemma" / "31b", manifest),
        (models / "manifests" / "hf.co" / "user" / "gemma-gguf" / "Q4", manifest),
        (lib / "tiny" / "latest", other),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
    return models


def test_ollama_files_count_shared_names_once(ctx: Context, home: Path) -> None:
    ollama_models(home / ".ollama")
    store = find_ollama(ctx)[0]
    assert store.source == "files"
    gemma = next(i for i in store.items if "gemma:31b" in i.names)
    assert gemma.names == ["gemma:31b", "hf.co/user/gemma-gguf:Q4"]
    assert gemma.bytes == 111 * MB
    assert len([i for i in store.items if i.id != "(unreferenced blobs)"]) == 2
    assert next(i for i in store.items if i.id == "(unreferenced blobs)").bytes == 1000
    assert store.bytes is not None and store.bytes < 2 * 111 * MB  # blobs counted once


def test_ollama_api_when_the_folder_is_not_readable(ctx: Context, home: Path, monkeypatch) -> None:
    tags = {
        "models": [
            {"name": "a:1", "size": 10, "digest": "d1" * 32, "details": {"family": "gemma"}},
            {"name": "b:1", "size": 10, "digest": "d1" * 32},
            {"name": "c:1", "size": 5, "digest": "d2" * 32},
        ]
    }
    ctx.fetch = lambda url: json.dumps(tags).encode() if url.endswith("/api/tags") else None
    service = str(home / "service" / ".ollama" / "models")
    monkeypatch.setattr("hone_drive.store_ollama.DEFAULTS", {"linux": [service]})
    monkeypatch.setattr("hone_drive.store_ollama.blocked", lambda path: path == service)
    store = find_ollama(ctx)[0]
    assert store.source == "ollama api" and store.path == service and store.unreadable == [service]
    assert store.bytes == 15
    assert store.items[0].names == ["a:1", "b:1"] and store.items[0].details["family"] == "gemma"


def test_ollama_api_alone_and_nothing(ctx: Context) -> None:
    assert find_ollama(ctx) == []
    ctx.fetch = lambda url: b'{"models": [{"name": "x", "size": 3, "digest": "ff"}]}'
    store = find_ollama(ctx)[0]
    assert store.path is None and "path" in store.unknown
    ctx.fetch = lambda url: b"not json"
    assert find_ollama(ctx) == []


def test_ollama_service_folder_without_server(ctx: Context, home: Path, monkeypatch) -> None:
    monkeypatch.setattr("hone_drive.store_ollama.DEFAULTS", {"linux": ["/srv/ollama/models"]})
    monkeypatch.setattr("hone_drive.store_ollama.blocked", lambda path: True)
    store = find_ollama(ctx)[0]
    assert store.bytes is None and "bytes" in store.unknown


def test_ollama_models_from_the_systemd_unit(ctx: Context, home: Path) -> None:
    models = ollama_models(home / "elsewhere")
    ctx.run = FakeRunner({"systemctl cat": Output(0, f'Environment="OLLAMA_MODELS={models}"\n')})
    stores = find_ollama(ctx)
    assert [s.path for s in stores] == [str(models)]


def test_ollama_api_matching_a_readable_store_is_not_repeated(ctx: Context, home: Path) -> None:
    ollama_models(home / ".ollama")
    ctx.fetch = lambda url: b'{"models": [{"name": "tiny:latest", "size": 3, "digest": "ff"}]}'
    assert len(find_ollama(ctx)) == 1


def test_huggingface_repos(ctx: Context, home: Path) -> None:
    hub = home / ".cache" / "huggingface" / "hub"
    write(hub / "models--org--name" / "blobs" / "abc", 70 * MB)
    (hub / ".locks").mkdir()
    store = find_huggingface(ctx)[0]
    assert store.items[0].names == ["org/name"] and store.items[0].details["kind"] == "model"
    ctx.env["HF_HUB_CACHE"] = str(home / "none")
    assert find_huggingface(ctx)[0].path == str(hub)


def test_comfyui_models_and_extra_paths(ctx: Context, home: Path) -> None:
    comfy = home / "apps" / "ComfyUI"
    (comfy / "main.py").parent.mkdir(parents=True)
    (comfy / "main.py").write_text("")
    write(comfy / "models" / "checkpoints" / "big.safetensors", 60 * MB)
    write(comfy / "models" / "vae" / "small.bin", 10)
    write(home / "shared" / "loras" / "style.safetensors", 55 * MB)
    (comfy / "extra_model_paths.yaml").write_text(
        f"comfyui:\n    base_path: {home / 'shared'}\n    loras: |\n        loras/\n        missing/\n"
    )
    store = find_comfyui(ctx)[0]
    ids = [i.id for i in store.items]
    assert "checkpoints/big.safetensors" in ids
    assert any(i.endswith("loras/style.safetensors") for i in ids)
    assert "(other files)" in ids


def test_extra_model_paths_subset() -> None:
    text = (
        "#comment\ncomfyui:\n    base_path: /data/\n    is_default: true\n    checkpoints: models/ckpt/ # here\n"
        "    text_encoders: |\n         models/te/\n         C:/x/\n    custom_nodes: nodes/\n"
        "a1111:\n    base_path: webui\n    vae: models/VAE\n"
    )
    assert extra_model_paths(text, "/opt/ComfyUI") == [
        os.path.join("/data/", "models/ckpt/"),
        os.path.join("/data/", "models/te/"),
        os.path.join("/data/", "C:/x/"),
        os.path.join("/opt/ComfyUI", "webui", "models/VAE"),
    ]


def test_docker_sizes(ctx: Context) -> None:
    rows = (
        "\n".join(
            json.dumps(r)
            for r in [
                {
                    "Type": "Images",
                    "Size": "11.38GB",
                    "Reclaimable": "2.22GB (19%)",
                    "TotalCount": "57",
                    "Active": "22",
                },
                {"Type": "Build Cache", "Size": "25.17GB", "Reclaimable": "16.98GB"},
            ]
        )
        + "\nnot json\n"
    )
    ctx.run = FakeRunner(
        {
            "docker system df": Output(0, rows),
            "docker info": Output(0, '/var/lib/docker [["driver-type","io.containerd.snapshotter.v1"]]\n'),
        }
    )
    store = find_docker(ctx)[0]
    assert store.bytes == 36_550_000_000 and store.reclaimable_bytes == 19_200_000_000
    assert store.path == "/var/lib/containerd"
    assert store.items[1].details["clean"] == "docker builder prune"
    ctx.run = FakeRunner(
        {"docker system df": Output(0, rows), "docker info": Output(0, "/data/docker null\n")}
    )
    assert find_docker(ctx)[0].path == "/data/docker"
    ctx.run = FakeRunner({"docker system df": Output(0, rows)})
    assert "path" in find_docker(ctx)[0].unknown
    ctx.os_name = "macos"
    assert "com.docker.docker" in (find_docker(ctx)[0].path or "")
    assert find_docker(Context(os_name="linux", home="/", run=FakeRunner())) == []


def test_parse_size() -> None:
    assert parse_size("512kB") == 512_000 and parse_size("0B") == 0 and parse_size("?") is None


def test_caches_with_clean_commands(ctx: Context, home: Path) -> None:
    write(home / ".cache" / "uv" / "wheels" / "a.whl", 20 * MB)
    write(home / ".npm" / "x", 100)  # too small to list
    store = find_caches(ctx)[0]
    assert [i.id for i in store.items] == ["uv"]
    assert store.items[0].details == {"clean": "uv cache clean", "same_volume": "true"}
    assert find_caches(Context(os_name="linux", home=str(home / "empty"), env={})) == []


def test_folder_stores_and_custom(ctx: Context, home: Path) -> None:
    write(home / ".lmstudio" / "models" / "m.gguf", 60 * MB)
    assert find_folder_store("lmstudio", "LM Studio", ["~/.lmstudio/models"])(ctx)[0].items[0].names == [
        "m.gguf"
    ]
    write(home / "mine" / "a.bin", 10)
    ctx.extra = [str(home / "mine"), str(home / "missing")]
    assert [s.title for s in find_custom(ctx)] == ["Folder mine"]


def test_candidates_and_blocked(ctx: Context, home: Path) -> None:
    ctx.env["X"] = "/x"
    assert candidates_for(ctx, ["$X/sub", "$UNSET/y", "~/a", "~/a", "$X"]) == [
        str(Path("/x", "sub")),
        str(home / "a"),
        "/x",
    ]
    assert blocked(str(home / "nothing" / "here")) is False
    assert set(all_finders()) >= {"ollama", "huggingface", "comfyui", "docker", "caches"}
    assert hashlib.sha256(b"").hexdigest()
