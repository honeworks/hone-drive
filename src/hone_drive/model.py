"""The data model (design §2): what a scan finds, what a benchmark measures, what advice says.

Every model has `unknown`: a fact the OS can't provide is `None` here with its reason in `unknown`, keyed
by the field's name (design §2, AGENTS.md rule 5)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RUN_FORMAT = 1  # the run JSON's format version (design §9; change 0001 "Migration and compatibility")

Bus = Literal["pcie", "sata", "usb", "thunderbolt", "mmc", "virtual", "other"]
DriveKind = Literal["nvme", "sata-ssd", "hdd", "usb-ssd", "usb-hdd", "sd-card", "virtual", "other"]
Severity = Literal["info", "suggest", "warn"]
CacheBypass = Literal["direct", "fallback", "possibly-cached"]
TestName = Literal["seq-write", "seq-read", "sustained-write", "random-read", "model-read"]


class Facts(BaseModel):
    """Base of every model: strict fields, and the reasons for the facts that are `None`."""

    model_config = ConfigDict(extra="forbid")
    unknown: dict[str, str] = Field(default_factory=dict[str, str])


class Gpu(Facts):
    name: str
    memory_bytes: int | None = None


class MachineFacts(Facts):
    os: Literal["linux", "macos", "windows"]
    os_version: str
    hostname: str
    cpu: str | None = None
    ram_bytes: int | None = None
    gpus: list[Gpu] = Field(default_factory=list[Gpu])


class Link(Facts):
    """How a drive is attached: what it negotiated and, when known, what the port and device can do."""

    bus: Bus
    name: str | None = None  # "USB 3.2 Gen 2 (10 Gbps)", "PCIe 4.0 x4"
    negotiated_gbps: float | None = None
    negotiated_lanes: int | None = None
    port_max_gbps: float | None = None
    port_max_lanes: int | None = None
    device_max_gbps: float | None = None
    expected_gbs: float | None = None  # usable throughput of the negotiated link, GB/s (design §4.2)
    limited: bool | None = None  # negotiated below both the port's and the device's maximum (design §4.1)


class Drive(Facts):
    id: str  # stable within a machine: "nvme0n1", "disk4", "PhysicalDisk1"
    model: str | None = None
    serial: str | None = None
    size_bytes: int | None = None
    kind: DriveKind
    external: bool
    rotational: bool | None = None
    link: Link


class Traits(Facts):
    """What a filesystem allows (design §4.3)."""

    posix_permissions: bool
    symlinks: bool
    hard_links: bool
    large_files: bool
    journaled: bool
    native_on: list[str]


class Volume(Facts):
    mount: str
    device: str
    drive_id: str | None = None
    filesystem: str
    label: str | None = None
    uuid: str | None = None
    total_bytes: int
    used_bytes: int
    free_bytes: int  # available to an ordinary user, as `df` and the file managers show it
    read_only: bool = False
    system: bool = False  # holds the OS or the home folder
    at_boot: bool | None = None  # mounted at boot, before services start (advice A06)
    traits: Traits | None = None

    @property
    def used_fraction(self) -> float:
        return self.used_bytes / self.total_bytes if self.total_bytes else 0.0


class Item(Facts):
    """One thing in a store a person recognises: a model, an image set, a cache."""

    id: str
    names: list[str]  # every name it is known by (two Ollama tags for one model, design §5.2)
    bytes: int | None = None
    files: int | None = None
    path: str | None = None
    volume: str | None = None  # mount point of the volume holding it
    accessed: str | None = None  # ISO-8601 UTC of the newest access, when the filesystem keeps it
    details: dict[str, str] = Field(default_factory=dict[str, str])


class Store(Facts):
    id: str  # "ollama", "huggingface", "comfyui", ... (design §5.1)
    title: str
    path: str | None = None
    volume: str | None = None
    bytes: int | None = None
    reclaimable_bytes: int | None = None
    items: list[Item] = Field(default_factory=list[Item])
    unreadable: list[str] = Field(default_factory=list[str])
    partial: bool = False  # a time limit stopped the count (design §5.2)
    source: str = "files"  # "files", "ollama api", "docker" ...
    clean: dict[str, str] = Field(default_factory=dict[str, str])  # os -> command that cleans it


class Folder(Facts):
    path: str
    bytes: int


class Accounting(Facts):
    """Where a volume's used space went, as far as a walk could read it (design §5.4)."""

    volume: str
    readable_bytes: int
    unaccounted_bytes: int
    explained: list[Folder] = Field(
        default_factory=list[Folder]
    )  # unreadable, but sized by a server (Ollama, Docker)
    top_folders: list[Folder] = Field(default_factory=list[Folder])
    unreadable: list[str] = Field(default_factory=list[str])
    partial: bool = False
    hints: list[str] = Field(default_factory=list[str])
    measure_command: str | None = None


class Duplicate(Facts):
    sha256: str
    bytes: int
    paths: list[str]
    stores: list[str]


class Measurement(Facts):
    id: str  # "<test>@<volume>", unique within a run
    test: TestName
    volume: str
    bytes: int
    seconds: float
    gbs: float  # throughput, 10^9 bytes per second
    cache: CacheBypass
    spread: float | None = None  # relative spread of the per-chunk throughput (0.1 = ±10%)
    iops: float | None = None
    latency_ms: float | None = None
    curve: list[float] = Field(default_factory=list[float])  # GB/s per second, sustained-write only
    files: list[str] = Field(default_factory=list[str])
    label: str | None = None  # "link-bound", "drive-bound", "cache exhausted after 21 GB", ...
    partial: bool = False
    warnings: list[str] = Field(default_factory=list[str])
    measured: str | None = None  # ISO-8601 UTC; older than the run when carried over from an earlier run


class Gain(Facts):
    bytes_freed: int | None = None
    seconds_saved_per_load: float | None = None


class Advice(Facts):
    rule: str
    severity: Severity
    title: str
    why: list[str]
    cites: list[str] = Field(default_factory=list[str])  # measurement ids and facts it relies on
    gain: Gain = Field(default_factory=Gain)
    commands: dict[str, list[str]] = Field(default_factory=dict[str, list[str]])  # os -> lines
    risks: list[str] = Field(default_factory=list[str])


class ProbeCommand(Facts):
    """A command a probe ran, so a user can repeat it (design §3.1)."""

    args: list[str]
    exit_code: int | None
    seconds: float


class Run(Facts):
    format: int = RUN_FORMAT
    id: str
    created: str  # ISO-8601 UTC
    version: str
    machine: MachineFacts
    drives: list[Drive] = Field(default_factory=list[Drive])
    volumes: list[Volume] = Field(default_factory=list[Volume])
    stores: list[Store] = Field(default_factory=list[Store])
    accounting: list[Accounting] = Field(default_factory=list[Accounting])
    duplicates: list[Duplicate] = Field(default_factory=list[Duplicate])
    measurements: list[Measurement] = Field(default_factory=list[Measurement])
    advice: list[Advice] = Field(default_factory=list[Advice])
    commands: list[ProbeCommand] = Field(default_factory=list[ProbeCommand])

    def volume(self, mount: str) -> Volume | None:
        return next((v for v in self.volumes if v.mount == mount), None)

    def drive(self, drive_id: str | None) -> Drive | None:
        return next((d for d in self.drives if d.id == drive_id), None)
