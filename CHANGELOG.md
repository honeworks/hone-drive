# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow semantic versioning. Why the
design changed is in [`design/changes/`](design/changes/).

## [Unreleased]

### Changed

- The code and the docs agree again (an audit on 2026-11-05). Behaviour restored to match the design:
  advice A07 no longer offers `docker volume prune` or `docker container prune` as safe cleaning;
  duplicates are found inside Hugging Face repos; reports replace store paths outside the home folder and
  the tools' default folders with `<custom>`; copy times fall back to link speeds and say so; random
  reads run 20 s and are timed as they ran; sustained writes are labelled `throttling after N s`; the
  dashboard greys out a speed the OS cache may have served and notes models that may load warm; `runs`
  and `compare` take `--json`; A01 and A04 show the command that checks them again. Elsewhere the
  design, CONTRIBUTING and AGENTS now describe what the code does (D-019 to D-021).

- The dashboard, rebuilt around the user's questions ([0002](design/changes/0002-a-dashboard-people-understand.md)):
  drives by name, verdicts in words, a headline, the advice as an ordered plan with checkboxes, one-click
  speed tests with a progress bar in words. Advice text names drives instead of mount points.

### Fixed

- A USB SSD whose bridge reports it as rotational is recognised as an SSD; a model name that already
  names its maker is not prefixed with the vendor again. The Linux USB fixture is now a real recording.

### Added

- Milestone M4 of [0001](design/changes/0001-initial-design.md), macOS and Windows: the probes and
  direct I/O for both, tested against hand-written fixtures and in CI on Ubuntu, macOS and Windows; not
  yet checked on a real Mac or PC (D-012).
- Milestone M3 of [0001](design/changes/0001-initial-design.md), advice and the dashboard: the ten
  advice rules with per-OS commands that hone-drive never runs (D-007); run comparison; reports with
  private details removed; the local dashboard (Overview, Drives, Benchmark, Models, Advice, History)
  with live benchmark progress, guarded against DNS rebinding and cross-site requests (D-005);
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
