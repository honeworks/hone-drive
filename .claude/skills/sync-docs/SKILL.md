---
name: sync-docs
description: Update every file a change affects - design/current.md, docs/, README, examples, CHANGELOG, AGENTS.md, the change record. Use after implementing any change and before verify-before-done; reviewers use the same table to find missing updates.
---

# Keep the docs in sync

Look at `git diff main...HEAD --stat` and the diff itself, then apply every row that matches:

| If the change... | Update |
|---|---|
| sets or changes the purpose, goals or non-goals | `design/README.md`, `design/current.md` §1, `README.md`, `AGENTS.md` "What this is", the `pyproject.toml` description |
| changes behaviour or the public API | `design/current.md` (the section, §12 public API, §15 guarantees), the matching `docs/` page, `README.md` if the quickstart or feature list changes |
| adds or changes a CLI command or flag | a CLI page in `docs/`, `design/current.md` §11 |
| adds or changes a stored format (the run JSON, reports) | its page in `docs/`, `design/current.md` §9 or §13 |
| adds or changes a store finder, an advice rule or a probe fact | the tables in `design/current.md` §3.2, §5.1 or §8.1, recorded fixtures for every OS it supports, `CHANGELOG.md` |
| adds an extra or a dependency | `pyproject.toml`, the install section of `README.md`, the reason in `design/decisions.md` |
| adds a concept users should know | a new example (`add-example`) |
| adds, moves or renames a module | the "Layout" of `AGENTS.md` (added with the first module), `design/current.md` §2 |
| is visible to users | `CHANGELOG.md`, top (unreleased) section: Added / Changed / Deprecated / Removed / Fixed, linking the change record |
| implements a change record | its status: `implemented in <next version>` |
| is a judgment call without a record | `design/decisions.md`, next `D-` number |
| changes a rule or command contributors use | `CONTRIBUTING.md`, `AGENTS.md`, and the skill in `.claude/skills/` that describes it |

Rules:
- Code in `README.md` and `docs/` should run; once there is public API, add a test in `tests/e2e/` that
  runs every `python` block, and write blocks that must not run as `text`.
- Write for users of the package: plain words, the style of the page around it, no internal notes.
- Run `uv run pytest -q` when done.
