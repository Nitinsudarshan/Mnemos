# Mnemos

Second Brain — a voice-enabled local AI assistant built around an Obsidian vault as the single source of truth. See the project brief for full architecture and build order.

## Build status

**Step 1: Backend + Storage.** Done. A CLI that creates and reads `.md` notes in the vault with correct frontmatter (`source`, `created`, `tags`, `related_notes`).

**Step 2: Retrieval.** Done. Notes are chunked (heading-aware, with sentence-level fallback for long unbroken paragraphs) and embedded into LanceDB (embedded mode, no server process) using `all-MiniLM-L6-v2`. Incremental re-indexing compares each note's mtime so unchanged notes are never re-embedded, and deleted notes are pruned from the index automatically.

**Step 3: LLM / grounded RAG answers.** Done. `ask` retrieves the top-k relevant chunks and sends them to Ollama's OpenAI-compatible endpoint with a system prompt that instructs the model to answer only from those excerpts — and to say so plainly if the answer isn't in your notes, rather than guessing.

**Step 4: Voice.** Done. `transcribe` (Whisper STT) and `speak` (Piper TTS) work standalone, and `voice-ask` chains them around the existing `ask` pipeline: audio file in → transcript → grounded answer → spoken WAV out.

**Step 5: Desktop app (Tauri shell).** Done. A FastAPI HTTP layer (`backend/server.py`) exposes every CLI capability over localhost, and a Tauri v2 app (`shell/`) wraps it in a real desktop window: a chat UI, a global show/hide hotkey, in-app voice input, and universal dictation — hold a hotkey from any other application, speak, and the transcribed text is typed directly into whatever field had focus. See `HANDOFF.md` for the full sub-step breakdown, dev workflow, and open items.

**Step 6 (current): MCP connectors.** In progress. `backend/notion.py` adds read-only `search`/`fetch` against Notion's official hosted MCP server (`mcp.notion.com`) — the first and lowest-risk connector per the build order. Not yet verified end-to-end against a real Notion workspace; see the module docstring and Setup below before relying on it. Google Docs, Calendar, Email, and Messaging follow the same read-only-first pattern, in that order.

### Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

The first `reindex` run will download `all-MiniLM-L6-v2` from Hugging Face (~80MB) — this requires normal internet access on whatever machine you run it on. The first `transcribe`/`voice-ask` run will similarly download a Whisper model (~150MB for the default "base" size).

`ask` and `voice-ask` require [Ollama](https://ollama.com) running locally with a model pulled:
```bash
ollama serve
ollama pull gemma4:latest   # or whatever model you prefer — see below
```

`speak` and `voice-ask` require a Piper voice model downloaded once:
```bash
python -m piper.download_voices en_US-lessac-medium --download-dir ./piper-voices
```

`notion-search` and `notion-fetch` (Step 6) require a Notion access token — see the "Notion connector" section below.

### Usage

By default the vault is created at `./vault` and the LanceDB index at `./.mnemos/lancedb`. Override with `MNEMOS_VAULT_PATH` / `MNEMOS_INDEX_PATH` respectively — the index is deliberately kept outside the vault since it's a derived, rebuildable cache, not part of the source of truth.

For `ask`, Ollama's endpoint and model are configurable via `MNEMOS_LLM_BASE_URL` (default `http://localhost:11434/v1`) and `MNEMOS_LLM_MODEL` (default `llama3.1` — override this if you pulled a different model). Because Ollama exposes an OpenAI-compatible API, pointing `MNEMOS_LLM_BASE_URL` at a hosted provider later is a config change, not a rewrite.

For voice: `MNEMOS_WHISPER_MODEL` (default `base` — try `small` or `medium` for better accuracy at the cost of speed), `MNEMOS_PIPER_MODEL` (path to the `.onnx` voice file — required, no default), and optionally `MNEMOS_PIPER_CONFIG` (defaults to `<model path>.json` alongside it).

For the Notion connector: `MNEMOS_NOTION_TOKEN` (required, no default) and optionally `MNEMOS_NOTION_MCP_URL` (default `https://mcp.notion.com/mcp`).

### Notion connector (Step 6)

Read-only search and fetch against your Notion workspace, via Notion's official hosted MCP server rather than a bespoke wrapper around Notion's REST API. Nothing is created, edited, or sent — per the project's non-negotiable safety rule, only write/send actions need a confirm-before-send gate, and this first pass simply doesn't implement any write tool calls yet.

Notion's hosted MCP server authenticates over OAuth. Mnemos doesn't run its own OAuth flow yet, so obtain an access token once via any MCP-aware client or bridge (e.g. `npx mcp-remote https://mcp.notion.com/mcp`) and set it here:

```bash
export MNEMOS_NOTION_TOKEN="<token from the OAuth flow>"
```

```bash
python -m backend.cli notion-search "roadmap notes"
python -m backend.cli notion-fetch "<page id or url>"
```

**Not yet verified end-to-end** — no Notion workspace/token was available while writing this connector. Confirm it against a real workspace before relying on it, and paste back the actual output (same verification standard as every other step in this build).

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

# Semantic search over the vault (no LLM involved — raw chunk matches)
python -m backend.cli search "what did I decide about the app shell?"
python -m backend.cli search "grocery list" --k 3

# Ask a question, answered by the LLM grounded in your notes
python -m backend.cli ask "why did I choose Tauri over Electron?"
python -m backend.cli ask "what's my grocery list?" --k 3 --model llama3.1

# Voice: speech-to-text only
python -m backend.cli transcribe recording.wav

# Voice: text-to-speech only
python -m backend.cli speak "Tauri was chosen for lower resource overhead." --out answer.wav

# Voice: full loop — audio question in, grounded answer, spoken answer out
python -m backend.cli voice-ask question.wav
python -m backend.cli voice-ask question.wav --out my-answer.wav --k 3
python -m backend.cli voice-ask question.wav --no-speak   # text answer only, skip TTS

# Notion connector (Step 6, read-only): search and fetch
python -m backend.cli notion-search "roadmap notes"
python -m backend.cli notion-fetch "<page id or url>"
```
