# 0001: Initial design: a storage dashboard for AI developers (v0.1)

## Status

`proposed` 2026-10-18. Awaiting the owner's review. Nothing is implemented until it is `accepted`. The
open questions are listed in [decisions.md](../decisions.md#awaiting-owner-review).

## Context

On 2026-10-18 the owner's laptop disk was 94% full and they had bought an external SSD. Working out what
to do took a whole session of shell commands, sudo round trips and two wrong conclusions
([history/0000-research.md](../history/0000-research.md)):

- the first scan accounted for half the used space; the other 362 GB turned out to be Ollama models in a
  service user's folder that an unprivileged scan cannot read;
- the SSD, sold as a 20 Gbps drive, linked at 10 Gbps on both ports tried, which only sysfs lane counts
  revealed;
- the first benchmark numbers were wrong because a script cut the decimal comma of the owner's locale;
- the internal drive turned out to write at a quarter of the external drive's speed because it was
  nearly full.

The owner then asked for a new hone-* project, in their words: one that "determines the external SSDs and
does some checks on them", "a visual dashboard", "open sourced", working "on Linux, Mac, Windows", "used
by AI developers like me, to get ideas like what we need to, to get guidance", that "checks the system,
checks the external drive, runs tests to tell differences", following the pattern of the other hone
projects.

## Problem

There is no single tool that answers an AI developer's storage questions: where the space went
(including folders other users and services own), what a drive and its link can actually do, what that
means for loading their own models, and what to move where with which settings. Disk benchmarks answer
in generic units and often measure RAM; disk-usage tools miss what they can't read and know nothing about
model stores; link speed is buried in OS-specific places; none of them advise.

## Options

1. **A guide only** (a Markdown page of commands per OS). Cheap, but every user repeats this session's
   mistakes, and nothing measures or compares.
2. **Wrap existing tools** (`fio`, `CrystalDiskMark`, `ncdu`, `smartctl`) behind a page. Strong
   benchmarks, but different tools per OS, installs users must make first, and output parsing (the very
   mistake of history §2).
3. **A Python package with its own probes, store finders and benchmarks, and a local dashboard.**
   Installs with one command on all three systems, needs nothing else, shares one data model between the
   dashboard, CLI and API, and can still use `smartctl` or `fio` when present.
4. **A desktop app** (Electron or Tauri). Polished, but a heavy build per OS and far from the family's
   Python packages and conventions.

## Decision

Option 3, as described in [design/current.md](../current.md):

- a `Probe` port with Linux, macOS and Windows implementations that use only the OS's own read-only
  tools and files, with "unknown, and why" for what an OS can't tell (§3);
- links described as negotiated against possible, with the likely cause of a gap (§4);
- store finders for Ollama, Hugging Face, ComfyUI, LM Studio, llama.cpp, Docker and common caches, sizing
  by unique files, byte-identical duplicates only, and unaccounted space with its likely hiding places
  (§5);
- benchmarks that bypass the page cache on every OS, including reads of the user's own model files, with
  strict safety rules for the one temporary file they write (§6);
- estimates in seconds per model and drive (§7), and ten advice rules that cite their measurements and
  show commands without running them (§8);
- stored runs, comparisons and privacy-safe reports (§9, §13);
- a dashboard served locally by the standard library, offline, as in Hone Frame (§10);
- tests against recorded probe outputs from real machines, and CI on all three systems (§14).

The name is **hone-drive** (import `hone_drive`, command `hone-drive`), pending the owner's choice
(decision D-003). Local git only until the owner says to publish.

### Milestones

| Milestone | Delivers |
|---|---|
| M1: scan | the data model, `LinuxProbe`, the store finders, sizing, unaccounted space, `scan` in the API and CLI with `--json`, runs on disk |
| M2: benchmarks | the five tests with direct I/O on Linux, the safety rules, estimates, `bench` |
| M3: advice and dashboard | the ten advice rules, the dashboard's six screens, reports, `compare` |
| M4: macOS and Windows | `MacProbe`, `WindowsProbe`, direct I/O on both, recorded fixtures from real machines, CI on three systems |

Each milestone ends with `scripts/check.sh` green, the acceptance cases it covers passing, and the docs in
step. The reference machine (Linux, NVMe plus the PNY USB SSD) is the first real fixture.

## Consequences

- Developers get one command (`uvx hone-drive`) that answers this session's questions in a minute, with
  numbers they can trust and share.
- Probes per OS are the bulk of the maintenance: OS updates change tool output (macOS 15 renamed its USB
  data type). Recorded fixtures make such breaks visible and easy to fix.
- Python-level benchmarks are honest for sequential reads and writes and for latency, and approximate for
  deep-queue random I/O (§16); `fio` can be added later as an optional engine.
- Advice without actions keeps 0.1 safe; users still run the commands themselves. Applying advice
  (moving a store and changing its setting) would be its own change, with its own safety design.
- A new core dependency, psutil (decision D-001).

## Migration and compatibility

A new package; nothing to migrate. The run JSON format (§9) carries a `format` version from the start so
later versions can read older runs.
