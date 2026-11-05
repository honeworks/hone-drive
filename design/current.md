# hone-drive: the design (0.1)

**Status:** accepted in [changes/0001](changes/0001-initial-design.md) on 2026-10-18; milestones M1 to M4
built the same day. Verified on the Linux reference machine; the macOS and Windows probes and direct I/O
are tested against hand-written fixtures and in CI, not yet on real Macs and PCs (decision D-012). The
model shelf (§17) was accepted in [changes/0003](changes/0003-model-shelf.md) and built on 2026-11-05;
checked with the real Ollama on the Linux reference machine.
Section numbers are stable; code comments will refer to them ("design §6.2"). The evidence behind the
defaults is in [history/0000-research.md](history/0000-research.md); smaller choices and the open
questions are in [decisions.md](decisions.md).

## 1. What it does

hone-drive is a local dashboard, a command line and a Python library that helps AI developers manage
storage. On Linux, macOS and Windows it:

1. **finds** the machine's drives and volumes: internal and external, the bus and the link each one
   negotiated against what it can do, the filesystem and how full it is (§3, §4);
2. **sizes** the places AI work fills: model stores (Ollama, Hugging Face, ComfyUI, LM Studio,
   llama.cpp), Docker, package and tool caches; finds byte-identical duplicates across stores; and
   accounts for the space it could not see (§5);
3. **measures** each drive with tests that bypass the page cache: sequential read and write, a sustained
   write, random reads, and reading real model files (§6);
4. **estimates** what that means in seconds: load times for the models on disk, per drive, and the time
   to copy a store from one drive to another (§7);
5. **advises**: ranked, checkable recommendations with their measured gain and the exact commands for
   this OS, which it shows but never runs (§8);
6. **keeps** each run so two runs can be compared (after switching ports, after freeing space) and
   exported as a report with private details removed (§9, §13);
7. **shelves** models when the user asks: copies them to a drive, proves each copy byte for byte, and only
   then removes them from the computer; brings them back the same way (§17, change
   [0003](changes/0003-model-shelf.md)).

### Goals

- Every number is measured on this machine or labelled as an estimate with its inputs.
- Nothing changes unless the user starts it. Outside hone-drive's own data folder, it writes only the
  benchmark's temporary file (always removed) and, when the user moves, brings back or removes models,
  the model files and the shelf's catalog (§17), each step proven before the next. Nothing needs
  administrator rights; nothing is formatted or mounted.
- The same facts on all three operating systems, with "unknown, and why" where an OS can't tell.
- Fast enough to open and glance at: the system scan finishes in seconds; long tests show progress and
  can be stopped.
- Useful without the dashboard: every screen's data comes from a public Python function and a CLI command
  with `--json`.

### Non-goals (0.1)

Moving anything but models on the user's request (§17), deleting anything else; formatting, partitioning
or mounting; raw block-device access; running
as administrator; network drives; repairing drives; telemetry or any network access (the dashboard works
offline); comparing results with other people's.

### Responsibilities

hone-drive owns the facts about this machine's storage and their presentation: probes, store finders,
benchmarks, estimates, advice rules, run history, the shelf and the dashboard. It depends on no other
honeworks package. Users own every action: advice writes commands for them to read and run, and the
shelf changes files only when they start it.

## 2. Concepts

| Concept | What it is |
|---|---|
| `Machine` | the OS, CPU, RAM, GPUs (name and memory, when known) and the drives and volumes |
| `Drive` | a physical device: model, size, kind (`nvme`, `sata-ssd`, `hdd`, `usb-ssd`, …), `internal` or `external`, its `Link` |
| `Link` | how a drive is attached: bus (`pcie`, `sata`, `usb`, `thunderbolt`, …) with the negotiated speed and lanes and, when known, what the port and the device can do |
| `Volume` | a mounted filesystem on a drive: mount point, filesystem, size, used, free, traits (§4.3) |
| `Store` | a known place AI work fills (an Ollama model folder, the Hugging Face cache, …) with its location, size and items (§5) |
| `Item` | one thing in a store a person recognises: a model, an image, a cache; its size and its files |
| `Measurement` | one benchmark result: test, volume, bytes, seconds, throughput, how the cache was bypassed, warnings |
| `Advice` | one recommendation: rule id, severity, title, why (measurements cited), gain, commands per OS |
| `Run` | one scan (and optional benchmark) of the machine at a moment, stored as JSON (§9) |

Facts that an OS cannot provide are `None` with a reason (`unknown: needs administrator rights`, `unknown:
Windows does not report USB link speed`), never guessed and never shown as zero.

## 3. Finding the system

