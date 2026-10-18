"""The dashboard server on a free local port: pages, API, events and the refusals of design §10."""

from __future__ import annotations

import http.client
import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from hone_drive.dashboard import TOKEN_HEADER, Server, start, stop
from hone_drive.jobs import Jobs
from hone_drive.model import Run
from hone_drive.runs import list_runs, save_run
from hone_drive.scanner import scan
from hone_drive.testing import reference_probe
from tests.unit.test_advice import reference_run


def fake_scan(**kwargs: Any) -> Run:
    progress = kwargs.get("progress")
    if progress is not None:
        progress("machine and drives")
    return scan(probe=reference_probe(), finders={})


@pytest.fixture
def server(data_home: Path) -> Iterator[Server]:
    running = start(0, Jobs(home="/home/user", scanner=fake_scan))
    yield running
    stop(running)


def request(
    server: Server,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes, dict[str, str]]:
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
    sent = {"Host": f"127.0.0.1:{server.server_address[1]}", **(headers or {})}
    payload = json.dumps(body).encode() if body is not None else None
    if payload is not None:
        sent.setdefault("Content-Type", "application/json")
    conn.request(method, path, body=payload, headers=sent)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response.status, data, dict(response.getheaders())


def wait_for_runs(count: int) -> None:
    deadline = time.monotonic() + 10
    while len(list_runs()) < count and time.monotonic() < deadline:
        time.sleep(0.05)


def test_page_carries_its_token_and_a_strict_csp(server: Server) -> None:
    status, body, headers = request(server, "GET", "/")
    assert status == 200 and server.token.encode() in body and b"{{TOKEN}}" not in body
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    assert request(server, "GET", "/app.js")[2]["Content-Type"].startswith("text/javascript")
    assert request(server, "GET", "/nothing/here")[0] == 200  # the page handles its own routes


def test_refusals(server: Server) -> None:
    assert request(server, "GET", "/api/runs", headers={"Host": "evil.example"})[0] == 403
    assert request(server, "POST", "/api/scan", {})[0] == 403  # no token
    token = {TOKEN_HEADER: server.token}
    assert request(server, "POST", "/api/scan", {}, {**token, "Origin": "http://evil.example"})[0] == 403
    assert request(server, "POST", "/api/scan", {}, {**token, "Content-Type": "text/plain"})[0] == 415
    assert request(server, "POST", "/api/nope", {}, token)[0] == 404
    assert request(server, "GET", "/api/runs/missing")[0] == 404
    assert request(server, "GET", "/api/compare")[0] == 400


def test_scan_through_the_api_with_events(server: Server) -> None:
    events: list[bytes] = []

    def read_events() -> None:
        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
        conn.request("GET", "/api/events", headers={"Host": "127.0.0.1"})
        response = conn.getresponse()
        while not any(b"event: done" in e for e in events):
            line = response.fp.readline()
            if not line:
                break
            events.append(line)
        conn.close()

    reader = threading.Thread(target=read_events, daemon=True)
    reader.start()
    time.sleep(0.3)
    status, body, _ = request(server, "POST", "/api/scan", {}, {TOKEN_HEADER: server.token})
    assert status == 202 and json.loads(body)["running"] is True
    wait_for_runs(1)
    reader.join(5)
    assert any(b"event: progress" in e for e in events) and any(b"event: done" in e for e in events)
    status, body, _ = request(server, "GET", "/api/runs/latest")
    view = json.loads(body)
    assert status == 200 and {"run", "headline", "plan", "drives", "volumes", "models", "biggest"} <= set(
        view
    )
    tasks = [t for step in view["plan"]["steps"] for t in step["tasks"]]
    assert any(t["rule"] == "A03" for t in tasks)


def test_run_view_compare_and_report(server: Server) -> None:
    first = reference_run()
    first.id = "20261018T000000Z-aaaaaa"
    save_run(first)
    second = reference_run()
    second.id = "20261018T010000Z-bbbbbb"
    second.drives[1].link.negotiated_gbps = 20.0
    save_run(second)
    view = json.loads(request(server, "GET", f"/api/runs/{second.id}")[1])
    root = next(v for v in view["volumes"] if v["mount"] == "/")
    assert [s["kind"] for s in root["segments"]][:2] == ["ollama", "comfyui"]
    gemma = next(m for m in view["models"]["rows"] if m["names"] == ["gemma4:31b"])
    assert set(gemma["loads"]) == {"/", "/run/media/user/PNYRP60PSSD"}
    assert view["headline"]["title"] == "Internal drive is 89% full"
    assert [s["key"] for s in view["plan"]["steps"]] == ["clean", "prepare", "move", "speed"]
    pny = next(d for d in view["drives"] if d["external"])
    assert pny["name"] == "PNY RP60 PSSD" and pny["connection"]["tone"] == "warn"
    assert pny["speed"]["read"] == 0.97 and pny["test_folder"] == "/run/media/user/PNYRP60PSSD"
    changes = json.loads(request(server, "GET", f"/api/compare?a={first.id}&b={second.id}")[1])["changes"]
    assert changes[0]["kind"] == "link"
    status, text, headers = request(server, "GET", f"/api/report/{second.id}?format=md")
    assert status == 200 and headers["Content-Type"].startswith("text/markdown") and b"## Drives" in text


def test_bench_and_stop_through_the_api(
    server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[tuple[list[str], list[str], int]] = []
    monkeypatch.setattr(server.jobs, "start_bench", lambda f, t, s: started.append((f, t, s)))
    token = {TOKEN_HEADER: server.token}
    assert (
        request(server, "POST", "/api/bench", {"folders": ["~"], "tests": ["seq"], "size": "1G"}, token)[0]
        == 202
    )
    assert started == [([str(Path.home())], ["seq"], 1024**3)]
    assert request(server, "POST", "/api/stop", {}, token)[0] == 202
    assert request(server, "POST", "/api/bench", {"tests": ["seq"]}, token)[0] == 400
