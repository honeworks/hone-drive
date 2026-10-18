from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from hone_drive.errors import HoneDriveError
from hone_drive.jobs import Jobs
from hone_drive.model import Run
from hone_drive.runs import load_run, save_run
from tests.unit.test_benchmark import local_run


def wait(jobs: Jobs) -> None:
    deadline = time.monotonic() + 20
    while jobs.busy() and time.monotonic() < deadline:
        time.sleep(0.02)


def test_bench_job_keeps_earlier_measurements_and_saves_advice(tmp_path: Path) -> None:
    earlier = local_run(tmp_path)
    earlier.id = "20200101T000000Z-aaaaaa"
    from hone_drive.model import Measurement

    earlier.measurements = [
        Measurement(
            id="seq-read@x",
            test="seq-read",
            volume=earlier.volumes[0].mount,
            bytes=1,
            seconds=1,
            gbs=1.0,
            cache="direct",
        )
    ]
    save_run(earlier)

    def scanner(**kwargs: Any) -> Run:
        return local_run(tmp_path)

    jobs = Jobs(home=str(tmp_path), scanner=scanner)
    listener = jobs.hub.listen()
    jobs.start_bench([str(tmp_path)], ["seq"], 16 * 1024**2)
    with pytest.raises(HoneDriveError, match="already running"):
        jobs.start_scan()
    wait(jobs)
    assert jobs.state["message"] == "done"
    run = load_run("latest")
    assert {m.test for m in run.measurements} >= {"seq-write", "seq-read"}
    events = []
    while not listener.empty():
        events.append(listener.get()[0])
    assert {"job", "tick", "done"} <= set(events)
    jobs.hub.leave(listener)
    jobs.hub.leave(listener)


def test_a_failing_job_reports_and_the_server_keeps_going() -> None:
    def broken(**kwargs: Any) -> Run:
        raise RuntimeError("probe exploded")

    jobs = Jobs(home="/home/user", scanner=broken)
    jobs.start_scan(accounting=True)
    wait(jobs)
    assert jobs.state == {"running": False, "kind": "scan", "message": "RuntimeError: probe exploded"}
    jobs.request_stop()
    assert jobs.stop.is_set()
