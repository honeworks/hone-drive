# 0003: The model shelf: move models to a drive and bring them back

## Status

`accepted 2026-11-05 by the owner ("accepted")`, and implemented the same day (unreleased; the next
version after 0.0.1). Proposed the same day. Asked for by the owner the same day ("I want it to move models to the drive. Copy,
make sure the model is copied ok, delete from machine. Then I want to be able to select I want this model,
and it should copy it back"), with the answers recorded below; the open questions of the first draft were
answered the same day (see "The owner's answers to the first draft").

## Context

hone-drive 0.1 finds what fills the disks and says what to move, but it never moves anything (design §1
non-goals, D-007, AGENTS.md rules 3 and 7, README "What it will never do"). Change 0001 said so on
purpose: "Applying advice ... would be its own change, with its own safety design." This is that change.

On the reference machine the internal drive is 89% full and an empty 1 TB PNY SSD sits next to it. The
owner's models are in Ollama (388 GB, in the service's folder `/usr/share/ollama`, which only the
`ollama` user can open: `drwx------`), ComfyUI (123 GB) and the Hugging Face cache. The owner wants to put
models they don't use now on the SSD, free the space, and bring a model back when they need it.

The owner's answers (2026-11-05):

1. Models are **used from the computer**. The drive is a shelf: a model on it is stored, not used, until
   it is brought back. ("For now, let's just copy and use from system.")
2. Whether hone-drive can read and change a tool's folder **depends on the system**: it must check on
   each machine and, when it can't, show the right command for that system. ("It depends on the system.
   What if it was ok on one system or OS? So it needs to check, give the right command for the fix.")
3. A model that is in use is **refused**, with the reason.
4. Scope: Ollama, ComfyUI, Hugging Face, LM Studio, llama.cpp and the user's own folders. Not Docker,
   not package caches (cleaning them is the right answer, design §8.1 A07).
5. When the drive is unplugged, its models are still listed as "on PNY RP60, not connected"; bringing one
   back waits until the user plugs the drive in and clicks again.

The owner also asked that moving be **idempotent**: "Copy my models to the drive" checks whether the
model is already there with the same bytes, copies only what is missing or different, and then removes
the local copy.

## Problem

Moving a model by hand is easy to get wrong, and the mistakes lose data or break a tool:

- an interrupted copy, or one that silently wrote bad bytes, followed by deleting the original;
- deleting an Ollama blob that another model still uses (two names often share their weights, design
  §5.2), or a Hugging Face blob a snapshot still links to;
- a folder the user can't read (Ollama's service folder on Linux), worked around with `sudo` and files
  then owned by the wrong user;
- a model in use by a running ComfyUI or loaded in Ollama;
- a drive that can't hold the files: FAT32 stops at 4 GB, exFAT can't store the symbolic links a Hugging
  Face snapshot is made of;
- not knowing, weeks later, what is on which drive.

## Options

1. **Leave it as it is.** Keep showing commands (A02 moves whole stores with `rsync` or `robocopy`). Safe
   for hone-drive, but every user does the checking, the shared-file bookkeeping and the bringing back
   by hand, and A02 moves whole stores only: the owner wants to pick models.
2. **Whole stores, applied by hone-drive:** run A02's commands for the user and point each tool at the
   drive. Fewer moving parts, but it is the "use from the drive" design the owner set aside (answer 1),
   and it can't move one model.
3. **A shelf of single models, copied, checked and removed by hone-drive, and brought back the same
   way.** Models stay usable only from the computer; the drive holds a catalog of what is on it.
4. **Call each tool's own commands** (`ollama rm`, `huggingface-cli delete-cache`, ...). Each tool covers
   only its own models, few can export or import, and their output would have to be parsed (history §2).

## Decision

Option 3: **the shelf**. hone-drive copies a model to a drive, checks the copy byte for byte, and only
then removes it from the computer; it brings a model back the same way. It changes files only when the
user starts one of these actions, never needs administrator rights while it runs, and still never runs a
command for the user (commands for setup are shown, as before).

### What "a model" is, per tool

| Tool | One model is | Shared files |
|---|---|---|
| Ollama | a manifest (`manifests/<host>/<namespace>/<name>/<tag>`) and the blobs it lists | blobs shared with other manifests stay on the computer while any other model there lists them |
| Hugging Face | one repo folder (`models--org--name`: `blobs/`, `snapshots/` of links to blobs, `refs/`) | none outside the repo |
| ComfyUI, LM Studio, llama.cpp, own folders | one model file (the items of design §5.1) | none |

