# Mnemos — Project Handoff (Steps 1–5 Complete)

Local-first, voice-enabled personal knowledge management system built around an Obsidian vault as the single source of truth. Clean build (not a fork; Khoj studied and credited as inspiration). Core requirement: no cloud dependency — all audio, notes, and inference stay on-device.

Repo: `github.com/Nitinsudarshan/Mnemos`
Local root: `D:\Projects\Mnemos`

## Tech stack (as actually implemented)

- Shell: Tauri v2 — chosen over Electron for smaller footprint and native global-hotkey support
- Backend: Python + FastAPI (`backend/server.py`), localhost-only (`127.0.0.1:8765`)
- Storage: Obsidian vault, plain markdown + YAML frontmatter
- Retrieval: LanceDB (embedded mode) + `all-MiniLM-L6-v2` embeddings
- STT: faster-whisper, `base` model (offline)
- TTS: Piper (`en_US-lessac-medium`)
- LLM serving: Ollama, OpenAI-compatible endpoint, model `gemma4:latest`
- Frontend: Vanilla JS (no framework — deliberate, keeps Tauri's low-footprint rationale intact on the frontend too)
- Text injection: `enigo` crate (Rust) — simulated keystrokes into whatever window has OS focus

## Completed: Steps 1–4 (backend, CLI-only)

- **Step 1** — Vault storage + CLI (`init`, `create`, `read`, `list`), correct frontmatter (`source`, `created`, `tags`, `related_notes`)
- **Step 2** — Retrieval: heading-aware chunking, LanceDB indexing, incremental mtime-based reindexing, `search` CLI subcommand
- **Step 3** — Grounded RAG via Ollama (`ask` CLI subcommand); system prompt enforces note-only answers, no hallucinated sources
- **Step 4** — Full voice pipeline (`voice-ask`): Whisper STT → retrieval → LLM → Piper TTS spoken WAV output. Verified end-to-end with real voice and speakers.

Commits: `26f6ffc` → `9b7b2d5` (see repo log for exact SHAs per step).

## Completed: Step 5 (Tauri shell) — all five sub-steps

Commit `4941f02` — "Step 5: Tauri shell - chat UI, global hotkey toggle, voice input, dictation into focused fields"

### 5a — Bare shell + health check

- FastAPI HTTP layer (`backend/server.py`) added on top of the existing CLI-only backend, wrapping `vault`/`retrieval`/`llm`/`voice` modules as endpoints: `/health`, `/notes` (POST/GET), `/note` (GET), `/search`, `/ask`, `/transcribe`, `/speak`, `/voice-ask`
- CORS allow-list (not wildcard) covering `tauri://localhost`, `https://tauri.localhost`, and the actual Tauri dev origin `http://127.0.0.1:1430` (baked into `server.py`'s default — no env var needed)
- Tauri scaffolded via `npm create tauri-app@latest` (Vanilla JS, npm) at `shell/`
- CSP in `tauri.conf.json` explicitly allows `connect-src` to `http://127.0.0.1:8765`
- Verified: webview `fetch()` reaches the backend, displays live status

### 5b — Real chat UI

- Replaced placeholder with full chat interface: text input, submit, answer + sources rendering, wired to `POST /ask`
- Verified end-to-end with real vault content, matching CLI/Swagger output

### 5c — Global hotkey toggle

- `tauri-plugin-global-shortcut` registered in `lib.rs`
- Ctrl+Shift+Space: show/hide the main window from anywhere in the OS
- Verified: works with focus on a completely different app, both directions (show + hide)

### 5d — Voice input in the UI

- 🎙 Record button using browser `MediaRecorder` API (no extra Tauri plugin needed) → POSTs to `/voice-ask` → renders transcript + answer + sources → plays back spoken answer via base64-encoded WAV in an `<audio>` element
- Verified end-to-end with real mic input

### 5e — Universal dictation into focused fields

- **5e-i** (isolated mechanism test): `enigo` crate added, an `inject_text(text: String)` Tauri command that simulates keystrokes into whatever window currently has OS focus. Verified working via a hardcoded test string typed into Notepad from a hidden/background Mnemos window.
- **5e-ii** (full flow): a second global hotkey triggers push-to-talk dictation:
  - Press: shows a small always-on-top, non-focus-stealing indicator window (`shell/src/indicator.html`, red "🎙 Listening..." — currently plain, not yet styled to match Oscar's pill design, see Backlog) and emits a `start-dictation` event to the main window's JS
  - JS reuses the exact `MediaRecorder` pipeline from 5d, branching on a `recordingMode` flag instead of adding a separate native audio-capture crate (kept the native dependency surface minimal)
  - Release: hides indicator, emits `stop-dictation`; JS stops recording, POSTs to `/transcribe` only (not `/voice-ask` — no spoken answer needed for dictation), then calls `invoke("inject_text", {text})`
  - Hotkey history: Alt+Space was tried first and rejected — it's a native Windows-reserved combo (opens the window system menu), and a standalone Alt press has its own OS meaning (menu-bar focus). Real key-press timing isn't perfectly simultaneous, so Windows would sometimes catch a bare Alt moment before Space joined it, stealing focus mid-recording — this produced garbled, cut-off transcripts ("I want her to llll ........."). Switched to Ctrl+Space.
  - **Status: code changed, not yet re-tested by the user. This is the single most important open item — see Next Steps.**

## Environment / dev workflow

Two terminals required (see `mnemos-dev-startup.md`, saved to vault `Reference/`):

**Terminal A — backend:**

```powershell
cd D:\Projects\Mnemos
.venv\Scripts\Activate.ps1
uvicorn backend.server:app --host 127.0.0.1 --port 8765
```

No env vars needed anymore — `127.0.0.1:1430` (CORS) and `gemma4:latest` (LLM default) are both hardcoded defaults now (`server.py`, `llm.py`). `MNEMOS_PIPER_MODEL` is the one remaining required env var (machine-specific absolute path, deliberately has no default):

```powershell
$env:MNEMOS_PIPER_MODEL = "D:\Projects\Mnemos\piper-voices\en_US-lessac-medium.onnx"
```

**Terminal B — Tauri shell:**

```powershell
cd D:\Projects\Mnemos\shell
npm run tauri dev
```

**Optional — Ollama** (only needed for `/ask`, `/voice-ask`):

```powershell
ollama serve
```

### Windows-specific setup gotchas encountered

- App Execution Aliases conflict with Python — disable in Settings
- Audio playback failures can be wrong output device (not a code issue)
- Ollama `0xC0000409` stack buffer overrun crashes can be transient — retry before diagnosing further; confirmed the OpenAI-compat endpoint itself works fine via direct `Invoke-RestMethod` test, so this was environment noise, not a real bug
- Rust install via `winget install Rustlang.Rustup`; also needs MSVC Build Tools (Visual Studio Build Tools, "Desktop development with C++" workload) for the `link.exe` linker — Tauri's first `cargo run` will fail clearly with "linker `link.exe` not found" if this is missing
- Always close and reopen the terminal after installing Rust or the Build Tools — PATH doesn't refresh in already-open terminal sessions
- Tauri dev server port landed on `127.0.0.1:1430` for this project's scaffold, not Vite's usual `1420` default — caused an early CORS failure until corrected
- `$env:VARNAME = "value"` in PowerShell is session-scoped — forgetting to re-set `MNEMOS_PIPER_MODEL` (and previously `MNEMOS_LLM_MODEL`, before it was hardcoded) in a fresh terminal is the single most common recurring error in this build

## Repo structure (current)

```
Mnemos/
├── backend/
│   ├── cli.py          # Steps 1-4 CLI (init/create/read/list/search/ask/voice-ask)
│   ├── vault.py         # Step 1
│   ├── retrieval.py     # Step 2
│   ├── llm.py           # Step 3 (DEFAULT_MODEL now "gemma4:latest")
│   ├── voice.py         # Step 4
│   ├── server.py        # Step 5a — FastAPI HTTP layer over the above
│   └── requirements.txt
├── shell/               # Step 5 — Tauri app
│   ├── src/
│   │   ├── index.html   # Chat UI
│   │   ├── main.js      # fetch/ask/voice-ask/dictation logic
│   │   └── indicator.html  # Dictation-in-progress indicator window
│   └── src-tauri/
│       ├── src/lib.rs   # Both hotkeys + inject_text command
│       ├── Cargo.toml
│       └── tauri.conf.json  # Two windows (main, indicator), CSP
└── vault/               # Obsidian vault (Notes/, Meetings/, Research/,
                          # Reference/, Journal/) — gitignored, local only
```

## Backlog (flagged during the build, not urgent, not yet actioned)

1. Verify Ctrl+Space dictation end-to-end — code changed from Alt+Space, not yet tested. **Do this first.**
2. Re-assess transcription quality after the hotkey fix — the garbled output ("I want her to llll .........") is suspected to be caused by the Alt+Space focus-steal interrupting the recording, not the Whisper `base` model itself. If quality is still poor on a clean, uninterrupted Ctrl+Space recording, try Whisper `small` instead of `base`.
3. Vocabulary/jargon bias — Whisper's `initial_prompt` parameter could fix known mishearings (e.g. "Tauri" → "starting"), inspired by Oscar's custom-vocabulary feature (screenshots reviewed from `navgurukul/oscar_ai_transcription`, `oscar-fe`, `oscar-be` — those repos themselves are cloud/account-based SaaS architecture, not reusable code, but the UI/UX patterns are worth borrowing selectively)
4. Optional local LLM cleanup pass on dictated text before injection (Oscar's "Faithful/Polished/Concise" idea) — fully local via the already-running `gemma4:latest`, no new cloud dependency, fits the local-first philosophy
5. Indicator window polish — currently a plain red box; Oscar's floating pill design (hotkey hint chip + mode dropdown) is a nicer reference direction, purely cosmetic
6. Hold vs. Toggle hotkey mode as a user setting — currently hardcoded to hold-only

## Next major milestone: Step 6 — MCP connectors

Per the original build order in the project brief, add one connector at a time, verify each before the next, lowest-risk first:

1. **Notion** — read-only search/fetch first (official hosted MCP server exists at `mcp.notion.com`, per earlier research — integrate directly rather than build a wrapper)
2. **Google Docs** — same read-only-first pattern
3. **Calendar**
4. **Email**
5. **Messaging (WhatsApp/Slack)** last — explicitly the highest-risk category per the brief ("a bad send is public"); no official WhatsApp MCP server exists, only community-maintained options (e.g. `lharries/whatsapp-mcp`), factor that maturity gap into sequencing

**Non-negotiable safety rule** (unchanged since the original brief): every action that creates, edits, sends, or shares something external must pause for explicit user confirmation before firing. Read-only actions (search, fetch, check) run without confirmation. Applies to every MCP integration without exception. Local vault note creation was already ungated as of Step 1 and that precedent stands — this rule is specifically about external services.

## Working style established across this build (for continuity)

- Incremental, one-task-at-a-time, explicit checkpoints — nothing proceeds without verification at each step
- Paste-back verification of actual file/terminal content, not just "it ran successfully"
- Commits and pushes at each completed step
- When a design choice has real tradeoffs (e.g. reusing JS `MediaRecorder` vs. a separate native audio crate for dictation), state the reasoning and the risk being accepted rather than silently picking one
- Primary terminal: PowerShell on Windows 11

---

# Mnemos — What We're Building

## One-line pitch

A local-first, voice-enabled personal AI that lives inside your own Obsidian vault — it answers questions from your own notes, talks back, types for you into any app on your machine, and (eventually) takes real actions on your behalf, all without your notes or your voice ever leaving your computer.

## Core philosophy

- **Your vault is the only source of truth.** Every other piece — the vector index, the LLM, the voice layer — is a service that reads from and writes back to plain Obsidian markdown files. Nothing holds state anywhere else. If Mnemos disappeared tomorrow, your notes would still be complete, readable, ordinary markdown.
- **Local-first, not local-only-by-default.** No cloud dependency, no account, no telemetry. Audio is transcribed on-device and discarded after transcription. The LLM runs on your own machine via Ollama. This isn't a privacy toggle — there's no cloud path to opt into at all.
- **A companion, not just a search box.** The end goal isn't "search my notes faster" — it's a system that can retrieve what you know, reason about it, speak back to you, capture new thoughts by voice from anywhere on your machine, and eventually act on your behalf (drafting docs, checking calendars, sending messages) with a hard rule that nothing external ever fires without your explicit confirmation first.
- **Clean build, credited inspiration.** Not a fork of anything. Khoj (an existing open-source "second brain" project) was studied closely for retrieval and Obsidian-integration patterns and is credited in code comments where borrowed; the codebase itself is independent.

## Feature list

### Built — Retrieval & knowledge (Steps 1-3)

- Vault-native note storage with correct YAML frontmatter (`source`, `created`, `tags`, `related_notes`) — organized into `Notes/`, `Meetings/`, `Research/`, `Reference/`, `Journal/`
- Heading-aware chunking of notes for retrieval
- Local vector search over the whole vault (LanceDB, embedded — no separate server process)
- Incremental re-indexing (only changed/new notes get re-embedded, based on file modification time)
- Grounded question-answering: answers cite which specific notes they came from, and the system prompt enforces "only answer from what's actually in the notes" — no hallucinated facts, no hallucinated sources
- Semantic retrieval is robust even to noisy input — proven in testing where the exact word "Tauri" was misheard as "starting," but retrieval still surfaced the correct note anyway

### Built — Voice (Step 4)

- Fully offline speech-to-text (Whisper)
- Fully offline text-to-speech (Piper) — the system doesn't just answer in text, it talks back
- One command captures your spoken question, retrieves the right notes, generates a grounded answer, and speaks it back to you — the full loop, no typing required

### Built — Desktop app (Step 5)

- A real, native-feeling desktop window (not a browser tab) built on Tauri, chosen specifically for its small footprint and OS-level hotkey support
- A chat interface for typed questions, with sources always shown alongside answers
- A global show/hide hotkey — bring Mnemos to front from literally any other application, or tuck it away again, without touching the mouse
- Voice input inside the app — a record button that captures your question by mic, transcribes it, retrieves and answers, and plays the spoken reply back — all inside the same window as typed chat
- Universal dictation — the headline feature of Step 5: hold a hotkey while your cursor is in any other application on your machine (Notepad, a browser, Slack, an email client, anything), speak, release, and the transcribed text is typed directly into whatever field you were in. A small on-screen indicator shows when it's listening. Mnemos never has to be the focused window for this to work.

### In progress / near-term polish (flagged, not yet built)

- Custom vocabulary/jargon list to fix known transcription mishearings, using Whisper's prompt-biasing rather than relying on retrieval alone to compensate
- An optional local cleanup pass on dictated text (filler-word removal, grammar tidy) before it's typed — run entirely through the same local LLM already powering question-answering, no new cloud dependency
- Visual polish on the "listening" indicator
- User-configurable hold-vs-toggle behavior for the dictation hotkey

### Planned — Actions via MCP (Step 6)

The next major phase: giving Mnemos the ability to do things, not just know things — added one integration at a time, safest first:

1. Notion — read/search your Notion workspace from inside Mnemos
2. Google Docs — same, read-first
3. Calendar — check and eventually schedule
4. Email — read, draft, and (with confirmation) send
5. Messaging (WhatsApp/Slack) — deliberately last, since a wrongly-sent message is the highest-stakes mistake this system could make

The rule that governs all of it: anything that creates, edits, sends, or shares something external pauses for your explicit yes before it fires. Every time, no exceptions. Read-only lookups (search, check, fetch) don't need to ask.

### Longer-term roadmap (from original project vision, not yet started)

- Content transformation skills: resume rewriting, meeting summarization, tone-shifting — Mnemos as an editor, not just a retriever
- Automation & code generation capabilities
- Long-horizon companion behaviors — the system remembering context and intent across sessions in a genuinely assistant-like way, not just answering isolated queries

## Who this is for (implicit from the design choices)

Someone who:

- Already lives in Obsidian and doesn't want a second app that fragments their notes
- Wants an AI assistant but is unwilling to send personal notes, voice recordings, or day-to-day dictation to a cloud API
- Wants voice as a first-class input method, not a bolted-on feature — including being able to dictate into any application, not just a chatbot window
- Is comfortable running local infra (Ollama, a Python backend, a small native app) in exchange for genuinely owning their data end to end
