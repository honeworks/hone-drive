"""The dashboard's one background job at a time (a scan or a benchmark), with events for the page."""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from hone_drive.advice import advise
from hone_drive.benchmark import Options, Tick, bench, carry_over
from hone_drive.errors import HoneDriveError
from hone_drive.model import Run
from hone_drive.runs import list_runs, load_run, save_run
from hone_drive.scanner import scan

LOG = logging.getLogger("hone_drive")
Event = tuple[str, dict[str, Any]]


@dataclass
class Hub:
    """Fans events out to every open page."""

    listeners: list[queue.Queue[Event]] = field(default_factory=list[queue.Queue[Event]])
    lock: threading.Lock = field(default_factory=threading.Lock)

    def listen(self) -> queue.Queue[Event]:
        listener: queue.Queue[Event] = queue.Queue()
        with self.lock:
            self.listeners.append(listener)
        return listener

    def leave(self, listener: queue.Queue[Event]) -> None:
        with self.lock:
            if listener in self.listeners:
                self.listeners.remove(listener)

    def publish(self, kind: str, data: dict[str, Any]) -> None:
        with self.lock:
            for listener in self.listeners:
                listener.put((kind, data))


@dataclass
class Jobs:
    home: str = field(default_factory=lambda: str(Path.home()))
    hub: Hub = field(default_factory=Hub)
    scanner: Callable[..., Run] = scan
    state: dict[str, Any] = field(default_factory=lambda: {"running": False, "kind": None, "message": ""})
    stop: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None

    def busy(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start_scan(self, accounting: bool = False, duplicates: bool = False) -> None:
        self._start("scan", lambda: self._scan(accounting, duplicates))

    def start_bench(self, folders: list[str], tests: list[str], size: int) -> None:
        self._start("bench", lambda: self._bench(folders, tests, size))

    def request_stop(self) -> None:
        self.stop.set()
        self._say("stopping …")

    def _start(self, kind: str, work: Callable[[], Run]) -> None:
        if self.busy():
            raise HoneDriveError(f"a {self.state['kind']} is already running; wait for it or stop it")
        self.stop = threading.Event()
        self.state = {"running": True, "kind": kind, "message": "starting"}
        self.hub.publish("job", dict(self.state))

        def target() -> None:
            try:
                run = work()
                run.advice = advise(run, self.home)
                save_run(run)
                self.state = {"running": False, "kind": kind, "message": "done", "run": run.id}
                self.hub.publish("done", dict(self.state))
            except Exception as exc:  # reported to the page; the server keeps running
                LOG.exception("%s failed", kind)
                self.state = {"running": False, "kind": kind, "message": f"{type(exc).__name__}: {exc}"}
                self.hub.publish("failed", dict(self.state))

        self.thread = threading.Thread(target=target, name=f"hone-drive {kind}", daemon=True)
        self.thread.start()

    def _say(self, message: str) -> None:
        self.state["message"] = message
        self.hub.publish("progress", {"message": message})

    def _scan(self, accounting: bool, duplicates: bool) -> Run:
        run = self.scanner(accounting=accounting, duplicates=duplicates, progress=self._say)
        return carry_over(run, load_run("latest")) if list_runs() else run

    def _bench(self, folders: list[str], tests: list[str], size: int) -> Run:
        run = self._scan(False, False)

        def tick(t: Tick) -> None:
            self.hub.publish("tick", asdict(t))

        return bench(run, folders, tests, Options(size=size, on_tick=tick, stop=self.stop))
