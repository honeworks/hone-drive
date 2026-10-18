# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow semantic versioning. Why the
design changed is in [`design/changes/`](design/changes/).

## [Unreleased]

### Added

- Milestone M3 of [0001](design/changes/0001-initial-design.md), advice and the dashboard: the ten
  advice rules with per-OS commands that hone-drive never runs (D-018); run comparison; reports with
  private details removed; the local dashboard (Overview, Drives, Benchmark, Models, Advice, History)
  with live benchmark progress, guarded against DNS rebinding and cross-site requests (D-016);
  measurements kept across scans (D-015); `hone-drive advise`, `compare`, `report`, `dashboard` (the
  default command). No SMART health yet (D-017).
- Milestone M2 of [0001](design/changes/0001-initial-design.md), the benchmarks: sequential write and
  read, sustained write with a per-second curve, random reads (latency and 32 in flight) and reads of
  real model files, with the page cache bypassed on Linux, macOS and Windows and the method recorded
  (D-013); the safety rules of design §6.3 with a ledger of temporary files (D-014) and one benchmark per
  machine; link-bound and drive-bound labels; load and copy time estimates; `hone-drive bench`.
- Milestone M1 of [0001](design/changes/0001-initial-design.md), the scan: the data model; the `Probe`
  port with Linux (sysfs), macOS (`diskutil`, `system_profiler`) and Windows (PowerShell storage
  cmdlets) probes; links as negotiated against possible; filesystem traits; store finders for Ollama
  (files, or its server when the folder is not readable, D-010), Hugging Face, ComfyUI (with
  `extra_model_paths.yaml`), LM Studio, llama.cpp, Docker and package caches; unaccounted space with its
  likely hiding places (D-011); byte-identical duplicates; runs on disk; `hone-drive scan`, `runs`,
  `show`; recorded machines for the tests (D-012).
- Repository created with the proposed design ([0001](design/changes/0001-initial-design.md)).
