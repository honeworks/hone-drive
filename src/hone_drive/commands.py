"""The commands advice shows (design §8.2): text for the user to read and run, never run by hone-drive.

Each function returns the lines for one OS. Moves always copy first, check, and leave deleting the old
copy as the last, separate step."""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

from hone_drive.model import Store, Volume

OSES = ("linux", "macos", "windows")


def join(os_name: str, *parts: str) -> str:
    return str(PureWindowsPath(*parts)) if os_name == "windows" else str(PurePosixPath(*parts))


def copy(os_name: str, source: str, target: str, sudo: bool = False) -> list[str]:
    if os_name == "windows":
        return [f'robocopy "{source}" "{target}" /E /COPY:DAT /R:1 /W:1']
    prefix = "sudo " if sudo else ""
    return [f'{prefix}mkdir -p "{target}"', f'{prefix}rsync -a --info=progress2 "{source}/" "{target}/"']


def delete_old(os_name: str, path: str, sudo: bool = False) -> list[str]:
    if os_name == "windows":
        return [f'# after checking the new location works:  Remove-Item -Recurse -Force "{path}"']
    return [f'# after checking the new location works:  {"sudo " if sudo else ""}rm -rf "{path}"']


def set_variable(os_name: str, name: str, value: str) -> list[str]:
    if os_name == "windows":
        return [f'setx {name} "{value}"', "# sign out and in again so programs see it"]
    if os_name == "macos":
        return [f'launchctl setenv {name} "{value}"', f"echo 'export {name}=\"{value}\"' >> ~/.zshrc"]
    return [f"echo 'export {name}=\"{value}\"' >> ~/.profile", "# log out and in again so programs see it"]


def move_store(store: Store, target: Volume, os_name: str, home: str) -> list[str]:
    """The commands that move `store` to `target` and point its tool at the new place."""
    if store.path is None:
        return []
    dest = join(
        os_name, target.mount, "docker-data" if store.id == "docker" else join(os_name, "models", store.id)
    )
    mover = MOVES.get(store.id, _move_plain)
    return mover(store, dest, os_name, home)


def _move_ollama(store: Store, dest: str, os_name: str, home: str) -> list[str]:
    path = store.path or ""
    service = os_name == "linux" and not path.startswith(home)
    if service:
        return [
            "sudo systemctl stop ollama",
            *copy(os_name, path, dest, sudo=True),
            f'sudo chown -R ollama:ollama "{dest}"',
            "sudo systemctl edit ollama   # add the two lines below, save and close",
            "#   [Service]",
            f'#   Environment="OLLAMA_MODELS={dest}"',
            "sudo systemctl daemon-reload && sudo systemctl start ollama && ollama list",
            *delete_old(os_name, path, sudo=True),
        ]
    quit_app = {
        "linux": "pkill ollama",
        "macos": "osascript -e 'quit app \"Ollama\"'",
        "windows": "Stop-Process -Name ollama*",
    }[os_name]
    return [
        quit_app,
        *copy(os_name, path, dest),
        *set_variable(os_name, "OLLAMA_MODELS", dest),
        "# start Ollama again, then check:  ollama list",
        *delete_old(os_name, path),
    ]


def _move_huggingface(store: Store, dest: str, os_name: str, home: str) -> list[str]:
    path = store.path or ""
    hf_home = dest
    return [
        *copy(os_name, path, join(os_name, hf_home, "hub")),
        *set_variable(os_name, "HF_HOME", hf_home),
        *delete_old(os_name, path),
    ]


def _move_comfyui(store: Store, dest: str, os_name: str, home: str) -> list[str]:
    path = store.path or ""
    install = str(PureWindowsPath(path).parent) if os_name == "windows" else str(PurePosixPath(path).parent)
    yaml = join(os_name, install, "extra_model_paths.yaml")
    return [
        *copy(os_name, path, dest),
        f"# then add this to {yaml} (create it if it is missing) and restart ComfyUI:",
        "#   external:",
        f"#     base_path: {dest}",
        *[
            f"#     {folder}: {folder}/"
            for folder in (
                "checkpoints",
                "diffusion_models",
                "unet",
                "text_encoders",
                "clip",
                "vae",
                "loras",
                "controlnet",
                "upscale_models",
            )
        ],
        *delete_old(os_name, path),
    ]


