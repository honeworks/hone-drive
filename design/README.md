# Why hone-drive exists

> Plug in an external SSD and open one page that tells you the truth about it: what it is, how it is
> connected, how fast it really reads and writes next to your internal drive, how long your own models
> would take to load from each, what is filling your disks, and what to move where.

## The problem

AI developers fill disks faster than anyone. A laptop with local models carries hundreds of gigabytes of
Ollama models, ComfyUI checkpoints, Hugging Face caches, Docker images and package caches, and the day it
runs out of space the obvious answer is an external SSD. Then the guessing starts:

- **"Where did my disk go?"** The usual tools miss the biggest folders. Model servers run as their own
  user (Ollama on Linux keeps its models in `/usr/share/ollama`), Docker hides its images behind root, and
  a plain `du` silently skips what it cannot read. On the machine this design started from, a normal scan
  found 424 GB of an 830 GB disk; the missing 362 GB were Ollama models nobody thought to look at.
- **"Is this drive fast enough for models?"** Box speeds are best cases. The drive in that session was
  sold as a 2,000 MB/s USB 3.2 Gen 2x2 drive, linked at 10 Gbps on both ports tried, and measured
  ~1 GB/s. Nothing on screen said why, or that a cable might fix it.
- **"How much slower will my models load?"** Developers want an answer in their own units: seconds to
  load *this* 19 GB model, *this* 28 GB video-model pair, from each drive. Generic benchmarks answer in
  MB/s at queue depth 32, and many quietly measure RAM because they don't bypass the page cache.
- **"Is my internal drive fine?"** A nearly full budget SSD gets slow. The same internal drive measured
  2.2 GB/s reads but only ~0.2 GB/s writes at 94% full, four times slower to write than the external
  drive. No tool connected "your disk is 94% full" to "this is why it is slow".
- **"What should I move, and how?"** Every model tool has its own settings for where models live
  (`OLLAMA_MODELS`, `HF_HOME`, ComfyUI's `extra_model_paths.yaml`, LM Studio's folder), some caches must
  stay on the same drive as projects to keep hard links, and an exFAT drive cannot hold files owned by a
  service user. Developers learn each of these by breaking something.
- **Three operating systems, three toolkits.** `lsblk` and sysfs on Linux, `diskutil` and
  `system_profiler` on macOS, PowerShell storage cmdlets on Windows. Each answers a different subset, in a
  different shape.

## The core ideas

1. **Measure, don't quote.** Every speed on screen was measured on this machine, with the page cache
   bypassed on every OS, and is labelled as measured; anything estimated says so and shows its inputs.
2. **Answers in an AI developer's units.** Load times for the models actually on the disk, per drive;
   space per model store (Ollama, Hugging Face, ComfyUI, LM Studio, llama.cpp, Docker, package caches);
   duplicates found by content, not by name.
3. **Explain the link.** What the port, the cable and the drive negotiated against what each can do
   (10 Gbps of a possible 20, one lane of two), with the likely cause and what to try.
4. **Account for every gigabyte.** The space a scan can see is compared with what the filesystem reports
   used; the gap is shown, with the places it is likely hiding and the command that would measure them.
5. **Advice you can check.** Every recommendation names the measurements behind it and the gain ("frees
   362 GB; this model loads in 19 s instead of 9 s"), and shows the exact commands for your OS. hone-drive
   0.1 never moves, deletes or formats anything itself.
6. **One page, three systems.** A local dashboard served from the package on Linux, macOS and Windows,
   working offline, with the same facts available from the command line and the Python API.

## Non-goals (0.1)

Moving, deleting or copying files; formatting or partitioning drives; anything that needs administrator
rights (it says what more it could see with them, and how); raw device access; drive health beyond what
SMART tools already report; network storage benchmarks; a hosted service or telemetry; comparing your
results with other people's (a later change could add an opt-in shared results format).

## What is in this folder

| File | What it holds |
|---|---|
| [`current.md`](current.md) | the design as it stands today: concepts, rules and the guarantees the tests check |
| [`changes/0001-initial-design.md`](changes/0001-initial-design.md) | the first design, from the owner's brief (proposed) |
| [`history/0000-research.md`](history/0000-research.md) | the session that started it: what was measured, what went wrong, what was learned |
| [`decisions.md`](decisions.md) | smaller choices, with the open questions for the owner |

## How this is being built

Specified by a human; designed and built by AI against a written design and tests; every decision is
reviewed by a human. The design history is in [changes/](changes/) and [decisions.md](decisions.md).
