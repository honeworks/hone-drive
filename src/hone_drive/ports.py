"""The one port hone-drive owns (design §3.1). Implementations: `probe_linux`, `probe_macos`,
`probe_windows`; the fake is `hone_drive.testing.FakeProbe`."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from hone_drive.model import Drive, MachineFacts, ProbeCommand, Volume


@runtime_checkable
class Probe(Protocol):
    """Read-only facts about this machine. Facts the OS can't give are `None` with a reason."""

    def machine(self) -> MachineFacts: ...
    def drives(self) -> list[Drive]: ...
    def volumes(self) -> list[Volume]: ...
    def commands(self) -> list[ProbeCommand]: ...
