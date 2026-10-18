---
name: bench-tests
description: Run or write full benchmark tests (tests/bench, marker bench) safely on a shared machine - the machine-wide lock, free-space checks, the temporary file removed, no speed assertions in the default suite. Use before running or changing anything under tests/bench or the benchmark and direct-I/O code.
---

# Benchmark tests

Details: CONTRIBUTING.md, "Benchmark tests and `scripts/gpu-lock.sh`". hone-drive calls no AI model; its
heavy tests write and read gigabytes on real drives. hone-drive has no benchmark tests yet.

**Run**
- Only through the lock: `scripts/gpu-lock.sh uv run pytest -m bench -q`. It waits for the machine-wide
  lock (`$HONE_GPU_LOCK`), so a benchmark never runs beside another project's model run.
- Point them at a volume with `HONE_DRIVE_TEST_VOLUMES` (paths separated by the OS's path separator);
  without it they use the temporary folder's volume, and skip with a reason when it lacks free space.
- Never point them at a volume you can't spare the space on; they follow design §6.3 like any run.

**Write**
- Put the test in `tests/bench/` with `@pytest.mark.bench`.
- Assert shapes and invariants (the method of cache bypass recorded, the temporary file gone, the curve
  has one point per second), never a speed: speeds depend on the machine.
- The default suite's benchmark test stays at 64 MiB.

**Report** what ran, on which drives, and what was skipped, with reasons, in the PR's "How it was tested".
