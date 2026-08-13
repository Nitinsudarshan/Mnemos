"""RAG answers grounded in vault retrieval, served via Ollama.

Step 3 of the Mnemos build. Still no voice, no MCP actions — this only
proves that answers are grounded in retrieved notes, not hallucinated.

Design decisions (justified per project convention — not defaults):

- HTTP client: plain `requests` against Ollama's OpenAI-compatible
  `/v1/chat/completions` endpoint, rather than pulling in the full `openai`
  SDK for a single call. Keeps the dependency footprint small, in keeping
  with the local-first design.
- Config via env vars (MNEMOS_LLM_BASE_URL, MNEMOS_LLM_MODEL), mirroring
  MNEMOS_VAULT_PATH / MNEMOS_INDEX_PATH. Because the endpoint is OpenAI-
  compatible, swapping Ollama for a hosted API later is a config change,
  not a rewrite — this is the exact reason Ollama was chosen over a
  bespoke local-inference wrapper.
- Grounding is enforced by prompt, not just retrieval: the system prompt
  explicitly instructs the model to answer only from the supplied note
  excerpts and to say so plainly when the answer isn't in them. Retrieval
  alone doesn't guarantee grounding — a model can still ignore the context
  and answer from its own training data unless told not to.
- Read-only: `ask()` never writes or sends anything, so the project's
  confirm-before-send rule (build order step 4+) doesn't apply here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import requests

from backend import retrieval

DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_MODEL = "llama3.1"  # assumes `ollama pull llama3.1` was run — override
                             # via MNEMOS_LLM_MODEL if you pulled something else
REQUEST_TIMEOUT_SECONDS = 120

SYSTEM_PROMPT = (
    "You are a personal assistant answering questions using ONLY the note "
    "excerpts provided below. Do not use outside knowledge. If the answer "
    "is not contained in the excerpts, say plainly that your notes don't "
    "cover this, rather than guessing or filling in from general knowledge. "
    "When you do answer, mention which note(s) the information came from."
)


class LLMConnectionError(RuntimeError):
    """Raised when Ollama (or whatever's at MNEMOS_LLM_BASE_URL) can't be reached."""


def get_llm_config() -> tuple:
    base_url = os.environ.get("MNEMOS_LLM_BASE_URL", DEFAULT_BASE_URL)
    model = os.environ.get("MNEMOS_LLM_MODEL", DEFAULT_MODEL)
    return base_url, model


@dataclass
class AskResult:
    answer: str
    sources: list  # list[retrieval.SearchResult]


def _build_context(chunks: list) -> str:
    parts = []
    for i, r in enumerate(chunks, 1):
        parts.append(f"[Note {i}: {r.note_title} ({r.folder})]\n{r.text.strip()}")
    return "\n\n".join(parts)


def ask(
    query: str,
    k: int = 5,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    embedder=None,
) -> AskResult:
    """Retrieve top-k chunks, then ask the LLM to answer grounded in them."""
    sources = retrieval.search(query, k=k, embedder=embedder)

    if not sources:
        return AskResult(
            answer=(
                "I don't have any indexed notes to search yet — run "
                "`python -m backend.cli reindex` first."
            ),
            sources=[],
        )

    context = _build_context(sources)
    user_message = (
        f"Note excerpts:\n\n{context}\n\n---\n\nQuestion: {query}"
    )

    resolved_base_url, resolved_model = get_llm_config()
    base_url = base_url or resolved_base_url
    model = model or resolved_model

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "stream": False,
    }

    try:
        response = requests.post(
            f"{base_url}/chat/completions",
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.exceptions.ConnectionError as e:
        raise LLMConnectionError(
            f"Could not reach the LLM server at {base_url}. Is Ollama running? "
            f"(`ollama serve`, and confirm `ollama list` shows '{model}' pulled). "
            f"Underlying error: {e}"
        ) from e
    except requests.exceptions.Timeout as e:
        raise LLMConnectionError(
            f"Request to {base_url} timed out after {REQUEST_TIMEOUT_SECONDS}s. "
            f"A large model's first load into memory can be slow — try again, "
            f"or increase REQUEST_TIMEOUT_SECONDS if this persists."
        ) from e
    except requests.exceptions.RequestException as e:
        raise LLMConnectionError(f"Request to {base_url} failed: {e}") from e

    if response.status_code != 200:
        raise LLMConnectionError(
            f"LLM server at {base_url} returned HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )

    data = response.json()
    try:
        answer = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise LLMConnectionError(
            f"Unexpected response shape from {base_url}: {data}"
        ) from e

    return AskResult(answer=answer, sources=sources)