Names are the ones the dashboard already shows: `ollama:gemma4:31b`, `huggingface:org/name`,
`comfyui:diffusion_models/wan2.2.safetensors`.

### On the drive

```
<drive>/hone-drive/
  drive.json          an id written the first time, so the drive is found wherever it is mounted
  shelf.json          the catalog: every model, its tool, names, original paths, files with size and SHA-256,
                      the links to recreate (Hugging Face), and its state
  ollama/blobs/sha256-<hex>          Ollama blobs, each once even when several models use it
  ollama/manifests/...               the manifests, as Ollama lays them out
  huggingface/models--org--name/...  blobs and refs; the snapshot links are kept in shelf.json
  comfyui/<install>/<subfolder>/<file>, lmstudio/..., llamacpp/..., custom/<folder>/...
```

Links are stored in the catalog, not as links, so an exFAT drive can hold a Hugging Face repo. A file over
4 GB is refused on FAT32 with the reason (design §4.3 `large_files`). A copy of `shelf.json` is kept in the
data folder per drive id, so the dashboard and `hone-drive shelf` list the models on a drive that is not
plugged in (answer 5).

### Moving a model to the drive

1. **Refuse** when the model is in use (answer 3): Ollama has it loaded (`/api/ps` on its local server);
   ComfyUI's server answers on its port (default `127.0.0.1:8188`; "close ComfyUI first"); for the other
   tools, a process of this user has the file open (psutil, best effort, said so). Refuse also when the
   drive lacks the room (the missing files plus 10% plus 2 GiB, as design §6.3 rule 2) or the access
   check below fails.
2. **Compare first** (idempotence), trusting the catalog: a file the catalog lists as checked, whose size
   and modification time on the drive and on the computer are still the ones the catalog recorded when
   it was checked, is the same and is not copied or read again. Any other file is hashed on both sides;
   matching files are not copied again.
3. **Copy** what is missing or different to `<name>.part`, hashing as it reads; `fsync`, then rename.
4. **Check**: read the copy back from the drive with the page cache bypassed (`directio`, design §6.2) and
   compare its SHA-256 with the local file's. For Ollama blobs the expected hash is also the blob's name.
   Every new copy is checked this way; only step 2's comparison of earlier copies trusts the catalog.
5. Mark the model `on drive` in `shelf.json` with each file's hash, size and modification time on both
   sides (written atomically, then copied to the data folder).
6. **Remove it from the computer** (skipped with `--keep-local`):
   - Ollama: through its server (`DELETE /api/delete`) when it answers, so Ollama itself drops the
     manifest and the blobs no other model uses; otherwise the manifest and those blobs directly;
   - Hugging Face: the repo folder; the others: the file.
7. Mark it `shelved`. A model is never removed from the computer before step 5 is on the drive.

Running it again on a shelved model does nothing; on a model that came back and is still on the drive,
step 2 finds every file unchanged and the move is only a delete (a second or two).

### Bringing a model back

