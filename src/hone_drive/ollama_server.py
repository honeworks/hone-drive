"""Bringing an Ollama model back through Ollama's own server (change 0003, decision D-024), for a model folder
this user may read but not write: Ollama's service on Linux makes its folders with mode 0755, so no
permission rule lets another user write into the ones it makes later.

hone-drive serves the drive's copy as a registry on 127.0.0.1 for the length of one pull: Ollama pulls
the model from it (checking every blob's SHA-256 itself and writing its own files), copies it to its
original names, and drops the temporary name. The manifest Ollama keeps is byte for byte the one on the
drive, so the model is the one that was moved."""

from __future__ import annotations

import contextlib
import errno
import json
import os
import re
import socket
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

from hone_drive import shelf_catalog as catalog
from hone_drive.errors import ShelfDamaged
from hone_drive.shelf_catalog import ShelfModel
from hone_drive.store_ollama import read_manifests, server
from hone_drive.stores import Context

MANIFEST_TYPE = "application/vnd.docker.distribution.manifest.v2+json"
CHUNK = 1024 * 1024
RANGE = re.compile(r"bytes=(\d+)-(\d*)")


def answers(ctx: Context) -> bool:
    return ctx.fetch(f"{server(ctx.env)}/api/version") is not None


def drive_manifest(entry: ShelfModel, mount: str) -> bytes:
    """The model's manifest as the drive holds it: the registry's own bytes where Ollama kept them (the blob
    a manifests-v2 link points to, or a manifests-v2 file where the OS made no link), else the
    manifests/ file."""
    linked = [x.target.rsplit("/", 1)[-1] for x in entry.links if x.rel.startswith("manifests-v2/")]
    files = [f.rel for f in entry.files if f.rel.startswith("manifests")]
    rel = (
        f"blobs/{linked[0]}" if linked else sorted(files, key=lambda r: not r.startswith("manifests-v2/"))[0]
    )
    with open(catalog.drive_file(mount, entry, rel), "rb") as handle:
        return handle.read()


def ollama_here(entry: ShelfModel, mount: str) -> bool:
    """Ollama has every name of the model on the computer, each listing the drive copy's config and
    layers, and every one of those blobs is there."""
    try:
        data = cast(dict[str, Any], json.loads(drive_manifest(entry, mount)))
    except (OSError, ValueError, IndexError):
        return False
    config = cast(dict[str, Any], data.get("config") or {})
    layers = [config, *cast(list[dict[str, Any]], data.get("layers") or [])]
    digests = tuple(str(layer["digest"]) for layer in layers if layer.get("digest"))
    names = {m.name for m in read_manifests(entry.root) if m.digests == digests}
    blobs = [os.path.join(entry.root, "blobs", d.replace(":", "-", 1)) for d in digests]
    return set(entry.names) <= names and all(os.path.isfile(b) for b in blobs)


def bring_back(entry: ShelfModel, mount: str, ctx: Context) -> None:
    """Have Ollama pull the drive's copy, copy it to each original name and drop the temporary name;
    raises when Ollama refuses a step or the model isn't there afterwards."""
    if ollama_here(entry, mount):
        return
    registry = _Registry(drive_manifest(entry, mount), entry, mount)
    first = ([x.rel for x in entry.links] + [f.rel for f in entry.files if f.rel.startswith("manifests")])[0]
    _, _, namespace, model, tag = first.split("/")[-5:]
    temp = f"127.0.0.1:{registry.port}/{namespace}/{model}:{tag}"
    steps: list[tuple[str, str, dict[str, object]]] = [
        ("POST", "/api/pull", {"model": temp, "insecure": True, "stream": False}),
        *[("POST", "/api/copy", {"source": temp, "destination": name}) for name in entry.names],
        ("DELETE", "/api/delete", {"model": temp}),
    ]
    try:
        for method, path, body in steps:
            answer = ctx.send(method, f"{server(ctx.env)}{path}", body)
            if answer is None or b'"error"' in answer:
                raise ShelfDamaged(f"Ollama could not take back {entry.names[0]} ({path}): {answer!r}")
    finally:
        registry.close()
    if not ollama_here(entry, mount):
        raise ShelfDamaged(
            f"Ollama does not list {entry.names[0]} with the drive's layers after taking it back"
        )


