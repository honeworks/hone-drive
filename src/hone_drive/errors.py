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