The same steps the other way: room on the computer, copy to `.part` in the model's original folder,
check against the catalog's SHA-256 with the cache bypassed, rename into place; for Hugging Face recreate
the snapshot links; for Ollama put the blobs and then the manifest in the store's folder (the manifest
last, so Ollama never lists a model whose blobs are missing). Bringing back **only copies**: it never
removes anything from the drive (owner, 2026-11-05: "We never delete a model ... bringing it back is
never part of copying to system"). Moving the model to the drive again later is then only a delete.

### Removing a model from the drive

A separate action, "Remove from drive", is the only way anything leaves the drive. It never touches the
computer's copy. When the drive holds the **only** copy (the model is not on the computer, or not with the
same bytes), it refuses unless the user confirms that the model will be gone (the dashboard asks them to
type the model's name; the CLI needs `--only-copy`). Ollama blobs other shelved models list stay on the
drive.

### Checking access per system (answer 2)

Before any change, `shelf.check()` tests what this machine allows, instead of assuming it from the OS:
it tries to list and read the store's folder, to create and remove a test file in it (named like the
benchmark's, `.hone-drive-check-*`), and asks the tool's server whether it answers. When something
fails it says what and shows the command that fixes it **on this system**, built from what it found
(owner, group, the user's groups, which tools are installed):

| Found | Command shown (never run) |
|---|---|
| Linux, folder owned by a service user (Ollama's `ollama`), `setfacl` installed | `sudo setfacl -R -m u:<user>:rwX,d:u:<user>:rwX,d:u:ollama:rwX /usr/share/ollama`, with the real user name filled in (this user, and no one else, may read and write it, also files added later; files this user brings back stay readable by Ollama) |
| Linux, no `setfacl`, or a filesystem without ACLs | the package that provides it (`acl` on pacman, apt and dnf); without ACLs, run the service as this user instead (the systemd override is shown) |
| macOS, folder not readable | `sudo chmod -R +a "<user> allow read,write,delete,add_file,add_subdirectory,list,search,file_inherit,directory_inherit" <folder>` |
| Windows, folder not readable | `icacls "<folder>" /grant "<user>":(OI)(CI)M /T` in an administrator PowerShell |
| ComfyUI or Ollama running and the model in use | which one, and "close it or unload the model, then try again" |

Why a rule for one user and not the service's group (owner's question 3: "pick the best option"): a
group rule would also let anyone added to that group later change model files; running Ollama as the
user would change their service setup and move 388 GB. A rule naming only this user changes nothing
else, Ollama keeps running as it is, and `sudo setfacl -R -x u:<user>,d:u:<user> /usr/share/ollama` undoes it.
On the reference machine the first row applies; on a Linux machine where Ollama runs as the user, on
macOS and on Windows the check usually passes with nothing to do. The check runs again when the user
clicks "Check again", so they see when the fix worked.

### Interruptions and two at once

- Every step can be stopped. A stop, an error or a killed process leaves each model complete in at least
  one place: `.part` files are never the only copy, and nothing local is removed before the drive's
  catalog says the copy checked out.
- The catalog records each model's state (`copying`, `on drive`, `shelved`, `restoring`); the next run
  resumes from it, re-checks what was copied and removes leftover `.part` files.
- One shelf job at a time per machine (the dashboard's single job, `jobs.py`), and a lock file in
  `<drive>/hone-drive/` taken like the benchmark lock (design §6.3 rule 5), so two computers or two
  processes never write one drive's catalog at once.

### The public API, CLI and dashboard

```python
from hone_drive import scan, shelf

run = scan()
print(shelf.check(run, "ollama"))                 # Access(ok=False, problem=..., fix=["sudo setfacl ..."])
plan = shelf.plan(run, ["ollama:gemma4:31b", "comfyui:diffusion_models/wan2.2.safetensors"],
                  to="/run/media/user/PNYRP60PSSD")  # what would be copied, checked, removed; nothing changes
shelf.move(run, plan)                               # copy, check, remove from the computer
shelf.contents()                                    # every drive's shelf, connected or not
shelf.restore(run, ["ollama:gemma4:31b"])           # copy back, check; the drive keeps its copy
shelf.remove("PNY", ["ollama:gemma4:31b"], only_copy=False)  # from the drive only; refused if it's the only copy
shelf.verify("PNY")                                 # "Check drive": re-hash everything against the catalog
```

```
hone-drive shelf                          # what is on each drive, connected or not
hone-drive shelf check [TOOL]             # can hone-drive move this tool's models here? the fix if not
hone-drive shelf move MODEL... [--to DRIVE] [--keep-local] [--dry-run]
hone-drive shelf restore MODEL... [--dry-run]
hone-drive shelf remove MODEL... [--from DRIVE] [--only-copy]
hone-drive shelf verify [DRIVE]
```

The Models screen gets a checkbox per model, "Move to PNY RP60", "Bring back" and "Remove from drive", and a column saying
where each model is (computer, drive, both). The progress bar is the benchmark's, in words ("checking
gemma4:31b on PNY RP60, 12 of 19 GB"). Before a move, a confirmation lists what will be removed from the
computer and the space it frees. A failed access check shows its fix in the same place.

### New guarantees (design §15)

17. A file is removed from the computer only when the catalog on the drive records a copy that was read
    back with the page cache bypassed and matched the local file's SHA-256, and neither file's size or
    modification time changed since; otherwise both are hashed again first.
18. Moving a model whose files are already on the drive with the same bytes copies nothing and only
    removes the local copy; moving or bringing back a model twice ends in the same state as once.
    Bringing a model back never removes anything from the drive.
19. A move or a restore stopped, failed or killed at any point leaves every model complete in at least
    one place, and the next run finishes it or cleans up after it.
20. A model in use (loaded in Ollama, ComfyUI running, the file open in a process) is refused with the
    reason, and nothing is changed.
21. An Ollama blob is removed from the computer only when no model left there lists it.
22. When hone-drive can't read, write or remove a store's files, `check()` says so with the command for
    this system, which it never runs; nothing runs with administrator rights.
23. A model brought back works with its tool: Ollama lists it with the same weights digest; ComfyUI, LM
    Studio, llama.cpp and the user's folders find the file at its original path; the Hugging Face repo
    has the same snapshot.
24. A drive's shelf is listed when the drive is unplugged, and the drive is recognised by its id wherever
    it is mounted.
25. A move is refused when the drive lacks the room or its filesystem can't hold a file.
26. Removing a model from a drive never touches the computer's copy, and is refused without an explicit
    confirmation when the drive holds the only copy.

### Tests

- Unit: the catalog (reopened after writing), the compare-first step (a changed size or modification
  time forces a hash), shared Ollama blobs, Hugging Face
  links on a filesystem without links, the access check with fake folders and permissions, the fix
  command per finding and OS, refusals.
- Acceptance (`tests/e2e/test_ac17_...` to `test_ac25_...`): fake stores in temporary folders and a fake
  Ollama server; AC19 with a subprocess killed mid-copy and mid-restore, as AC8 does.
- Real tools (`-m real`, new marker, not in the default suite or CI, run by hand under
  `scripts/gpu-lock.sh`): pull a small Ollama model, move it to a temporary "drive", check `ollama list`
  no longer shows it, bring it back, check `/api/show` gives the same digest and the model answers; the
  same for a ComfyUI install's model file. Run on the reference machine before the record is marked
  implemented; macOS and Windows until a real machine is available: CI with fake stores (D-012).

## Consequences

- hone-drive changes files for the first time. The promise becomes: **it changes nothing unless you click
  move or bring back, it checks every byte before it removes anything, and it never needs administrator
  rights while it runs.** The README's "What it will never do", design §1 (goals, non-goals,
  responsibilities), AGENTS.md rules 3 and 7, CONTRIBUTING and D-007 are rewritten to say that; advice
  still never runs a command.
- A model on the shelf is the only copy. The confirmation says so, and says which ones can be downloaded
  again (Ollama and Hugging Face models) and which may not (a ComfyUI file from elsewhere, a folder of
  the user's own).
- Checking costs time on the first move only: it reads the model on the computer once, writes it to the
  drive and reads it back (19 GB at the reference machine's measured speeds: about 9 s, 21 s and 20 s). A
  repeated move trusts the catalog and reads nothing when the files are unchanged. The catalog can't see
  a file changed without its size or modification time changing (a disk fault, a tool that resets the
  time); the drive's copy was checked when it was written, and "Check drive" re-hashes everything on
  demand.
- New modules: `shelf.py` (plan, move, restore, catalog), `shelf_stores.py` (what a model is per tool),
  `access.py` (the check and the fix commands). They write files, so they follow the benchmark's safety
  style (design §6.3) and stay outside the read-only probes.
- Advice A02 (move a whole store) stays as commands; the Plan screen's "Move big model folders" step
  links to the Models screen for single models.
- No new dependency (psutil already lists open files).

## Migration and compatibility

New optional data only: the run is unchanged (the shelf lives on the drive and in
`<data folder>/shelves/<drive id>.json`), `shelf.json` carries its own `format: 1`. The data model gains
an optional `Item.on_drive` (where a shelved model is) so the views can show it; runs written before read
as before.

## The owner's answers to the first draft (2026-11-05)

1. Keep the drive's copy after bringing a model back: yes, bringing back never removes anything from the
   drive; a separate "Remove from drive" action does ("We never delete a model. We can have a button for
   deleting from drive").
2. Checking before a removal: "Trust catalog" (compare-first, step 2; every new copy is still read back).
3. Access to Ollama's service folder: "Pick the best option": a rule for this user only (see "Checking
   access per system").
