# Implementation decisions

Choices too small for a change record. They are numbered in the order they were made, and code comments
refer to them by number. Entries marked **awaiting owner review** are proposals until the owner confirms.

## Awaiting owner review

| Entry | Question for the owner |
|---|---|
| D-019 | design §6.2 now describes the cache fallback the code has, instead of a file twice the size of RAM: keep this? |

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
- **Status:** accepted 2026-10-18 by the owner ("accepted"). For models, superseded by change 0003 (the
  shelf): hone-drive moves models when the user asks; advice still only shows commands.

## D-008: distribution through PyPI  (2026-10-18)

- **Choice:** a pure-Python wheel on PyPI, started with `uvx hone-drive` or `pipx install hone-drive`.
- **Why:** AI developers have Python and uv already; one wheel serves all three systems. Standalone
  binaries (PyInstaller) can follow if people without Python ask for them.
- **Status:** accepted 2026-10-18 by the owner ("accepted").

## D-009: local git only until the owner says to publish  (2026-10-18)

- **Choice:** the repository is created locally; it goes to `github.com/honeworks/hone-drive` and PyPI only
  when the owner asks.
- **Status:** done: the owner published the repository at `github.com/honeworks/hone-drive` on
  2026-10-18. PyPI is still to come (D-008).

## D-010: Ollama's service folder is sized through its server  (2026-10-18)

- **Question:** on Linux the Ollama service keeps its models in its own user's folder
  (`/usr/share/ollama`), which the user can't read (history §1); hone-drive never runs as root.
- **Choice:** when an Ollama folder is behind an unreadable folder, ask the local server
  (`OLLAMA_HOST`, default `127.0.0.1:11434`) for `/api/tags`: names, sizes and manifest digests. Names
  with one digest are one item. The store is marked `source: "ollama api"`, its path is the unreadable
  folder, and the volume accounting subtracts it as explained (design §5.4).
- **Why:** it is the only way to see the biggest store on the reference machine without root; the request
  goes to localhost only. Two models that share a weights blob but have different manifests are counted
  twice through the API (rare: only for copies of one model with a different template).
- **Status:** follows the design (0001).

## D-011: the volume walk runs only when asked  (2026-10-18)