### 3.1 The `Probe` port

One port owned by hone-drive, with one implementation per OS and a fake:

```python
class Probe(Protocol):
    def machine(self) -> MachineFacts: ...        # OS, CPU, RAM, GPUs
    def drives(self) -> list[Drive]: ...          # physical drives with their links
    def volumes(self) -> list[Volume]: ...        # mounted filesystems, mapped to drives
    def commands(self) -> list[ProbeCommand]: ... # the commands it ran, for the run (§9)
```

`probe_for(sys.platform)` picks `LinuxProbe`, `MacProbe` or `WindowsProbe`. Each runs only read-only
commands and files, never as administrator, each with a timeout, and records the commands it ran in the
run (§9) so a user can repeat them. Probes take their command runner and file reader as arguments, so the
tests replay a recorded machine on any OS (§14, decision D-012). Commands run in the C locale, so numbers
are never written with a decimal comma (history §2).

### 3.2 Where the facts come from

| Fact | Linux | macOS | Windows |
|---|---|---|---|
| volumes, usage | psutil | psutil | psutil |
| drive model, size, rotational, removable | `/sys/block/*` | `diskutil info -plist` | `Get-PhysicalDisk`, `Get-Disk` (PowerShell, JSON) |
| bus | the sysfs device path | `diskutil` `Protocol` | `Get-PhysicalDisk` `BusType` |
| USB link: negotiated speed, lanes | `/sys/bus/usb/devices/*/speed`, `rx_lanes`, `tx_lanes` | `system_profiler SPUSBHostDataType -json` (`SPUSBDataType` before macOS 15) | unknown in 0.1 (§16) |
| USB port capability | the root hub's `speed` and lanes | unknown in 0.1 | unknown in 0.1 |
| Thunderbolt / USB4 link | a PCIe drive sysfs marks `removable` is on Thunderbolt or USB4; its PCIe link as below | unknown in 0.1 | unknown in 0.1 |
| NVMe PCIe link | `/sys/class/nvme/*/device/current_link_speed`, `_width`, `max_link_*` | `system_profiler SPNVMeDataType -json` (link width and speed) | unknown in 0.1 |
| GPUs | `nvidia-smi`, `/sys/class/drm` | `system_profiler SPDisplaysDataType -json` | `Get-CimInstance Win32_VideoController`, `nvidia-smi` |
| SMART health | not read in 0.1 (§16, decision D-017) | same | same |

Every probe command is optional: a missing tool or a refusal gives `None` with the reason, and the rest of
the scan goes on.

## 4. Drives and links

### 4.1 Link speed and what limits it

A `Link` holds `negotiated_gbps`, `negotiated_lanes`, `port_max_gbps`, `port_max_lanes` and
`device_max_gbps` where known. A link is **limited** when it negotiated less than the port can do and the
device is not known to be the cap: less than both when the device reports its maximum, less than the
port when it doesn't (common: USB devices rarely say). The dashboard shows the gap ("10 of 20 Gbps, 1 of
2 lanes") and advice A03 (§8) names the likely causes in order: the cable, an adapter or hub, the port,
the drive's own mode; when the device's maximum is unknown, A03 adds that the drive may already be at its
limit.

### 4.2 Expected speeds

A small table maps a link to the throughput it can carry after protocol overhead, used to tell a drive
that is slow from a link that is slow (decision D-004):

