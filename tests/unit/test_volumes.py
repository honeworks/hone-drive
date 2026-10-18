from hone_drive.volumes import Partition, list_volumes, mount_of


def test_skips_pseudo_and_duplicate_mounts() -> None:
    parts = [
        Partition("/dev/sda1", "/data", "ext4", "rw"),
        Partition("/dev/sda1", "/data/bind", "ext4", "rw"),
        Partition("tmpfs", "/tmp", "tmpfs", "rw"),
        Partition("/dev/loop3", "/snap/x", "squashfs", "ro"),
        Partition("/dev/sdb1", "/ro", "ext4", "ro,noatime"),
        Partition("/dev/sdc1", "/gone", "ext4", "rw"),
    ]

    def usage(mount: str) -> tuple[int, int, int]:
        if mount == "/gone":
            raise OSError("no medium")
        return 100, 60, 30

    volumes = list_volumes("linux", lambda: parts, usage)
    assert [v.mount for v in volumes] == ["/data", "/ro"]
    assert volumes[1].read_only and not volumes[0].read_only
    assert volumes[0].used_fraction == 0.6


def test_mount_of() -> None:
    mounts = ["/", "/home", "/run/media/u/SSD"]
    assert mount_of("/home/u/x", mounts) == "/home"
    assert mount_of("/run/media/u/SSD", mounts) == "/run/media/u/SSD"
    assert mount_of("/usr", mounts) == "/"
    assert mount_of("/x", []) is None
