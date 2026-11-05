"""The dashboard's shelf API (change 0003): a plan that changes nothing, then a move and a restore as jobs."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from hone_drive.dashboard import TOKEN_HEADER, Server, start, stop
from hone_drive.jobs import Jobs
from hone_drive.model import Run
from tests.integration.test_dashboard import request
from tests.shelf_machine import Machine, build

WAN = "comfyui:diffusion_models/wan.safetensors"


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Machine:
    return build(tmp_path, monkeypatch)


@pytest.fixture
def server(machine: Machine) -> Iterator[Server]:
    def scanner(**_kwargs: Any) -> Run:
        return machine.run()

    running = start(0, Jobs(home=str(machine.home), scanner=scanner))
    yield running
    stop(running)


def post(server: Server, path: str, body: dict[str, Any]) -> tuple[int, Any]:
    status, data, _ = request(server, "POST", path, body, {TOKEN_HEADER: server.token})
    return status, json.loads(data)


def finished(server: Server) -> dict[str, Any]:
    deadline = time.monotonic() + 20
    while server.jobs.busy() and time.monotonic() < deadline:
        time.sleep(0.05)
    return server.jobs.state


def test_plan_move_and_restore(server: Server, machine: Machine) -> None:
    status, planned = post(
        server, "/api/shelf/plan", {"action": "move", "models": [WAN], "to": str(machine.drive)}
    )
    assert status == 200 and planned["plans"][0]["models"][0]["refused"] is None
    assert not (machine.drive / "hone-drive").exists()  # a plan changes nothing
    assert post(server, "/api/shelf/move", {"models": [WAN], "to": str(machine.drive)})[0] == 202
    state = finished(server)
    assert state["kind"] == "shelf" and state["result"]["done"] == [WAN]
    view = json.loads(request(server, "GET", "/api/runs/latest")[1])
    row = next(r for r in view["models"]["rows"] if r["key"] == WAN)
    assert row["on_computer"] is False and row["where"] == "PNY RP60 PSSD" and row["connected"]
    assert view["shelf"]["drives"][0]["models"] == 1 and view["shelf"]["targets"]
    status, planned = post(server, "/api/shelf/plan", {"action": "restore", "models": [WAN]})
    assert status == 200 and planned["plans"][0]["models"][0]["refused"] is None
    post(server, "/api/shelf/restore", {"models": [WAN]})
    assert finished(server)["result"]["done"] == [WAN]
    view = json.loads(request(server, "GET", "/api/runs/latest")[1])
    row = next(r for r in view["models"]["rows"] if r["key"] == WAN)
    assert row["on_computer"] and row["on_drives"] == ["PNY RP60 PSSD"]


def test_remove_needs_a_yes_and_bad_actions_are_refused(server: Server, machine: Machine) -> None:
    post(server, "/api/shelf/move", {"models": [WAN], "to": str(machine.drive)})
    finished(server)
    post(server, "/api/shelf/remove", {"models": [WAN]})
    assert WAN in finished(server)["result"]["failed"]
    post(server, "/api/shelf/remove", {"models": [WAN], "only_copy": True})
    assert finished(server)["result"]["done"] == [WAN]
    assert post(server, "/api/shelf/explode", {"models": [WAN]})[0] == 400
    status, _, _ = request(server, "POST", "/api/shelf/move", {"models": [WAN]})  # no token
    assert status == 403
