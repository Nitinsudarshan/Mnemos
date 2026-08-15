"""HTTP layer over the existing backend modules — the seam the Tauri shell
talks to.

Step 5 (sub-step 1) of the Mnemos build: steps 1-4 only ever ran as a CLI,
one process, one invocation, exit. A Tauri webview can't shell out to
Python per keystroke — it needs a long-lived process it can call over HTTP.
This file adds nothing new in terms of *logic*; it's a thin wrapper around
vault/retrieval/llm/voice, verified from the terminal (or FastAPI's auto
docs at /docs) exactly like every prior step, before any Rust/Tauri code
is written.

Design decisions (justified per project convention — not defaults):

- Framework: FastAPI. Already the locked-in choice in the project brief,
  not a new decision here — this file is the first place it's actually
  used, since steps 1-4 only needed argparse.
- ASGI server: uvicorn. The standard, most widely-used companion to
  FastAPI — no separate justification needed beyond "this is what
  virtually every FastAPI app runs on."
- Bind to 127.0.0.1 only, never 0.0.0.0. The brief specifies
  "localhost-only, no exposed ports" — this is a personal assistant with
  filesystem and (later) MCP write access; it must be unreachable from
  the network, not just unreachable by default.
- CORS: an explicit allow-list of Tauri's known dev/prod origins
  (MNEMOS_CORS_ORIGINS to override), never "*". A wildcard would let any
  webpage the user later opens in a browser silently call this API too.
- Audio in/out as base64-in-JSON, not raw multipart responses. Every
  endpoint here returns exactly one JSON body, including the two that
  carry audio (/speak, /voice-ask). That means the Tauri frontend only
  ever needs `fetch(...).then(r => r.json())` — one response-handling
  code path for every endpoint — instead of branching between JSON parsing
  and binary blob handling depending on which route was called. The
  ~33% base64 size overhead is negligible for short spoken answers and
  is the right trade for that simplicity.
- Errors as JSON `{"error": "..."}` with an appropriate HTTP status,
  reusing the exact exception messages the CLI already prints — so the
  frontend can show the same clear, actionable text a terminal user
  already gets (e.g. "Is Ollama running?").
- No confirm-before-send gating in this file. That rule (build order
  step 4 of the *feature* roadmap, not to be confused with this build
  order's step 5) applies to MCP actions that write/send to external
  services — none exist yet. Local vault note creation was already
  ungated as of step 1; this file doesn't change that precedent.
"""
from __future__ import annotations

import base64
import os
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend import google_docs, llm, notion, retrieval, vault, voice

app = FastAPI(title="Mnemos", description="Local second-brain backend")

