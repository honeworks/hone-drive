# AGENTS.md: hone-drive

A brief for AI coding tools (Claude Code, Codex, Cursor and others) helping with this repository.
People follow the same rules; they are in [CONTRIBUTING.md](CONTRIBUTING.md).

## What this is

`hone-drive` (import `hone_drive`) is a storage dashboard for AI developers, on Linux, macOS and Windows.
It finds the machine's drives and how each is linked, sizes the model stores and caches that fill them,
benchmarks drives with the page cache bypassed, turns the results into load times for the user's own
models, and gives advice with commands it shows but never runs. A local dashboard, a command line and a
Python API share one data model. Read [design/current.md](design/current.md) before changing behaviour;
its §15 lists the guarantees the tests check. Why the design looks like this:
[design/changes/](design/changes/) and [design/decisions.md](design/decisions.md).

**Status:** the design (0001) is `proposed`. Do not implement anything the owner has not accepted.

## Commands

```bash
uv sync --all-extras                 # install everything (dev included)
scripts/check.sh                     # all quality gates: lint, format, types, tests + coverage, build, wheel smoke test
uv run pytest                        # default suite (fast, offline, deterministic)
scripts/gpu-lock.sh uv run pytest -m bench   # full-size benchmarks on real drives, under the machine-wide lock
```

## Rules

1. **Simple first** ([CONTRIBUTING.md](CONTRIBUTING.md#keep-it-simple)): the plainest code that meets the
   guarantees; complexity ≤ 10, functions ~40 lines, modules ~300 lines.
2. **Design before code:** a new need or change starts as `design/changes/NNNN-<name>.md` with status
   `proposed`; the maintainer accepts it before it is built. Small choices go into `design/decisions.md`.
3. **Read-only, no administrator rights:** probes run only read-only commands and files, each with a
   timeout; nothing runs as administrator; nothing is moved, deleted or formatted. The only file written
   outside the data folder is the benchmark's temporary file, removed on every path (design §6.3).
4. **OS specifics stay in the probes and the direct-I/O module:** everything else works on the data model
   and runs the same on Linux, macOS and Windows (design §3).
5. **Unknown is `None` with a reason:** never guess, never show zero for a fact an OS can't provide
   (design §2).
6. **Measured or labelled:** a speed is measured with the cache bypassed and records how; estimates say
   they are estimates; throughput is computed from monotonic time, never parsed from another tool's
   output (design §6.2, §7).
7. **Advice never acts:** it cites measurements and shows commands per OS; hone-drive never runs them
   (design §8).
8. **Privacy:** reports remove serials, UUIDs, user and host names unless asked to keep them; nothing
   leaves the machine (design §13).
9. **The default suite is offline, fast and deterministic** and asserts no speeds; full benchmarks
   (`-m bench`) run only through `scripts/gpu-lock.sh`.
10. **Git**: Conventional Commits, small commits; AI-written commits end with a `Co-Authored-By:` line.
    Push a branch and open a pull request only when the user asks ("push", "ship it"). Inside that pull
    request's flow, reviewing it on GitHub and pushing fixes the user asked for need no new request.
    Never push to `main`, tag or publish unless the maintainer asks.
