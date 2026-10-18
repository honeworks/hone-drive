"""Reading and writing past the operating system's cache (design §6.2). The only OS-specific code outside
the probes (AGENTS.md rule 4).

| OS      | direct                                               | fallback                          |
|---------|------------------------------------------------------|-----------------------------------|
| Linux   | `O_DIRECT`, page-aligned buffers                     | `posix_fadvise(DONTNEED)` per file |
| macOS   | `fcntl(F_NOCACHE)`                                   | none: `possibly-cached`           |
| Windows | `CreateFileW(FILE_FLAG_NO_BUFFERING | WRITE_THROUGH)` | none: `possibly-cached`           |
"""

from __future__ import annotations

import io
import mmap
import os
import sys
from dataclasses import dataclass
from typing import Any

from hone_drive.model import CacheBypass

ALIGN = 4096
F_NOCACHE = 48  # <sys/fcntl.h> on macOS; Python's fcntl module doesn't always name it


@dataclass
class Opened:
    file: io.FileIO
    cache: CacheBypass


def buffer(size: int) -> mmap.mmap:
    """An anonymous mapping: page aligned, as direct I/O needs on every OS."""
    return mmap.mmap(-1, max(ALIGN, size - size % ALIGN))


def open_file(path: str, write: bool) -> Opened:
    """Open `path` for direct I/O (creating it when writing), or the best fallback this OS has."""
    if sys.platform == "win32":
        return _open_windows(path, write)
    flags = (os.O_WRONLY | os.O_CREAT | os.O_TRUNC) if write else os.O_RDONLY
    direct = getattr(os, "O_DIRECT", 0)
    if direct:
        try:
            fd = os.open(path, flags | direct, 0o600)
            return Opened(_wrap(fd, write), "direct")
        except OSError:
            pass  # tmpfs and some FUSE filesystems refuse O_DIRECT
    fd = os.open(path, flags, 0o600)
    if sys.platform == "darwin":
        import fcntl  # noqa: PLC0415 - POSIX only

        try:
            fcntl.fcntl(fd, getattr(fcntl, "F_NOCACHE", F_NOCACHE), 1)
            return Opened(_wrap(fd, write), "direct")
        except OSError:
            return Opened(_wrap(fd, write), "possibly-cached")
    return Opened(_wrap(fd, write), "fallback" if hasattr(os, "posix_fadvise") else "possibly-cached")


def drop_cache(opened: Opened) -> None:
    """After a fallback write, ask the OS to forget the file's pages so a read comes from the drive."""
    if opened.cache == "fallback" and hasattr(os, "posix_fadvise"):
        fd = opened.file.fileno()
        os.fsync(fd)
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)


def _wrap(fd: int, write: bool) -> io.FileIO:
    return io.FileIO(fd, "wb" if write else "rb")


def _open_windows(path: str, write: bool) -> Opened:  # pragma: no cover - runs on Windows CI only
    import ctypes  # noqa: PLC0415
    import msvcrt  # noqa: PLC0415

    generic_read, generic_write = 0x80000000, 0x40000000
    no_buffering, write_through = 0x20000000, 0x80000000
    create_always, open_existing = 2, 3
    kernel32: Any = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)  # noqa: B009 - Windows only
    kernel32.CreateFileW.restype = ctypes.c_void_p
    handle: int | None = kernel32.CreateFileW(
        ctypes.c_wchar_p(path),
        generic_write if write else generic_read,
        1 | 2,  # share read and write
        None,
        create_always if write else open_existing,
        no_buffering | write_through,
        None,
    )
    binary: int = getattr(os, "O_BINARY", 0)
    if handle is None or handle == ctypes.c_void_p(-1).value:
        flags = (os.O_WRONLY | os.O_CREAT | os.O_TRUNC) if write else os.O_RDONLY
        return Opened(_wrap(os.open(path, flags | binary), write), "possibly-cached")
    fd: int = getattr(msvcrt, "open_osfhandle")(handle, 0 if write else os.O_RDONLY)  # noqa: B009
    return Opened(_wrap(fd, write), "direct")
