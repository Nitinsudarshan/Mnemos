# Mnemos

Second Brain — a voice-enabled local AI assistant built around an Obsidian vault as the single source of truth. See the project brief for full architecture and build order.

## Build status

**Step 1: Backend + Storage.** Done. A CLI that creates and reads `.md` notes in the vault with correct frontmatter (`source`, `created`, `tags`, `related_notes`).

**Step 2 (current): Retrieval.** Done. Notes are chunked (heading-aware, with sentence-level fallback for long unbroken paragraphs) and embedded into LanceDB (embedded mode, no server process) using `all-MiniLM-L6-v2`. Incremental re-indexing compares each note's mtime so unchanged notes are never re-embedded, and deleted notes are pruned from the index automatically. Still no LLM, no voice — those come in step 3+.

### Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

The first `reindex` run will download `all-MiniLM-L6-v2` from Hugging Face (~80MB) — this requires normal internet access on whatever machine you run it on.

### Usage

By default the vault is created at `./vault` and the LanceDB index at `./.mnemos/lancedb`. Override with `MNEMOS_VAULT_PATH` / `MNEMOS_INDEX_PATH` respectively — the index is deliberately kept outside the vault since it's a derived, rebuildable cache, not part of the source of truth.

```bash
# Create the vault folder structure (Notes/, Meetings/, Research/, Reference/, Journal/)
python -m backend.cli init

# Create a note
python -m backend.cli create --folder Notes --title "My note" \
  --content "Some text" --tags "idea,followup" --source dictation

# List notes
python -m backend.cli list
python -m backend.cli list --folder Notes

# Read a note back
python -m backend.cli read vault/Notes/2026-08-13-my-note.md

# Embed new/changed notes into LanceDB
python -m backend.cli reindex

# Semantic search over the vault
python -m backend.cli search "what did I decide about the app shell?"
python -m backend.cli search "grocery list" --k 3
```
