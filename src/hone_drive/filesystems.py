"""What each filesystem allows, for AI work (design §4.3)."""

from __future__ import annotations

from hone_drive.model import Traits

# name -> (posix_permissions, symlinks, hard_links, large_files, journaled, native_on)
_TABLE: dict[str, tuple[bool, bool, bool, bool, bool, list[str]]] = {
    "ext4": (True, True, True, True, True, ["linux"]),
    "ext3": (True, True, True, True, True, ["linux"]),
    "xfs": (True, True, True, True, True, ["linux"]),
    "btrfs": (True, True, True, True, True, ["linux"]),
    "f2fs": (True, True, True, True, True, ["linux"]),
    "zfs": (True, True, True, True, True, ["linux"]),
    "bcachefs": (True, True, True, True, True, ["linux"]),
    "apfs": (True, True, True, True, True, ["macos"]),
    "hfs": (True, True, True, True, True, ["macos"]),
    "ntfs": (False, True, True, True, True, ["windows"]),
    "refs": (False, True, True, True, True, ["windows"]),
    "exfat": (False, False, False, True, False, ["linux", "macos", "windows"]),
    "vfat": (False, False, False, False, False, ["linux", "macos", "windows"]),
}
_ALIASES = {
    "fat32": "vfat",
    "fat": "vfat",
    "msdos": "vfat",
    "fat16": "vfat",
    "ntfs3": "ntfs",
    "fuseblk": "ntfs",
    "ntfs-3g": "ntfs",
    "hfsplus": "hfs",
    "hfs+": "hfs",
    "zfs_member": "zfs",
}


def normalise(name: str) -> str:
    key = name.strip().lower()
    return _ALIASES.get(key, key)


def traits(filesystem: str, os_name: str) -> Traits | None:
    """The traits of `filesystem` as seen from `os_name`; None for filesystems hone-drive doesn't know.

    NTFS keeps Windows permissions, not POSIX ones: a Linux or macOS service user can't own files there.
    """
    row = _TABLE.get(normalise(filesystem))
    if row is None:
        return None
    posix, symlinks, hard_links, large, journaled, native = row
    if normalise(filesystem) == "ntfs" and os_name == "windows":
        posix = True  # on Windows its own permission model serves the same purpose
    return Traits(
        posix_permissions=posix,
        symlinks=symlinks,
        hard_links=hard_links,
        large_files=large,
        journaled=journaled,
        native_on=native,
    )


def native_filesystem(os_name: str) -> str:
    return {"linux": "ext4", "macos": "APFS", "windows": "NTFS"}[os_name]
