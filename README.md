# Mnemos

Second Brain — a voice-enabled local AI assistant built around an Obsidian vault as the single source of truth. See the project brief for full architecture and build order.

## Build status

**Step 1 (current): Backend + Storage.** A CLI that creates and reads `.md` notes in the vault with correct frontmatter (`source`, `created`, `tags`, `related_notes`). No voice, no LLM, no retrieval yet — those come in later steps.

### Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

### Usage

By default the vault is created at `./vault`. Override with `MNEMOS_VAULT_PATH` to point at a real Obsidian vault.

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
```
