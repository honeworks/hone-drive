# Implementation decisions

Choices too small for a change record. They are numbered in the order they were made, and code comments
refer to them by number. Entries marked **awaiting owner review** are proposals until the owner confirms.

## Awaiting owner review

| Entry | Question for the owner |
|---|---|
| (none) | |

On 2026-10-18 the owner accepted design 0001 ("accepted"), and with it every open question with its
proposed option (D-001, D-003, D-005, D-006, D-007, D-008).

## D-001: psutil is a core dependency  (2026-10-18)

- **Question:** listing volumes with their filesystem and usage the same way on Linux, macOS and Windows.
- **Choice:** `psutil` (BSD-3, wheels for all three systems) as a core dependency next to pydantic.
  Drive and link details still come from each OS's own tools (design §3.2).
- **Why:** the standard library has `shutil.disk_usage` but no portable list of mounted volumes; writing
  one per OS duplicates a mature, widely installed package.
- **Status:** accepted 2026-10-18 by the owner ("accepted").

## D-002: the command line uses argparse  (2026-10-18)

- **Choice:** the CLI is built on `argparse`, in the core, not a `cli` extra with typer and rich.
- **Why:** the dashboard is the main interface; `uvx hone-drive` must work with nothing but the core, and
  the CLI's output is a short summary or JSON.
- **Status:** follows the design (0001).

## D-003: the name hone-drive  (2026-10-18)

- **Choice:** `hone-drive`, import `hone_drive`, command `hone-drive`, variables `HONE_DRIVE_*`.
- **Why:** short, says what it is about, reads well as a command; "disk" suggests internal drives only and
  "storage" suggests cloud storage.
- **Status:** accepted 2026-10-18 by the owner ("accepted").

## D-004: an expected-speed table per link  (2026-10-18)

- **Choice:** a fixed table of usable throughput per link generation and width (design §4.2), with a
  measurement within 15% of it called link-bound.
- **Why:** history §5: the measured ~1 GB/s matched a 10 Gbps link, which is what told a slow link from a
  slow drive. The figures are the links' rates less typical protocol overhead.
- **Status:** follows the design (0001).

## D-005: the dashboard is a standard-library server and a static page  (2026-10-18)

- **Choice:** `http.server` on `127.0.0.1`, a JSON API, server-sent events, and the page's HTML, CSS and
  JavaScript shipped in the package with inline SVG charts; the host and token checks of Hone Frame.
- **Why:** no build step, no extra dependency, no network; it starts in under a second from `uvx` and is
  the pattern the family already maintains.
- **Status:** accepted 2026-10-18 by the owner ("accepted").

## D-006: full benchmarks under the machine-wide lock  (2026-10-18)

- **Choice:** the `bench` marker (full-size tests on real drives) is not in the default suite or CI and
  runs only through `scripts/gpu-lock.sh`. The default suite runs a 64 MiB benchmark for shape only.
- **Why:** writing gigabytes disturbs another project's model run on the same machine, and its timings are
  disturbed in turn; CI runners' disks say nothing about users' drives.
- **Status:** accepted 2026-10-18 by the owner ("accepted").

## D-007: advice only, no actions in 0.1  (2026-10-18)

- **Choice:** advice shows commands per OS with a copy button and never runs them.
- **Why:** moving hundreds of gigabytes of models, changing service settings and reformatting drives can
  lose data; doing it safely (checks, resumable copies, rollback) deserves its own design.
- **Status:** accepted 2026-10-18 by the owner ("accepted").

## D-008: distribution through PyPI  (2026-10-18)

- **Choice:** a pure-Python wheel on PyPI, started with `uvx hone-drive` or `pipx install hone-drive`.
- **Why:** AI developers have Python and uv already; one wheel serves all three systems. Standalone
  binaries (PyInstaller) can follow if people without Python ask for them.
- **Status:** accepted 2026-10-18 by the owner ("accepted").

## D-009: local git only until the owner says to publish  (2026-10-18)

- **Choice:** the repository is created locally; it goes to `github.com/honeworks/hone-drive` and PyPI only
  when the owner asks.
- **Status:** follows the workspace rules.