# Tauri v2 serves the production webview from tauri://localhost (or
# https://tauri.localhost on Windows). The dev server's actual port varies by
# project scaffold (this one landed on 1430, not Vite's usual 1420 default) —
# check your own devUrl/devPort in tauri.conf.json if the fetch is CORS-
# blocked. Override with MNEMOS_CORS_ORIGINS="origin1,origin2" for anything
# not covered here.
_default_origins = "tauri://localhost,https://tauri.localhost,http://127.0.0.1:1430,http://localhost:1430"
_origins = [o.strip() for o in os.environ.get("MNEMOS_CORS_ORIGINS", _default_origins).split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------

class CreateNoteRequest(BaseModel):
    folder: str
    title: str
    content: str = ""
    tags: list[str] = []
    source: str = "manual"
    related_notes: list[str] = []


class SearchRequest(BaseModel):
    query: str
    k: int = 5


class AskRequest(BaseModel):
    query: str
    k: int = 5
    model: Optional[str] = None


class SpeakRequest(BaseModel):
    text: str


def _source_dicts(sources: list) -> list:
    return [{"note_title": r.note_title, "note_path": r.note_path, "folder": r.folder} for r in sources]


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Vault (steps 1)
# ---------------------------------------------------------------------------

@app.post("/notes")
def create_note(req: CreateNoteRequest):
    if req.folder not in vault.VAULT_FOLDERS:
        raise HTTPException(400, f"Unknown vault folder '{req.folder}'. Expected one of {vault.VAULT_FOLDERS}")
    path = vault.create_note(
        folder=req.folder,
        title=req.title,
        content=req.content,
        tags=req.tags,
        source=req.source,
        related_notes=req.related_notes,
    )
    return {"path": str(path)}


@app.get("/notes")
def list_notes_endpoint(folder: Optional[str] = None):
    if folder is not None and folder not in vault.VAULT_FOLDERS:
        raise HTTPException(400, f"Unknown vault folder '{folder}'. Expected one of {vault.VAULT_FOLDERS}")
    return {"paths": [str(p) for p in vault.list_notes(folder=folder)]}


@app.get("/note")
def read_note_endpoint(path: str):
    try:
        note = vault.read_note(path)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(404, str(e))
    return {
        "title": note.title,
        "content": note.content,
        "source": note.source,
        "created": note.created,
        "tags": note.tags,
        "related_notes": note.related_notes,
    }


# ---------------------------------------------------------------------------
# Retrieval + RAG (steps 2-3)
# ---------------------------------------------------------------------------

@app.post("/search")
def search_endpoint(req: SearchRequest):
    results = retrieval.search(req.query, k=req.k)
    return {
        "results": [
            {"note_title": r.note_title, "note_path": r.note_path, "folder": r.folder, "text": r.text, "score": r.score}
            for r in results
        ]
    }


@app.post("/ask")
def ask_endpoint(req: AskRequest):
    try:
        result = llm.ask(req.query, k=req.k, model=req.model)
    except llm.LLMConnectionError as e:
        raise HTTPException(502, str(e))
    return {"answer": result.answer, "sources": _source_dicts(result.sources)}


# ---------------------------------------------------------------------------
# Voice (step 4)
# ---------------------------------------------------------------------------

async def _save_upload_to_temp(upload: UploadFile) -> Path:
    suffix = Path(upload.filename or "audio").suffix or ".wav"
    data = await upload.read()
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return Path(tmp_path)


@app.post("/transcribe")
async def transcribe_endpoint(audio: UploadFile = File(...)):
    tmp_path = await _save_upload_to_temp(audio)
    try:
        text = voice.transcribe_audio(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    return {"text": text}


@app.post("/speak")
def speak_endpoint(req: SpeakRequest):
    fd, tmp_path_str = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    tmp_path = Path(tmp_path_str)
    try:
        voice.synthesize_to_wav(req.text, tmp_path)
        audio_b64 = base64.b64encode(tmp_path.read_bytes()).decode("ascii")
    except voice.VoiceConfigError as e:
        raise HTTPException(500, str(e))
    finally:
        tmp_path.unlink(missing_ok=True)
    return {"audio_base64": audio_b64, "mime_type": "audio/wav"}


@app.post("/voice-ask")
async def voice_ask_endpoint(
    audio: UploadFile = File(...),
    k: int = Form(5),
    model: Optional[str] = Form(None),
    speak: bool = Form(True),
):
    tmp_in = await _save_upload_to_temp(audio)
    tmp_out = tmp_in.with_suffix(".answer.wav")
    try:
        result = voice.voice_ask(tmp_in, output_wav_path=tmp_out, k=k, model=model, speak=speak)
    except (voice.VoiceConfigError, llm.LLMConnectionError) as e:
        raise HTTPException(502, str(e))
    finally:
        tmp_in.unlink(missing_ok=True)

    audio_b64 = None
    if result.answer_audio_path is not None:
        p = Path(result.answer_audio_path)
        audio_b64 = base64.b64encode(p.read_bytes()).decode("ascii")
        p.unlink(missing_ok=True)

    return {
        "transcript": result.transcript,
        "answer_text": result.answer_text,
        "sources": _source_dicts(result.sources),
        "audio_base64": audio_b64,
        "mime_type": "audio/wav" if audio_b64 else None,
    }


# ---------------------------------------------------------------------------
# MCP connectors (step 6) — read-only for now, so no confirm-before-send
# gate is needed here yet. That gate belongs on write/send actions, none of
# which exist across any connector at this point in the build.
# ---------------------------------------------------------------------------

@app.get("/connectors/notion/search")
def notion_search_endpoint(q: str):
    try:
        result = notion.search(q)
    except notion.NotionConfigError as e:
        raise HTTPException(500, str(e))
    except notion.NotionConnectionError as e:
        raise HTTPException(502, str(e))
    return {"result": result}


@app.get("/connectors/notion/fetch")
def notion_fetch_endpoint(id: str):
    try:
        result = notion.fetch(id)
    except notion.NotionConfigError as e:
        raise HTTPException(500, str(e))
    except notion.NotionConnectionError as e:
        raise HTTPException(502, str(e))
    return {"result": result}


@app.get("/connectors/google-docs/search")
def google_docs_search_endpoint(q: str):
    try:
        result = google_docs.search(q)
    except google_docs.GoogleDocsConfigError as e:
        raise HTTPException(500, str(e))
    except google_docs.GoogleDocsConnectionError as e:
        raise HTTPException(502, str(e))
    return {"result": result}


@app.get("/connectors/google-docs/fetch")
def google_docs_fetch_endpoint(id: str):
    try:
        result = google_docs.fetch(id)
    except google_docs.GoogleDocsConfigError as e:
        raise HTTPException(500, str(e))
    except google_docs.GoogleDocsConnectionError as e:
        raise HTTPException(502, str(e))
    return {"result": result}