class _Registry:
    """The smallest registry Ollama pulls from: the manifest by tag, blobs by digest, with ranges."""

    def __init__(self, manifest: bytes, entry: ShelfModel, mount: str) -> None:
        files = {f.rel: catalog.drive_file(mount, entry, f.rel) for f in entry.files}
        self.manifest = manifest
        blobs = {
            rel.split("/", 1)[1].replace("-", ":", 1): p
            for rel, p in files.items()
            if rel.startswith("blobs/")
        }
        handler = type("Handler", (_Handler,), {"manifest": self.manifest, "blobs": blobs})
        self.servers = _listen(handler)
        self.port = int(self.servers[0].server_address[1])
        for httpd in self.servers:
            threading.Thread(target=httpd.serve_forever, name="hone-drive registry", daemon=True).start()

    def close(self) -> None:
        for httpd in self.servers:
            httpd.shutdown()
            httpd.server_close()


class _Server6(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def _listen(handler: type[BaseHTTPRequestHandler]) -> list[ThreadingHTTPServer]:
    """The registry on 127.0.0.1 and, on the same port, on ::1, since `localhost` (where blobs are sent
    from) may be either; loopback only, never another interface."""
    for _ in range(20):
        four = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        four.daemon_threads = True
        try:
            six = _Server6(("::1", int(four.server_address[1])), handler)
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                four.server_close()
                continue
            return [four]  # no IPv6 loopback: localhost is 127.0.0.1 only
        six.daemon_threads = True
        return [four, six]
    raise OSError("no free port on both loopback addresses")


class _Handler(BaseHTTPRequestHandler):
    manifest: bytes = b""
    blobs: dict[str, str] = {}  # noqa: RUF012 - set per server by _Registry

    def log_message(self, format: str, *args: Any) -> None:
        """Quiet."""

    def do_HEAD(self) -> None:
        self._answer(head=True)

    def do_GET(self) -> None:
        self._answer(head=False)

    def _answer(self, head: bool) -> None:
        if self.path.rstrip("/") == "/v2":
            self._send(HTTPStatus.OK, b"{}", "application/json", head)
        elif "/manifests/" in self.path:
            self._send(HTTPStatus.OK, self.manifest, MANIFEST_TYPE, head)
        elif (blob := self.blobs.get(self.path.rsplit("/", 1)[-1])) is None:
            missing = json.dumps({"errors": [{"code": "BLOB_UNKNOWN"}]}).encode()
            self._send(HTTPStatus.NOT_FOUND, missing, "application/json", head)
        elif self.path.startswith("/v2/"):
            # a registry sends blobs from another host, and Ollama takes the Location of the first redirect
            # to another host name: the same server under its other local name
            self.send_response(HTTPStatus.TEMPORARY_REDIRECT)
            port = self.headers.get("Host", "").rsplit(":", 1)[-1]  # the port Ollama reached
            self.send_header("Location", f"http://localhost:{port}/direct/" + self.path.rsplit("/", 1)[-1])
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._blob(blob, head)

    def _send(self, status: int, body: bytes, kind: str, head: bool) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _blob(self, path: str, head: bool) -> None:
        size = os.path.getsize(path)
        start, end = 0, size - 1
        match = RANGE.match(self.headers.get("Range") or "")
        if match:
            start = int(match.group(1))
            end = min(int(match.group(2)), size - 1) if match.group(2) else size - 1
        self.send_response(HTTPStatus.PARTIAL_CONTENT if match else HTTPStatus.OK)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        if match:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if head:
            return
        with open(path, "rb") as handle, contextlib.suppress(BrokenPipeError, ConnectionResetError):
            handle.seek(start)
            left = end - start + 1
            while left > 0 and (chunk := handle.read(min(CHUNK, left))):
                self.wfile.write(chunk)
                left -= len(chunk)
