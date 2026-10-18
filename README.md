# hone-drive

**A storage dashboard for AI developers.** Plug in an external SSD and see the truth about your drives:
how each one is connected (and whether the cable is holding it back), how fast it really reads and writes
with the page cache bypassed, how many seconds your own models would take to load from each, which model
stores and caches fill your disks (Ollama, Hugging Face, ComfyUI, LM Studio, llama.cpp, Docker, uv, pip,
npm …), where the space you can't see is hiding, and what to move where, with the exact commands for your
system. Linux, macOS and Windows; works offline; needs no administrator rights.

Part of **[honeworks](https://github.com/honeworks)**: small, standalone tools for reliable generative-AI
workflows.

> **Status: in development.** The design is in [design/current.md](design/current.md), accepted in
> [design/changes/0001](design/changes/0001-initial-design.md).

## What it does (the plan)

1. **Drives and links:** every internal and external drive with its bus and the link it negotiated
   against what the port and drive can do ("10 of 20 Gbps, 1 of 2 lanes: try the original cable").
2. **Where the space goes:** model stores and caches sized by unique files, byte-identical duplicates
   across tools, and the used space no scan could read, with where it is likely hiding.
3. **Honest benchmarks:** sequential and sustained writes, sequential and random reads, and reads of your
   real model files, with direct I/O on every OS. The only file written is removed afterwards.
4. **Your units:** "this 19 GB model loads in ~9 s from the internal drive and ~20 s from the SSD".
5. **Advice:** ranked recommendations that cite the measurements and the gain, with commands to copy
   (`OLLAMA_MODELS`, `HF_HOME`, ComfyUI's `extra_model_paths.yaml`, mounting at boot, cache clean-ups).
   hone-drive never runs them.
6. **History and reports:** compare runs (after switching ports or freeing space) and share a report with
   private details removed.

```bash
uvx hone-drive            # opens the dashboard (planned)
uvx hone-drive scan --json
```

## How this is being built

Specified by a human; designed and built by AI against a written design and tests; every decision is
reviewed by a human. The design history is in [design/changes/](design/changes/) and
[design/decisions.md](design/decisions.md); the session that started it is in
[design/history/0000-research.md](design/history/0000-research.md).

## License

Apache-2.0.
