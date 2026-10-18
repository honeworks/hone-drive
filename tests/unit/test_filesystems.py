from hone_drive.filesystems import native_filesystem, normalise, traits


def test_exfat_lacks_what_services_and_caches_need() -> None:
    exfat = traits("exfat", "linux")
    assert exfat is not None
    assert not exfat.posix_permissions and not exfat.hard_links and not exfat.symlinks and exfat.large_files


def test_ntfs_permissions_depend_on_the_os() -> None:
    assert traits("ntfs3", "linux").posix_permissions is False  # type: ignore[union-attr]
    assert traits("NTFS", "windows").posix_permissions is True  # type: ignore[union-attr]


def test_unknown_filesystem_and_aliases() -> None:
    assert traits("weirdfs", "linux") is None
    assert normalise("FAT32") == "vfat"
    assert traits("vfat", "linux").large_files is False  # type: ignore[union-attr]
    assert native_filesystem("macos") == "APFS"
