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
from collections.abc import Callable
from dataclasses import dataclass, field

Which = Callable[[str], str | None]
PROBE = ".hone-drive-check-"
ACL_PACKAGE = {"pacman": "sudo pacman -S acl", "apt": "sudo apt install acl", "dnf": "sudo dnf install acl"}
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
) -> Access:
    """Try to list, read and write in `folders` and to read `files`; the fix for this system when it fails.
    A folder that does not exist yet must be creatable in its nearest existing parent."""
    problems: list[str] = []
    locked: list[str] = []
    for path in folders:
        existing = _existing(path)
        if not _can_write(existing):
            problems.append(f"can't change files in {existing}")
            locked.append(_outermost_locked(existing))
    for path in files:
        if not os.access(path, os.R_OK):
            problems.append(f"can't read {path}")
            locked.append(_outermost_locked(os.path.dirname(path)))
    if not problems:
        return Access(ok=True)
    top = min(locked, key=len)
    return Access(ok=False, problems=problems, fix=fix_for(top, os_name, user or getpass.getuser(), which))


def fix_for(top: str, os_name: str, user: str, which: Which = shutil.which) -> list[str]:
    """The command that lets `user` read and write `top`, also files added there later, on this system."""
    if os_name == "windows":
        return [f'icacls "{top}" /grant "{user}:(OI)(CI)M" /T   # in an administrator PowerShell']
    if os_name == "macos":
        return [f'sudo chmod -R +a "user:{user} allow {MAC_RIGHTS}" "{top}"']
    owner = owner_of(top)
    if owner == user:
        return [f'chmod -R u+rwX "{top}"']
    acl = f"sudo setfacl -R -m u:{user}:rwX,d:u:{user}:rwX" + (f",d:u:{owner}:rwX" if owner else "")
    lines = [f'{acl} "{top}"', f'# to undo it: sudo setfacl -R -x u:{user},d:u:{user} "{top}"']
    if which("setfacl") is None:
        install = next((cmd for tool, cmd in ACL_PACKAGE.items() if which(tool)), "install the acl package")
        lines.insert(0, install)
    return lines


def owner_of(path: str) -> str | None:
    """The user name owning `path` (POSIX); None when the OS can't say."""
    try:
        import pwd  # noqa: PLC0415 - POSIX only

        return pwd.getpwuid(os.stat(path).st_uid).pw_name
    except (ImportError, KeyError, OSError):
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