def _move_docker(store: Store, dest: str, os_name: str, home: str) -> list[str]:
    if os_name != "linux":
        return [
            "# Docker Desktop: Settings → Resources → Advanced → Disk image location: choose a folder on "
            f"{dest.rsplit('models', 1)[0]}"
        ]
    return [
        "sudo systemctl stop docker docker.socket containerd",
        *copy(os_name, "/var/lib/docker", join(os_name, dest, "docker"), sudo=True),
        *copy(os_name, "/var/lib/containerd", join(os_name, dest, "containerd"), sudo=True),
        f'# in /etc/docker/daemon.json:  {{"data-root": "{join(os_name, dest, "docker")}"}}',
        f'# in /etc/containerd/config.toml:  root = "{join(os_name, dest, "containerd")}"',
        "sudo systemctl start containerd docker && docker system df",
    ]


def _move_lmstudio(store: Store, dest: str, os_name: str, home: str) -> list[str]:
    return [
        *copy(os_name, store.path or "", dest),
        "# LM Studio → My Models → the folder icon → choose the new folder",
        *delete_old(os_name, store.path or ""),
    ]


def _move_llamacpp(store: Store, dest: str, os_name: str, home: str) -> list[str]:
    return [
        *copy(os_name, store.path or "", dest),
        *set_variable(os_name, "LLAMA_CACHE", dest),
        *delete_old(os_name, store.path or ""),
    ]


def _move_plain(store: Store, dest: str, os_name: str, home: str) -> list[str]:
    return [*copy(os_name, store.path or "", dest), *delete_old(os_name, store.path or "")]


MOVES = {
    "ollama": _move_ollama,
    "huggingface": _move_huggingface,
    "comfyui": _move_comfyui,
    "docker": _move_docker,
    "lmstudio": _move_lmstudio,
    "llamacpp": _move_llamacpp,
}


def clean_cache(command: str, path: str, os_name: str) -> list[str]:
    """A cache's clean command for `os_name`; `rm -rf` becomes `Remove-Item` on Windows."""
    if command.startswith("rm -rf"):
        return [f'Remove-Item -Recurse -Force "{path}"'] if os_name == "windows" else [f'rm -rf "{path}"']
    if command.startswith("sudo ") and os_name == "windows":
        return []
    return [command]


def mount_at_boot(volume: Volume, os_name: str) -> list[str]:
    """Linux: an `/etc/fstab` line with `nofail`, so the machine still boots without the drive."""
    if os_name != "linux":
        return []
    source = f"UUID={volume.uuid}" if volume.uuid else volume.device
    target = "/mnt/" + (volume.label or "models").replace(" ", "-").lower()
    options = "defaults,nofail,x-systemd.device-timeout=10"
    if volume.filesystem in ("exfat", "vfat", "ntfs", "ntfs3"):
        options += ",uid=1000,gid=1000"
    return [
        f'sudo mkdir -p "{target}"',
        f"echo '{source} {target} {volume.filesystem} {options} 0 2' | sudo tee -a /etc/fstab",
        f'sudo umount "{volume.mount}"; sudo mount -a && findmnt "{target}"',
    ]


def reformat(volume: Volume, os_name: str) -> list[str]:
    label = (volume.label or "Models").replace(" ", "")[:11]
    if os_name == "linux":
        return [
            "# ERASES THE DRIVE: copy anything you need off it first",
            f'sudo umount "{volume.mount}"',
            f"sudo mkfs.ext4 -L {label} {volume.device}",
        ]
    if os_name == "macos":
        return [
            "# ERASES THE DRIVE: copy anything you need off it first",
            f"diskutil eraseVolume APFS {label} {volume.device}",
        ]
    return [
        "# ERASES THE DRIVE: copy anything you need off it first",
        f"Format-Volume -DriveLetter {volume.mount[:1]} -FileSystem NTFS -NewFileSystemLabel {label}",
    ]
