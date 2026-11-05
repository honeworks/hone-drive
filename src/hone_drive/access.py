"""Can hone-drive read, write and remove a tool's files on this machine? (change 0003, "Checking access per
system"). The check tries it instead of assuming it from the OS, and when it fails it builds the command
that fixes it here, from what it found: the folder's owner, this user, the tools installed. hone-drive
shows the command and never runs it (AGENTS.md rule 7); nothing runs as administrator.

Like `directio`, this module asks the OS about users and permissions, so it has per-OS code."""

from __future__ import annotations

import contextlib
import getpass
import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

Which = Callable[[str], str | None]
PROBE = ".hone-drive-check-"
ACL_PACKAGE = {"pacman": "sudo pacman -S acl", "apt": "sudo apt install acl", "dnf": "sudo dnf install acl"}
MAC_READ = "read,list,search,readattr,readextattr,file_inherit,directory_inherit"
MAC_RIGHTS = (
    "read,write,delete,add_file,add_subdirectory,list,search,readattr,writeattr,readextattr,writeextattr,"
    "file_inherit,directory_inherit"
)


@dataclass
class Access:
    ok: bool
    problems: list[str] = field(default_factory=list[str])
    fix: list[str] = field(default_factory=list[str])  # commands for the user to run; never run here


def check(
    folders: list[str],
    files: list[str],
    os_name: str,
    *,
    user: str | None = None,
    which: Which = shutil.which,
    write: bool = True,
) -> Access:
    """Try to list, read and (with `write`) write in `folders` and to read `files`; the fix for this system
    when it fails. A folder that does not exist yet must be creatable in its nearest existing parent."""
    problems: list[str] = []
    locked: list[str] = []
    for path in folders:
        existing = _existing(path)
        if not write and not os.access(existing, os.R_OK | os.X_OK):
            problems.append(f"can't read {existing}")
            locked.append(_outermost_locked(existing))
        elif write and not _can_write(existing):
            problems.append(f"can't change files in {existing}")
            locked.append(_outermost_locked(existing))
    for path in files:
        if not os.access(path, os.R_OK):
            problems.append(f"can't read {path}")
            locked.append(_outermost_locked(os.path.dirname(path)))
    if not problems:
        return Access(ok=True)
    problems = list(dict.fromkeys(problems))  # several folders inside one locked folder say it once
    top = min(locked, key=len)
    fix = fix_for(top, os_name, user or getpass.getuser(), which, write)
    return Access(ok=False, problems=problems, fix=fix)


def fix_for(top: str, os_name: str, user: str, which: Which = shutil.which, write: bool = True) -> list[str]:
    """The command that lets `user` read (and with `write`, change) `top`, also files added there later, on
    this system."""
    if os_name == "windows":
        rights = "M" if write else "RX"
        return [f'icacls "{top}" /grant "{user}:(OI)(CI){rights}" /T   # in an administrator PowerShell']
    if os_name == "macos":
        return [f'sudo chmod -R +a "user:{user} allow {MAC_RIGHTS if write else MAC_READ}" "{top}"']
    owner = owner_of(top)
    mode = "rwX" if write else "rX"
    if owner == user:
        return [f'chmod -R u+{mode} "{top}"']
    keep = f",d:u:{owner}:rwX" if owner and write else ""  # the owner keeps reading files written for it
    acl = f"sudo setfacl -R -m u:{user}:{mode},d:u:{user}:{mode}{keep}"
    lines = [f'{acl} "{top}"', f'# to undo it: sudo setfacl -R -x u:{user},d:u:{user} "{top}"']
    if which("setfacl") is None:
        install = next((cmd for tool, cmd in ACL_PACKAGE.items() if which(tool)), "install the acl package")
        lines.insert(0, install)
    return lines


def owner_of(path: str) -> str | None:
    """The user name owning `path` (POSIX); None when the OS can't say."""
    if sys.platform == "win32":
        return None
    try:
        import pwd  # noqa: PLC0415 - POSIX only

        return pwd.getpwuid(os.stat(path).st_uid).pw_name
    except (KeyError, OSError):
        return None


def _existing(path: str) -> str:
    while not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return path


def _can_write(folder: str) -> bool:
    """Create and remove a test file: what the OS really allows, not what the mode bits suggest."""
    if not os.access(folder, os.R_OK | os.X_OK):
        return False
    probe = os.path.join(folder, f"{PROBE}{os.getpid()}")
    try:
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("hone-drive checks it can write here; this file is removed at once")
    except OSError:
        return False
    with contextlib.suppress(OSError):
        os.remove(probe)
    return True


def _outermost_locked(path: str) -> str:
    """The outermost folder on the way to `path` this user can't open or change (`/usr/share/ollama` for a
    blob in `/usr/share/ollama/.ollama/models/blobs`), so one command covers everything below it."""
    path = _existing(path)
    chain = [path]
    while (parent := os.path.dirname(chain[-1])) != chain[-1]:
        chain.append(parent)
    for folder in reversed(chain):
        if not os.access(folder, os.R_OK | os.X_OK) or (folder == path and not os.access(folder, os.W_OK)):
            return folder
    return path


def held_elsewhere(path: str) -> bool:
    """Windows: another program has `path` open (it can't be opened without sharing). Other systems don't
    lock files this way; `shelf_busy.look()` finds their open files instead."""
    if sys.platform != "win32":
        return False
    return _sharing_violation(path)  # pragma: no cover - runs on Windows CI only


def _sharing_violation(path: str) -> bool:  # pragma: no cover - runs on Windows CI only
    import ctypes  # noqa: PLC0415

    generic_read, open_existing, sharing_violation = 0x80000000, 3, 32
    kernel32: Any = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)  # noqa: B009 - Windows only
    kernel32.CreateFileW.restype = ctypes.c_void_p
    handle = kernel32.CreateFileW(ctypes.c_wchar_p(path), generic_read, 0, None, open_existing, 0, None)
    if handle is None or handle == ctypes.c_void_p(-1).value:
        return int(getattr(ctypes, "get_last_error")()) == sharing_violation  # noqa: B009
    kernel32.CloseHandle(ctypes.c_void_p(handle))
    return False