- **Choice:** `scan()` finds drives, volumes and stores in seconds; the walk that measures unaccounted
  space (design §5.4) runs with `accounting=True` (`scan --account`, the dashboard's button).
- **Why:** on the reference machine the walk of an 830 GB volume took 2.5 minutes cold; the goal of
  design §1 is a scan that finishes in seconds.
- **Status:** follows the design (0001).

## D-012: probes take a runner and a file reader; fixtures replay real machines  (2026-10-18)

- **Choice:** each probe gets a `Runner` (commands) and, on Linux, a `Files` reader (sysfs, /proc) as
  arguments. `hone_drive.testing.RecordedSystem` answers both from a JSON fixture, and `record()` writes
  one from the machine it runs on, with serials, UUIDs, the host and user names replaced.
- **Why:** a Linux probe can then be tested on macOS and Windows CI and the other way round; a
  contributor with a new machine adds a fixture with one command. Both Linux fixtures are recorded on
  the reference machine (the USB one with the PNY plugged in; recording it found that its USB bridge
  reports the SSD as rotational). The macOS and Windows fixtures were written by hand from documented
  formats (their `note` says so) until recordings from real machines replace them.
- **Status:** follows the design (0001).

## D-013: the benchmark engine is plain Python  (2026-10-18)

- **Choice:** sequential tests write and read 16 MiB chunks of random-looking data through one
  page-aligned `mmap` buffer with the direct-I/O flags of design §6.2, timed with `time.monotonic()`;
  the spread is the relative standard deviation of the per-chunk speeds. Random reads: 4 KiB at
  seeded random aligned offsets, one at a time for latency, then 32 threads with their own file handles.
- **Why:** no tool to install on any OS, and no output to parse (history §2). On the reference machine it
  measured the internal drive's reads at 2.1–3.1 GB/s and writes at 0.15–0.16 GB/s, the same as `dd`
  with `O_DIRECT` that afternoon.
- **Status:** follows the design (0001).

## D-014: a ledger of temporary files  (2026-10-18)

- **Choice:** before a test file is created, its path goes into `bench-files.json` in the data folder;
  it leaves the ledger when the file is deleted. Every benchmark starts by deleting what the ledger still
  lists (only files named `.hone-drive-bench-*`).
- **Why:** design §6.3 rule 1 for a process that is killed (`finally` blocks don't run): the next start
  must find the file without searching every drive.
- **Status:** follows the design (0001).

## D-015: a new scan keeps the latest run's measurements  (2026-10-18)

- **Choice:** `scan` (CLI and dashboard) copies the latest run's measurements of the volumes it still
  finds, when the host is the same; each measurement carries the time it was measured. A benchmark of a
  volume replaces that volume's measurements.
- **Why:** a benchmark takes minutes and the speeds rarely change; without this, every scan would make
  the load times fall back to link estimates. The time stamp keeps it honest.
- **Status:** follows the design (0001).

## D-016: dashboard colours are the dataviz reference palette  (2026-10-18)

- **Choice:** stores take the eight categorical slots in a fixed order (Ollama, ComfyUI, Hugging Face,
  Docker, LM Studio, llama.cpp, caches, custom folders), so a store keeps its colour on every screen;
  "other files" is neutral grey and unaccounted space is a hatched grey. Benchmarked volumes take the
  first three slots. Light and dark steps are the reference palette's validated values; status colours
  (warning, suggestion, note) always come with a word.
- **Why:** the palette is validated for colour-vision deficiency on adjacent pairs, which is what a
  stacked bar in a fixed order needs.
- **Status:** follows the design (0001).

## D-017: no SMART health in 0.1  (2026-10-18)

- **Question:** design §3.2 lists SMART health as optional and §10 a "SMART summary when available".
- **Choice:** not built in 0.1. `smartctl` needs administrator rights on most systems and isn't
  installed by default, so it would be "unknown" almost everywhere; design §16 says so.
- **Why:** AGENTS.md rule 3 (no administrator rights). A later change can read it when the user has set
  up `smartctl` to run without a password.
- **Status:** answered by the owner on 2026-11-05: hone-drive should read SMART health, and show per OS
  how to install `smartctl` and allow hone-drive to use it ("then we should have it"). That needs its own
  change record (a new data source, new instructions, possibly new advice); until it is built, 0.1 reads no
  SMART health, as design §3.2 and §16 say.

## D-018: A05 speaks only about volumes that matter  (2026-10-18)

- **Choice:** the filesystem advice (A05) is given for an external volume only when it holds a store or
  advice A02 would move one there, and it is a warning when a store that runs as its own user (Ollama on
  Linux, Docker) would move there.
- **Why:** an exFAT stick for moving files between computers needs no advice.
- **Status:** follows the design (0001).

## D-019: the cache fallback is what each OS can do, not a file twice the size of RAM  (2026-11-05)

- **Question:** design §6.2 said that when direct I/O is refused, the test writes a file at least twice
  the machine's RAM so the cache can't hold it. The code never did: on Linux it drops the file's pages
  with `posix_fadvise(DONTNEED)` (`cache: fallback`), elsewhere it marks the result `possibly-cached`.
- **Choice:** the design now describes the code. A `possibly-cached` result carries a warning, is shown
  greyed out (drive card and chart) and is never used for a load or copy time.
- **Why:** on the reference machine (30 GB RAM) the larger file would be a 60 GB write, more than the free
  space safety rule 2 allows on most drives, and minutes of wear on a drive that refused direct I/O;
  direct I/O is refused only on tmpfs and some FUSE filesystems, where no one keeps models.
- **Status:** awaiting owner review (the owner asked on 2026-11-05 that code and docs agree).

## D-020: when a sustained write is "throttling"  (2026-11-05)

- **Choice:** after at least 5 s whose per-second speeds are each within ±15% of their median, three
  seconds in a row under 75% of that median label the measurement `throttling after N s`. A fall under half
  the first seconds' speed is reported first, as `cache exhausted after N GB`; anything else is `steady`.
- **Why:** design §6.4 names the label ("a drop after a steady stretch") without numbers. ±15% is the
  tolerance design §4.2 already uses for "link-bound", and three seconds keep one slow second from
  counting. Not yet checked against a real throttling drive.
- **Status:** the owner left the numbers open on 2026-11-05 ("I don't know"); they stand as provisional
  until a sustained-write curve from a real throttling drive confirms or changes them.

## D-021: which advice has commands (guarantee 11)  (2026-11-05)

- **Question:** guarantee 11 says every advice item has commands for the current OS; A01 (full), A04
  (slow because full) and A09 (keep this file here) had none.
- **Choice:** guarantee 11 asks for commands on every item that asks the user to act (`warn` and
  `suggest`); an `info` item may only inform (today A09, "keep this file where it is"). A01 shows
  `hone-drive scan --account` (what fills the drive) and A04 `hone-drive bench <mount> --tests seq`
  (check the speed after freeing space). A rule whose OS has no commands written yet gives no advice on
  that OS (A06 on macOS and Windows) instead of an item with nothing to run.
- **Why:** a rule by severity is one a contributor adding an advice rule can follow and a test can check
  for every rule on every OS (`test_ac11_every_item_that_asks_for_action_has_commands`), where a list of
  named exceptions grows silently; a made-up command for "do nothing" helps no one.
- **Status:** the owner left it to the implementer on 2026-11-05 ("you decide what's best for an open
  source project").

## D-022: how the shelf is built  (2026-11-05)

- **Choice:** change 0003 named three modules; the shelf has six, each under the ~300-line guide:
  `shelf.py` (the public API), `shelf_move.py`, `shelf_restore.py`, `shelf_models.py`, `shelf_catalog.py`,
  `shelf_copy.py`, plus `access.py` and `cli_shelf.py`. The record's optional `Item.on_drive` was not
  needed: the views read the shelves directly, so the run's format is unchanged. The benchmark's lock
  became `bench_safety.lock_file`, holding "<host> <pid>", so a lock on a drive tells another computer's
  hold from a process that is gone.
- **Why:** one module per job keeps each readable; no change to the stored run means nothing to migrate.
- **Status:** follows change 0003.

