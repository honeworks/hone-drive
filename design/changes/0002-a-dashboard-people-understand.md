# 0002: A dashboard people understand

## Status

`accepted 2026-10-18 by the owner` ("UI is awful. not understandable. not usable, make it much better"),
and implemented the same day in 0.0.1.

## Context

The first dashboard ([0001](0001-initial-design.md), design §10) showed the data model almost as it is
stored: mount points (`/run/media/…/PNYRP60PSSD`) instead of drive names, test ids (`seq-read`,
`link-bound`), rule ids (`A07`), twenty advice cards in one list with raw commands, and a benchmark form
that asked the user to pick volumes and tests. The owner, the first user, found it neither
understandable nor usable.

## Problem

An AI developer opens the page with three questions: is something wrong, what is filling my disks, and
what should I do. The first dashboard answered none of them at a glance; every answer needed the
vocabulary of the design document.

## Options

1. Polish the existing screens (spacing, colours) and keep their structure.
2. Rebuild the screens around the user's questions, in plain words, with the server computing the
   words, names and verdicts so the page only draws.
3. A wizard that walks through one fixed flow.

## Decision

Option 2. The run and the advice are unchanged; a presentation layer (`presentation.py`, `views.py`)
turns them into what people read:

- **Names, not mount points:** "Internal drive", "PNY RP60 PSSD"; the advice text itself uses them
  (and the CLI with it), with the mount point given once where it matters.
- **Verdicts with a tone:** each drive card says how full it is, whether it runs at full speed, and how
  fast it is in seconds for a 20 GB model, with a coloured dot and words (never colour alone).
- **A headline:** one sentence for the most important finding and a button to what to do about it.
- **A plan instead of a list:** the advice grouped into ordered steps (clean caches, prepare the
  external drive, move model folders, get full speed, find hidden space), each with what it frees, a
  checkbox per task kept in the browser, and the commands behind "How to do it". Findings (full, slow)
  are shown as findings, not tasks.
- **One-click speed tests:** "Test speed" on each drive card and "Test all drives" (seq and model reads,
  4 GiB), with a progress bar that says what is happening on which drive, in words.
- **Screens by question:** Overview, Plan, Space, Drives & speed, Models, History; each screen opens with
  one sentence saying what it is for. Technical details stay one click away.

## Consequences

- The page is understandable without the design document; the raw facts are still in the run JSON, the
  CLI's `--json` and each drive's "Technical details".
- Advice titles changed wording (names instead of mount points); scripts that matched them by text
  break. Rule ids are unchanged, so match on those.
- The JavaScript is split in three modules (`ui.js`, `app.js`, `screens.js`); still no build step.

## Migration and compatibility

No stored format changed. Runs saved before this change show their stored advice until the next scan or
benchmark recomputes it.
