"""How probes touch the machine: commands with timeouts and read-only file access (design §3.1).

Probes take a `Runner` and a `Files` so the tests can replay what a real machine answered
(`hone_drive.testing.RecordedSystem`) on any OS."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from hone_drive.model import ProbeCommand

TIMEOUT_S = 10.0


@dataclass(frozen=True)
class Output:
    """What a command printed; `code` is None when it could not start or timed out (`error` says which)."""

    code: int | None
    stdout: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.code == 0


class Runner(Protocol):
    def __call__(self, args: list[str], timeout: float = TIMEOUT_S) -> Output: ...


class Files(Protocol):
    """Read-only access to text files and folders (sysfs, /proc, config files)."""

    def read(self, path: str) -> str | None: ...
    def realpath(self, path: str) -> str: ...
    def listdir(self, path: str) -> list[str]: ...


@dataclass
class SubprocessRunner:
    """Runs a command as an argument list, never through a shell, and keeps a log of what ran."""

    log: list[ProbeCommand] = field(default_factory=list[ProbeCommand])

    def __call__(self, args: list[str], timeout: float = TIMEOUT_S) -> Output:
        start = time.monotonic()
        env = {**os.environ, "LC_ALL": "C", "LANG": "C"}  # numbers in C locale (history §2)
        try:
            done = subprocess.run(  # noqa: S603 - argument list, no shell
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                check=False,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError:
            out = Output(None, "", f"{args[0]} is not installed")
        except subprocess.TimeoutExpired:
            out = Output(None, "", f"{args[0]} did not answer within {timeout:.0f} s")
        except OSError as exc:
            out = Output(None, "", f"{args[0]} could not start: {exc}")
        else:
            out = Output(done.returncode, done.stdout, done.stderr.strip() or None)
        self.log.append(
            ProbeCommand(args=args, exit_code=out.code, seconds=round(time.monotonic() - start, 3))
        )
        return out


class LocalFiles:
    """The real filesystem."""

    def read(self, path: str) -> str | None:
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def realpath(self, path: str) -> str:
        return os.path.realpath(path)

    def listdir(self, path: str) -> list[str]:
        try:
            return sorted(os.listdir(path))
        except OSError:
            return []


def read_value(files: Files, path: str) -> str | None:
    """A one-line sysfs value, stripped; None when missing or empty."""
    text = files.read(path)
    return text.strip() or None if text is not None else None
