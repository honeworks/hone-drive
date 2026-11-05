"""Typed errors. Every message says what went wrong and what to do (CONTRIBUTING "Explicit failure")."""

from __future__ import annotations


class HoneDriveError(RuntimeError):
    """Base class of every hone-drive error."""


class NotFound(HoneDriveError):
    """A run, a volume or a file that does not exist."""


class UnsafeBenchmark(HoneDriveError):
    """A benchmark refused by the safety rules of design §6.3 (free space, read-only, not selected)."""


class BenchmarkBusy(HoneDriveError):
    """Another benchmark holds the machine's benchmark lock (design §6.3 rule 5)."""


class UnsupportedFormat(HoneDriveError):
    """A stored run written by a newer, incompatible version of hone-drive."""


class ShelfRefused(HoneDriveError):
    """A shelf action refused by the rules of change 0003: in use, no access, no room, the only copy."""


class ShelfBusy(HoneDriveError):
    """Another process (or computer) is changing this drive's shelf."""


class ShelfDamaged(HoneDriveError):
    """A copy whose bytes don't match its SHA-256: nothing was removed, the bad copy is not kept."""


class Stopped(HoneDriveError):
    """The user stopped a shelf job; what is complete stays, a `.part` file is removed."""
