# 0000: The session behind the first design (October 2026)

hone-drive started from one afternoon, 2026-10-18, spent answering "what is filling our disks?" on the
owner's laptop and then deciding what to put on a new external SSD. Every number below was measured on
that machine:

- **Machine:** a Linux laptop (EndeavourOS, kernel 6.18), 30 GB RAM, an RTX 4060 Laptop GPU with 8 GB.
- **Internal drive:** WD PC SN560 1 TB NVMe, PCIe 4.0 x4 link, ext4, 936 GB volume, 830 GB used (94%).
- **External drive:** PNY RP60 portable SSD 1 TB, USB, exFAT from the factory, empty.

The tools were `df`, `du`, `lsblk`, `lsusb`, sysfs, `docker system df`, `ollama list` and `dd` with
`O_DIRECT`. Each section ends with what carried over into the design.

## 1. Finding the space

An unprivileged `du` of the root filesystem counted **424 GB of 830 GB used**. Following the gap took
four rounds, three of which needed the owner to run commands with `sudo` and paste the output:

| Round | Guess | Result |
|---|---|---|
| 1 | another user's home folder (unreadable, so a good suspect) | 4.7 GB |
| 2 | Docker's data root `/var/lib/docker` | 5.9 GB; the images were in `/var/lib/containerd` (34 GB), because Docker used the containerd image store |
| 3 | files deleted but held open | all were `memfd` buffers of the desktop compositor, in RAM |
| 4 | a full `sudo du` | **`/usr/share/ollama`: 362 GB**, the Ollama service's models, owned by its own user |

Other findings: a 32 GB swap file at the root; the uv package cache at 69 GB; ComfyUI models at 115 GB;
the pacman package cache at 11 GB.

Carried over: store finders that know where model servers keep data per OS, including service users
(design §5.1); "not readable" never counted as empty (§5.2); the unaccounted-space check with its likely
hiding places and the command to measure them (§5.4); Docker sized through Docker itself (§5.1).

## 2. Measuring the drives, and a parsing mistake

Benchmarks used `dd` with 16 MiB blocks and 4 GiB files, `oflag=direct` / `iflag=direct` to bypass the
page cache. The first internal-drive results looked absurd (8 GB/s reads, then "1 GB/s" and "2 GB/s"):
the script split `dd`'s output on commas, and the owner's locale writes `2,4 GB/s`. Rerun with
`LC_ALL=C`:

| | Internal WD SN560 | External PNY RP60 |
|---|---|---|
| sequential read, test file | 2.1–3.1 GB/s | 0.95–1.0 GB/s |
| read of real model files (6.7 GB, 13 GB) | 2.2 GB/s | — |
| sequential write | 0.13–0.24 GB/s | 0.89 GB/s |

Also learned: without direct I/O a reread is served from RAM (18 GB of the 30 GB were page cache at the
time), which is what many casual benchmarks measure.

Carried over: direct I/O on every OS, with the method recorded and a cached result never shown as plain
(§6.2, AC7); throughput computed in Python from monotonic time, never parsed from tool output (§6.2);
`model-read` on real files (§6.1), since they were the figure the owner cared about.

## 3. The internal drive was slow to write because it was full

The internal drive's writes (~0.2 GB/s) were a quarter of the external drive's. The SN560 is a budget
drive that writes through a fast cache sized from free space; at 94% full there was little left.

Carried over: the fill-level check and advice A04 (§4.4, §8.1); the sustained-write test that shows a
cache running out (§6.1).

## 4. One model, two formats, two copies

The owner asked whether ComfyUI's language models were the same as Ollama's. Two pairs shared a base
model (Qwen2.5-VL 7B: 6.0 GB in Ollama as GGUF, 9.4 GB in ComfyUI as fp8 safetensors; Qwen3-VL 8B: 6.1 GB
and 9.4 GB), but different formats and quantisations used differently (a chat model against a text
encoder) cannot share a file. Ollama's list also showed many models twice under two names with one ID,
which share their blobs and use the space once.

Carried over: duplicates only when bytes are identical (§5.3, AC12); Ollama items sized by unique blobs
(§5.2, AC4); advice A02 points each tool at one shared folder per store instead of pretending formats
can be shared.

## 5. The link: 10 Gbps of a possible 20

The PNY RP60 is sold as a USB 3.2 Gen 2x2 drive (20 Gbps, up to 2,000 MB/s). sysfs showed:

| Port tried | Host controller | Drive negotiated |
|---|---|---|
| first | 10 Gbps, 1 lane | SuperSpeed+ 10 Gbps, 1 lane |
| second | 20 Gbps, 2 lanes | SuperSpeed+ 10 Gbps, `rx_lanes=1`, `tx_lanes=1` |

Measured reads (~1 GB/s) matched a 10 Gbps link exactly, so the drive was link-bound. The likely causes,
in order: the cable, the drive's own mode, the port. Nothing in a desktop UI shows any of this.

Carried over: links as negotiated against possible, with lanes (§4.1); the expected-speed table that
tells link-bound from drive-bound (§4.2, D-004); advice A03 with "test again" after a change (§8.1).

## 6. Turning numbers into decisions

The owner's questions were in their own units: "how much slower?", "what about LLMs used by ComfyUI?",
"can both tools read models from the SSD without copies?". The answers that helped were load times per
model (a 19 GB model: ~9 s internal, ~20 s external; the 28 GB Wan 2.2 pair a ComfyUI workflow swaps every
run: ~13 s and ~28 s), that generation speed is unchanged once loaded, that recently read models may come
from RAM, and the exact settings: `OLLAMA_MODELS` in the service's override, ComfyUI's
`extra_model_paths.yaml`, `HF_HOME`; ext4 rather than exFAT so the Ollama service user can own its files;
an `/etc/fstab` entry with `nofail`; uv's cache kept on the projects' volume for its hard links.

Carried over: estimates in seconds per item and volume, labelled disk time only (§7, AC10); advice rules
A02, A05, A06, A07 and A09 (§8.1); the filesystem traits table (§4.3).