| Link | Usable, roughly |
|---|---|
| USB 2.0 (480 Mbps) | 0.04 GB/s |
| USB 3.2 Gen 1 (5 Gbps) | 0.45 GB/s |
| USB 3.2 Gen 2 (10 Gbps) | 1.05 GB/s |
| USB 3.2 Gen 2x2 (20 Gbps) | 2.0 GB/s |
| USB4 / Thunderbolt 3–4 (40 Gbps), NVMe enclosure | 3.8 GB/s (enclosures often reach 2.8) |
| USB4 v2 (80 Gbps) | 7.0 GB/s |
| PCIe 3.0 x4 / 4.0 x4 / 5.0 x4 | 3.5 / 7 / 14 GB/s (0.9 of the lanes' rate) |
| SATA 1.5 / 3 / 6 Gbps | 0.15 / 0.28 / 0.55 GB/s |

A measured read within 15% of its link's figure is **link-bound**; well below it, **drive-bound**.

### 4.3 Filesystem traits

Each volume's filesystem is mapped to traits that matter for AI work:

| Trait | Why it matters | e.g. false on |
|---|---|---|
| `posix_permissions` | a model server running as its own user (Ollama on Linux) must own its files | exFAT, FAT32, NTFS on Linux/macOS |
| `symlinks` | moving a folder and leaving a link behind | FAT32, exFAT |
| `hard_links` | uv and pnpm caches link files into projects instead of copying | exFAT, FAT32 |
| `large_files` | model files over 4 GB | FAT32 |
| `native_on` | which OSes read and write it without extra drivers | ext4: Linux; APFS: macOS; NTFS: Windows; exFAT: all |
| `journaled` | survives being unplugged mid-write | exFAT, FAT32 |

### 4.4 Fill level

A volume's `used_fraction` is shown with the drive's kind. Above 85% on an internal SSD with a measured write
speed, advice A04 warns that writes slow down as the drive's fast cache shrinks, citing that measurement
(history §3); without a write measurement it says nothing.

## 5. Where the space goes

### 5.1 Known stores

A store finder is a plain function `find_<store>(env, home, os) -> list[Store]` in a dict keyed by store
id (CONTRIBUTING "Variants": a dict of functions, not a class hierarchy). 0.1 knows:

| Store id | Default locations (overridden by) | Items |
|---|---|---|
| `ollama` | Linux service `/usr/share/ollama/.ollama/models`, `/var/lib/ollama/.ollama/models`; user `~/.ollama/models`; macOS `~/.ollama/models`; Windows `%USERPROFILE%\.ollama\models` (`OLLAMA_MODELS`, also read from the systemd unit) | models from `manifests/`, sized by their unique blobs; when the folder belongs to the service's own user, from the local Ollama server's `/api/tags` (decision D-010) |
| `huggingface` | `~/.cache/huggingface/hub` (`HF_HUB_CACHE`, `HF_HOME`) | repos (`models--org--name`), sized by unique blobs |
| `comfyui` | folders whose name contains "comfy" with a `main.py` and a `models/` folder, up to three levels under the home folder, plus their `extra_model_paths.yaml` entries | model files by subfolder (`checkpoints`, `diffusion_models`, `text_encoders`, `loras`, …) |
| `lmstudio` | `~/.lmstudio/models`, `~/.cache/lm-studio/models` | model files |
| `llamacpp` | `~/.cache/llama.cpp`, `~/Library/Caches/llama.cpp`, `%LOCALAPPDATA%\llama.cpp` (`LLAMA_CACHE`) | GGUF files |
| `docker` | `docker system df --format json` when Docker answers (no store when it doesn't); the data root from `docker info` | images, containers, volumes, build cache with their reclaimable size |
| `caches` | uv, pip, npm, pnpm, conda, Hugging Face `xet`, torch hub, whisper, Playwright browsers, pacman, apt, dnf, Homebrew | one item per cache, with its clean command |

Users add folders of their own (`hone-drive scan --add ~/models`) as the store `custom`.

### 5.2 Sizing rules

- Sizes are allocated bytes (`st_blocks × 512` on POSIX, the file size on Windows), counted once per
  inode, so hard links and sparse files are not double-counted.
- A store's items that share files (two Ollama names for one model; Hugging Face snapshots) list every
  name, and the store's size counts each file once.
- Folders that can't be read are listed with "not readable" and are never counted as empty.
- Each store has one time limit (default 60 s) for all its folders; a store that times out
  is marked partial with the bytes counted so far.

### 5.3 Duplicates

Two files are duplicates only when their bytes are identical. Candidates are files over 100 MB (every file inside a
Hugging Face repo, every Ollama blob) with equal sizes in different model stores; an Ollama blob's name is its SHA-256, so a candidate is hashed once and
compared with that name; other pairs are hashed both. Hashing is opt-in (`--duplicates`) because it reads
the files. The same model in different formats (a GGUF in Ollama, safetensors in ComfyUI) is not a
duplicate and is never reported as one; the history explains why (history §4).

### 5.4 Unaccounted space

For each volume: `unaccounted = used − readable − explained`, where `readable` is what a walk of the volume
as the user could count (without crossing into other mounts) and `explained` is the stores on it that the
walk couldn't read but a server sized (Ollama's service folder through its API, Docker's data root). The
walk takes minutes on a large disk, so it runs only when asked (`scan --account`, the dashboard's
"Account for space"; decision D-011); it also lists the biggest folders two levels down. When it is over 5% of the volume, the dashboard shows it with the likely hiding places
for this OS (other users' home folders, model servers running as their own user, container data roots,
deleted files still held open, snapshots, Windows' page and hibernation files) and the administrator command that would measure
them (`sudo du -xh --max-depth=3 / | sort -rh | head -40` on Linux and its equivalents), which it shows
but never runs.

## 6. Benchmarks

### 6.1 Tests

| Test | What it does | Default size |
|---|---|---|
| `seq-write` | writes a temporary file in 16 MiB blocks, flushed to the device | 4 GiB |
| `seq-read` | reads that file back | 4 GiB |
| `sustained-write` | writes until a size or time limit, recording throughput each second: shows the drive's fast cache running out and heat throttling | 32 GiB or 120 s (opt-in) |
| `random-read` | 4 KiB reads at random offsets in the temporary file, one at a time and 32 in flight | 20 s |
| `model-read` | reads real files the user picks (or the three largest single-file items of the stores on that volume), read only | up to 3 files |

A run benchmarks the volumes the user chooses; the default compares the volume holding the home folder
with every external volume.

### 6.2 Bypassing the page cache

Every read and write test bypasses the operating system's cache, or says it could not:

| OS | How |
|---|---|
| Linux | `O_DIRECT` with aligned buffers (`mmap`-allocated); where a filesystem refuses it, `posix_fadvise(DONTNEED)` after the write (`cache: fallback`) |
| macOS | `fcntl(F_NOCACHE)` on the file |
| Windows | `CreateFileW` with `FILE_FLAG_NO_BUFFERING | FILE_FLAG_WRITE_THROUGH` through `ctypes`, aligned buffers |

When direct I/O is refused (tmpfs and some FUSE filesystems), Linux writes through the cache and then
asks the kernel to drop the file's pages (`posix_fadvise(DONTNEED)`) before reading it back, and marks
the measurement `cache: fallback`; on macOS and Windows, which have no such call, it is marked
`cache: possibly-cached`, carries a warning, is shown greyed out and is never used for an estimate
(decision D-019). `model-read` uses the same flags, so a model file read a
minute ago is not measured from RAM. Throughput is computed from monotonic timestamps in Python, never
parsed from another tool's output (history §2).

### 6.3 Safety rules

1. The only file written is `<folder>/.hone-drive-bench-<run id>-<test>.tmp`, deleted in a `finally`
   block. Its path is written to a ledger in the data folder before it is created, so the next benchmark
   deletes it if a killed process left it behind (decision D-014).
2. A write test runs only when the volume has its size plus 10% plus 2 GiB free, and stops early if free
   space falls under 2 GiB.
3. No test writes to a volume that is read-only, the system volume of another OS, or one the user did not
   select; a folder on a filesystem the scan skipped (a RAM disk mounted inside a volume) is refused, so a
   result is never filed under the wrong volume.
4. Tests can be stopped at any time; a stop deletes the file and records a partial measurement.
5. One benchmark at a time per machine (a lock file in the data folder); a scan can run beside it.

### 6.4 Interpreting a result

Each measurement is compared with the link's expected speed (§4.2) and, for internal drives, with the
fill level (§4.4). The dashboard labels it `link-bound`, `drive-bound`, `cache exhausted after N GB`
(from the sustained curve), `throttling after N s` (a drop after a steady stretch) or `steady`
(decision D-020).

## 7. Estimates

- **Load time** of an item from a volume = item bytes ÷ the volume's measured `seq-read` (or the
  `model-read` figure when there is one for that volume), shown with a range from the measurement's
  spread. It is labelled "disk time only": a model server also spends time on GPU upload and setup,
  which hone-drive does not measure in 0.1.
- **Warm loads**: when an item fits in free RAM it may come from the page cache on the second load; the
  dashboard says so instead of promising the disk figure every time.
- **Copy time** of a store between two volumes = bytes ÷ min(source read, target sustained write).
- Estimates without a measurement for that volume use the link's expected speed and say so (a copy
  time is "measured" only when both its read and its write were).

## 8. Advice

### 8.1 Rules

Advice comes from plain functions over a `Run`, one per rule, in a dict keyed by rule id. Each returns
zero or more `Advice` items. 0.1 has:

| Id | When | Says |
|---|---|---|
| A01 | a volume of 10 GB or more is over 85% full | which stores and items to move or clean first, with the space each frees |
| A02 | a store over 20 GB is on a fuller volume than the emptiest external one, which has room for it | move it, with the store's own setting (`OLLAMA_MODELS`, `HF_HOME`, ComfyUI `extra_model_paths.yaml`, LM Studio's folder) and the change in load time per item |
| A03 | a link is limited (§4.1) | the gap, the likely causes in order, what to try; "test again" re-runs the probe |
| A04 | an internal SSD over 85% full has a measured write under 30% of its measured read (or of its link without one) (§4.4) | the slowdown is likely the fill level; freeing space should restore it |
| A05 | an external volume that holds a store, or that A02 would move one to, lacks a trait it needs (§4.3, D-018) | e.g. Ollama's service user can't own files on exFAT: reformat as the OS's native filesystem (with the warning that this erases it) or run the server as your user |
| A06 | an external volume holds a store (or A02 would move one there) and is known not to be mounted at boot: Linux only in 0.1, since the macOS and Windows probes can't tell | an `/etc/fstab` line with `nofail` |
| A07 | a cache is safe to clean and at least 1 GB (uv, pip, npm, package manager caches; Docker's build cache and unused images from 2 GB; never containers or volumes) | the clean command and the bytes it frees; for caches that need the same volume as projects (uv, pnpm), "clean, don't move" |
| A08 | byte-identical duplicates exist (§5.3) | which copy to keep and how to point the other tool at it |
| A09 | an item A02 would move is 5 GB or more, was used in the last 14 days, and would load over 2× slower from the external volume | keep it on the internal drive |
| A10 | space is unaccounted (§5.4) | where it may be and the administrator command that would measure it |

### 8.2 Advice items

An `Advice` has `rule`, `severity` (`info`, `suggest`, `warn`), `title`, `why` (sentences with the
measurement ids they cite), `gain` (`bytes_freed`, `seconds_saved_per_load` or `None`), `commands` (per
OS, as text) and `risks` (what could go wrong, e.g. "reformatting erases the drive"). Commands are never
run by hone-drive; the dashboard has a copy button. Advice is ranked by severity, then by bytes freed.

## 9. Runs and history

A run is one JSON document: `id` (UTC time and a short hash), hone-drive version, the machine, drives,
volumes, stores, measurements, advice, and the probe commands with their exit codes. Runs are stored in
the data folder (`HONE_DRIVE_HOME`, default: `$XDG_DATA_HOME/hone-drive` or `~/.local/share/hone-drive` on Linux, `~/Library/Application
Support/hone-drive` on macOS, `%LOCALAPPDATA%\hone-drive` on Windows), one file per run, written
atomically. `compare(a, b)` lists what changed: drives added or removed, links renegotiated, volumes
fuller or emptier, throughput up or down beyond the measurements' spread (at least ±10%).

A new scan keeps the latest run's measurements of the volumes it still finds, each with the time it was
measured, so the load times don't fall back to link speeds after every scan; a new benchmark of a volume
replaces them (decision D-015).

## 10. The dashboard

`hone-drive` (no arguments) or `hone-drive dashboard` starts a server on `127.0.0.1` on a free port,
opens the browser and prints the address. It uses the standard library's HTTP server, a JSON API and
server-sent events for progress, with the page's HTML, CSS and JavaScript shipped in the package: no
build step, no CDN, no network.

Every screen speaks in the user's words (change [0002](changes/0002-a-dashboard-people-understand.md)):
drives by name ("Internal drive", the external drive's model), verdicts with a coloured dot and words,
sizes and seconds instead of identifiers. `presentation.py` and `views.py` compute the words, names,
tones and the plan on the server; the page only draws.

| Screen | Shows |
|---|---|
| Overview | a headline (the most important finding and a button to act on it), a card per drive (how full, connection at full speed or not, measured speed and the load time of a 20 GB model, a "Test speed" button), the plan's steps and the biggest things on disk |
| Plan | what was found (full, slow), then the advice as ordered steps: clean caches, prepare the external drive, move model folders, get full speed, find hidden space; each task with what it frees, a checkbox kept in the browser, and "How to do it" with the commands and their risks |
| Space | a bar per drive with each store in its fixed colour, a table of the same, the biggest single things, and the two slow scans (hidden space, duplicates) as buttons with what they do |
| Drives & speed | read and write speed compared across drives, then each drive's card with its filesystem and startup notes and its technical details |
| Models | every model with its tool, size and drive, and its load time from each drive (estimated from the connection until the drive is tested, and labelled so), with a note when it fits in the memory free now and may load warm (§7); models on a drive's shelf, connected or not; a checkbox per model with "Move to <drive>", "Bring back" and "Remove from drive", each opening a confirmation with the plan (§17) |
| History | compare two scans, open an older one, download a report to share |

Charts are inline SVG and HTML drawn by the page, in the dataviz reference palette for light and dark
(decision D-016); every chart has a legend or a table. Store colours follow the store, never its size.
The server refuses requests whose `Host` is not a local name (DNS rebinding), and changes (`POST`) without
the page's token in the `X-Hone-Drive-Token` header, with a foreign `Origin`, or with a body that isn't
JSON (CSRF). The page's content security policy allows only its own scripts and styles. A scan, a
benchmark or a shelf action runs as one background job at a time; `/api/events` streams its progress and each benchmark
chunk's throughput. Screens have their own address (`#drives`); `?still` turns off live updates, for
screenshots.

## 11. The command line

`hone-drive [dashboard [--port N] [--no-browser]]`, `scan [--add PATH] [--account] [--duplicates]
[--json]`, `bench [PATH ...] [--tests seq,random,sustained,model] [--size 4G] [--model FILE] [--run ID]
[--json]`, `advise [RUN] [--json]`, `runs [--json]`, `show [RUN] [--json]`, `compare RUN_A [RUN_B] [--json]`, `report [RUN]
[--format md|json] [--keep-private]`, `shelf [--json]`, `shelf check [TOOL]`, `shelf move MODEL...
[--to DRIVE] [--keep-local] [--dry-run] [--json]`, `shelf restore MODEL... [--from DRIVE] [--dry-run]
[--json]`, `shelf remove MODEL... [--from DRIVE] [--only-copy]`, `shelf verify [DRIVE]` (§17). `RUN`
defaults to `latest`. Commands print a short human summary,
or JSON with `--json`; `scan` and `bench` save the run with its advice. The CLI uses `argparse` (decision
D-002); `python -m hone_drive` is the same command.

## 12. Python API

```python
from pathlib import Path
from hone_drive import BenchOptions, advise, bench, load_time, report, scan

run = scan(accounting=False, duplicates=False)          # machine, drives, volumes, stores; no benchmarks
run = bench(run, [str(Path.home()), "/mnt/models"], ["seq", "model"], BenchOptions(size=4 * 1024**3))
for a in advise(run, str(Path.home())):
    print(a.severity, a.title, a.gain.bytes_freed)
print(report(run, "md"))                                 # private details removed
```

The shelf (§17) is the module `hone_drive.shelf`:

```python
from hone_drive import scan, shelf

run = scan()
print(shelf.check(run, "ollama"))                              # Access(ok, problems, fix)
the_plan = shelf.plan(run, ["ollama:gemma4:31b"], to="/run/media/user/PNYRP60PSSD")
shelf.move(run, the_plan)                                      # Result(done, failed, stopped)
shelf.contents(run)                                            # every drive's shelf, connected or not
shelf.restore(run, ["ollama:gemma4:31b"])                      # the drive keeps its copy
shelf.remove(run, ["ollama:gemma4:31b"], only_copy=False)      # from the drive only
shelf.verify(run)                                              # re-hash every copy on connected drives
```

The public names are exported from `hone_drive/__init__.py` with `__all__`. `hone_drive.testing` provides
`FakeProbe` (machines described in data; `reference_probe()` is this design's reference machine),
`RecordedSystem` and `recorded_probe()` (a machine replayed from a fixture), `record()` (`python -m
hone_drive.testing out.json` records this machine with serials, UUIDs and names replaced) and the `Probe`
contract checker `check_probe()`.

## 13. Reports and privacy

`report()` writes Markdown or JSON from a run for sharing in an issue or a forum. By default it removes
serial numbers, volume UUIDs, the user name and home path (replaced by `~`), host name and any store path
outside the home folder and the tools' default folders (replaced by `<custom>` wherever it appears); `--keep-private` keeps them. Runs on disk keep
everything; they never leave the machine unless the user shares a report.

## 14. Testing

- **Unit and contract** (default suite, offline): probes are tested against recorded command outputs and
  sysfs trees from real machines (Linux laptop with NVMe and a USB SSD, an Apple silicon Mac, a Windows
  desktop), stored as fixtures; store finders against fake folder trees; advice rules against runs made
  with `FakeProbe`; the API and dashboard against a fake run.
- **Small benchmarks** (default suite, `tests/unit/test_benchmark.py` and the acceptance cases): 32–64 MiB
  on the test machine's temporary folder, checking the shape of the result, the cache-bypass flag and
  that the file is gone; never a speed.
- **CI** runs the default suite on Ubuntu, macOS and Windows runners (design goal: three systems).
- **Bench** (`-m bench`, not in the default suite): full-size benchmarks on real drives, run by hand
  through `scripts/gpu-lock.sh`, since a 4 GiB write would disturb another project's model run on the same
  machine (decision D-006). None are written yet: the marker is reserved for them.
- **Real programs** (default suite): the shelf's in-use check against real processes on every system: a
  program with a model file open, one with it mapped into memory and closed, and a ComfyUI `main.py`
  started on a port of its own (§17).
- **Real tools** (`-m real`, not in the default suite; by hand through `scripts/gpu-lock.sh`, and in the
  "Real tools" workflow on Linux, macOS and Windows runners): the real Ollama and the real Hugging Face
  cache (`huggingface_hub`, dependency group `real`) moved to an exFAT disk image and back. A small model
  is pulled, moved, no longer listed by Ollama, brought back, listed with the same digest, and answers; a
  tiny Hugging Face repo is moved and back and found offline with the same bytes. On the Linux runner
  Ollama is installed by its own installer as a service of its own user with its folder closed, as on the
  reference machine: the test sees the access check fail, runs the fix it shows, and moves the model.

## 15. Guarantees (acceptance cases)

1. `scan()` on each OS returns every mounted local volume with size, used and free matching the OS's own
   figures within 1%.
2. A fact the OS can't provide is `None` with a reason, never a guess or zero.
3. A USB drive whose negotiated link is below both its port's and its own reported maximum is marked
   limited, and advice A03 is produced (fixture: the reference machine's 10-of-20 Gbps link).
4. Ollama models listed under two names that share blobs are counted once in the store's size.
5. A folder that can't be read is reported as not readable and never counted as empty.
6. Unaccounted space is reported when a volume's used bytes exceed the readable total by more than 5%,
   with OS-specific hiding places (fixture: 830 GB used, 424 GB readable).
7. Every read and write measurement records how the cache was bypassed; a cached measurement is never
   shown as a plain result.
8. The benchmark's temporary file is gone after a normal finish, an error, a stop, and a killed process
   followed by the next start.
9. A write test refuses to start when free space is under its size plus the margin, and stops when free
   space falls under 2 GiB.
10. A load-time estimate for an item uses the measured read of its volume and is labelled "disk time
    only"; without a measurement it uses the link's expected speed and says so.
11. Advice never runs a command; every advice item has its rule, why with measurement ids, and commands
    for the current OS when it asks the user to act (`warn`, `suggest`); an `info` item may only inform
    (decision D-021).
12. Two different-format copies of one model (GGUF and safetensors) are never reported as duplicates;
    byte-identical files in two stores are.
13. A report removes serials, UUIDs, user name, home path and host name unless `--keep-private` is given.
14. The dashboard serves only on a local address, refuses a foreign `Host`, and refuses a state-changing
    request without its token.
15. `compare()` reports a renegotiated link and a throughput change beyond the spread between two runs.
16. The package installs and `hone-drive scan --json` runs on Linux, macOS and Windows with no
    administrator rights and no tools beyond the OS's own.
17. A file is removed from the computer only when the catalog on the drive records a copy that was read
    back with the page cache bypassed and matched the local file's SHA-256, and neither file's size or
    modification time changed since; otherwise both are hashed again first.
18. Moving a model whose files are already on the drive with the same bytes copies nothing and only
    removes the local copy; moving or bringing back a model twice ends in the same state as once.
    Bringing a model back never removes anything from the drive.
19. A move or a restore stopped, failed or killed at any point leaves every model complete in at least
    one place, and the next run finishes it or cleans up after it.
20. A model in use (loaded in Ollama, ComfyUI running, the file open in a process) is refused with the
    reason, and nothing is changed.
21. An Ollama blob is removed from the computer only when no model left there lists it.
22. When hone-drive can't read, write or remove a store's files, `check()` says so with the command for
    this system, which it never runs; nothing runs with administrator rights.
23. A model brought back works with its tool: Ollama lists it with the same digest; ComfyUI, LM Studio,
    llama.cpp and the user's folders find the file at its original path; the Hugging Face repo has the
    same snapshot.
24. A drive's shelf is listed when the drive is unplugged, and the drive is recognised by its id wherever
    it is mounted.
25. A move is refused when the drive lacks the room or its filesystem can't hold a file.
26. Removing a model from a drive never touches the computer's copy, and is refused without an explicit
    confirmation when the drive holds the only copy.

## 16. Known limits (0.1)

- Windows does not expose USB or Thunderbolt link speed through its standard tools; 0.1 reports it as
  unknown and relies on the measured speed (an extra probe through the USB device APIs may come later).
- Load-time estimates cover reading from disk, not a model server's GPU upload and setup.
- A drive's own maximum link speed is often not reported; "could the drive go faster?" is then answered
  from its model's published figure only if the user enters it.
- Random-read tests from Python measure latency well at one request in flight; at 32 in flight they are
  approximate (threads, not an async I/O engine). A later change may add `fio` as an optional engine.
- SMART health is not read in 0.1 (`smartctl` usually needs administrator rights); the Drives screen
  shows no health summary (decision D-017).
- APFS snapshots, Btrfs/ZFS compression and deduplication can make "used" differ from the sum of files;
  0.1 names these as possible causes of unaccounted space without measuring them.
- The shelf's in-use check sees this user's programs only (an Ollama service of its own user answers
  `/api/ps` instead); a program of another user with a model file open is not seen on Linux and macOS.

## 17. The model shelf

Change [0003](changes/0003-model-shelf.md). Models are used from the computer; a drive's **shelf** keeps
the ones not needed now. Nothing here happens unless the user starts it.

**A model** is, per tool: Ollama, a manifest and the blobs it lists (a blob another model lists is shared);
Hugging Face, one repo folder (`models--org--name`); ComfyUI, LM Studio, llama.cpp and the user's own
folders, one model file. Its key is `<store id>:<name>` (`ollama:gemma4:31b`,
`comfyui:diffusion_models/wan.safetensors`). Docker and package caches are not shelved: cleaning them is
the answer (A07).

**On the drive**, `<drive>/hone-drive/` holds `drive.json` (an id, so the drive is found wherever it is
mounted), `shelf.json` (the catalog: each model's tool, names, original folder, and each file's size,
SHA-256 and the modification times it had when it was checked; Hugging Face's snapshot links as data, so
an exFAT drive can hold them) and `files/<tool>/<folder id>/...` (one copy of a blob two models list).
A copy of each catalog is kept in `<data folder>/shelves/<drive id>.json`, so an unplugged drive's models
are still listed. The catalog carries `format: 1`.

**Moving** (`plan()`, then `move()`):

1. Refuse, changing nothing, when the model is in use (Ollama `/api/ps`; for ComfyUI, a process running
   that install's `main.py` on any port, the ComfyUI desktop app, or a server answering on
   `127.0.0.1:8188`; a file one of this user's programs has open or mapped into memory; on Windows, a
   file another program holds, found by opening it without sharing), when this user can't read,
   write or remove its files (with the fix, below), when the drive lacks room for the files still to
   copy plus 10% plus 2 GiB, or when the drive is FAT32 and a file is over 4 GB.
2. Compare first, trusting the catalog: a file whose size and modification time on both sides are still
   the recorded ones is not read or copied again; any other file already on the drive with the same size
   is hashed on both sides.
3. Copy what is missing to `<name>.part`, hashing as it reads, `fsync`, read the copy back with the page
   cache bypassed (§6.2), compare, and rename. Each checked file is recorded in the catalog at once.
4. Mark the model complete in the catalog, then remove it from the computer (not with `--keep-local`):
   Ollama through its server's `DELETE /api/delete` when it answers, and in any case the manifests and the
   blobs no remaining manifest lists; Hugging Face the repo folder; the others the file.

**Bringing back** copies each file to its original place the same way, checked against the catalog's
SHA-256 (a drive copy that doesn't match is reported damaged and nothing is written in place), Ollama's
blobs before its manifests, Hugging Face's links last (a copy where the OS refuses links). It is refused
for an unplugged drive, an incomplete copy, or a different file already in the model's place. It never
removes anything from the drive.

**Removing from a drive** never touches the computer. It is refused for a model whose only copy is on the
drive unless the user confirms (the dashboard asks for the model's name; the CLI needs `--only-copy`).
Files other models on the drive list stay. **Verifying** re-hashes every file on a connected drive and
marks damaged models incomplete, so they can be moved again.

**Access** (`check()`) is tried, not assumed: list and read the folders, create and remove a test file
(`.hone-drive-check-<pid>`), read every file of the model. When it fails, the fix is built for this system
from what was found and shown, never run:

| Found | Fix shown |
|---|---|
| Linux, the folder owned by another user (Ollama's service: `ollama`) | `sudo setfacl -R -m u:<user>:rwX,d:u:<user>:rwX,d:u:<owner>:rwX <folder>` (this user only, also files added later, and the owner keeps reading files brought back), with the line that undoes it; the `acl` package's install command first when `setfacl` is missing |
| Linux, the folder this user's own but not writable | `chmod -R u+rwX <folder>` |
| macOS | `sudo chmod -R +a "user:<user> allow read,write,delete,...,file_inherit,directory_inherit" <folder>` |
| Windows | `icacls "<folder>" /grant "<user>:(OI)(CI)M" /T` in an administrator PowerShell |

**Interruptions:** a stop removes the `.part` file and keeps what is complete; an error or a killed
process leaves each model complete on the computer (the catalog says the drive's copy is not complete)
or on the drive (the catalog says it is), and the next run finishes it. One shelf job at a time per
machine, and a lock in `<drive>/hone-drive/` holding the host and process, so two computers never write
one catalog at once.

