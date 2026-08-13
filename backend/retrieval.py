"""Vault retrieval: chunk notes, embed them, and query via LanceDB.

Step 2 of the Mnemos build. Still no LLM, no voice — this only proves that
a query against the vault returns relevant chunks.

Design decisions (justified per project convention — not defaults):

- Embedding model: `all-MiniLM-L6-v2` (sentence-transformers). Chosen because
  it's small (~80MB), CPU-fast, and produces 384-dim vectors that are more
  than sufficient for personal-note semantic search at vault scale. A larger
  model would cost latency and RAM on a local-first app for marginal recall
  gains on this use case.
- Index storage: LanceDB lives OUTSIDE the vault (default `./.mnemos/lancedb`,
  overridable via MNEMOS_INDEX_PATH), never inside `./vault`. The vault must
  stay pure markdown + frontmatter per the core architecture principle —
  the index is a derived, rebuildable cache, not part of the source of truth.
- Chunking: heading-aware. Obsidian notes already carry natural structure
  (## / ### headings), so splitting on those boundaries keeps chunks
  semantically coherent instead of cutting mid-thought at a fixed character
  count. Notes with no headings fall back to paragraph splitting.
- Incremental re-indexing: each note's mtime is stored alongside its chunks.
  Since the vault is the source of truth and can be edited directly in
  Obsidian (outside this app), `reindex` compares current mtime to the
  stored one and only re-embeds notes that actually changed, rather than
  re-embedding the whole vault every time.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from backend import vault

EMBED_MODEL_NAME = "all-MiniLM-L6-v2"
EMBED_DIM = 384
TABLE_NAME = "chunks"

# Target chunk size in characters. Heading sections longer than this are
# further split on paragraph breaks; shorter adjacent sections are merged.
CHUNK_TARGET_CHARS = 800
CHUNK_MAX_CHARS = 1500

_embedder = None  # lazy-loaded singleton


def get_index_path() -> Path:
    """Location of the LanceDB store. Deliberately outside the vault —
    overridable via MNEMOS_INDEX_PATH, mirroring MNEMOS_VAULT_PATH."""
    root = os.environ.get("MNEMOS_INDEX_PATH", "./.mnemos/lancedb")
    return Path(root).expanduser().resolve()


def get_embedder():
    """Lazily load the sentence-transformer model. Downloads on first run
    (needs network access to huggingface.co on the machine actually running
    this — not required at import time, only at first embed call)."""
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer

        _embedder = SentenceTransformer(EMBED_MODEL_NAME)
    return _embedder


def embed_texts(texts: list, embedder=None) -> list:
    embedder = embedder or get_embedder()
    vectors = embedder.encode(list(texts), normalize_embeddings=True)
    return [v.tolist() for v in vectors]


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+.+$", re.MULTILINE)


def _split_by_headings(body: str) -> list:
    """Split note body into sections at heading boundaries. Each section
    keeps its heading line attached so the chunk carries its own context."""
    matches = list(_HEADING_RE.finditer(body))
    if not matches:
        return [body] if body.strip() else []

    sections = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        section = body[start:end].strip()
        if section:
            sections.append(section)

    # Anything before the first heading (e.g. an intro paragraph)
    pre = body[: matches[0].start()].strip()
    if pre:
        sections.insert(0, pre)
    return sections


_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def _split_by_sentences(text: str) -> list:
    """Fallback for a single paragraph with no blank-line breaks: split on
    sentence boundaries and regroup up to the target chunk size."""
    sentences = [s.strip() for s in _SENTENCE_RE.split(text) if s.strip()]
    if len(sentences) <= 1:
        # No sentence boundaries either (e.g. one giant run-on line or a
        # code block) — hard-wrap by character count as a last resort.
        return [
            text[i : i + CHUNK_TARGET_CHARS].strip()
            for i in range(0, len(text), CHUNK_TARGET_CHARS)
        ]
    chunks, current = [], ""
    for s in sentences:
        if current and len(current) + len(s) + 1 > CHUNK_TARGET_CHARS:
            chunks.append(current.strip())
            current = s
        else:
            current = f"{current} {s}" if current else s
    if current:
        chunks.append(current.strip())
    return chunks


def _split_large_section(section: str) -> list:
    """Paragraph-split a section that's too long to be one chunk. Any
    resulting piece still over the max falls back to sentence splitting."""
    if len(section) <= CHUNK_MAX_CHARS:
        return [section]
    paragraphs = [p.strip() for p in section.split("\n\n") if p.strip()]
    chunks, current = [], ""
    for p in paragraphs:
        if len(p) > CHUNK_MAX_CHARS:
            if current:
                chunks.append(current.strip())
                current = ""
            chunks.extend(_split_by_sentences(p))
            continue
        if current and len(current) + len(p) + 2 > CHUNK_TARGET_CHARS:
            chunks.append(current.strip())
            current = p
        else:
            current = f"{current}\n\n{p}" if current else p
    if current:
        chunks.append(current.strip())
    return chunks or [section]


def _merge_small_sections(sections: list) -> list:
    """Merge consecutive short sections up to the target chunk size so a
    one-line heading with two sentences under it doesn't become its own
    near-empty chunk."""
    merged, current = [], ""
    for s in sections:
        if current and len(current) + len(s) + 2 <= CHUNK_TARGET_CHARS:
            current = f"{current}\n\n{s}"
        else:
            if current:
                merged.append(current)
            current = s
    if current:
        merged.append(current)
    return merged


def chunk_note_body(body: str) -> list:
    sections = _split_by_headings(body)
    expanded = []
    for s in sections:
        expanded.extend(_split_large_section(s))
    return _merge_small_sections(expanded)


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------

@dataclass
class IndexStats:
    notes_scanned: int = 0
    notes_reindexed: int = 0
    notes_skipped_unchanged: int = 0
    chunks_written: int = 0


def _open_table(db, create_if_missing: bool = True):
    import pyarrow as pa

    if TABLE_NAME in db.table_names():
        return db.open_table(TABLE_NAME)
    if not create_if_missing:
        return None
    schema = pa.schema(
        [
            pa.field("vector", pa.list_(pa.float32(), EMBED_DIM)),
            pa.field("text", pa.string()),
            pa.field("note_path", pa.string()),
            pa.field("note_title", pa.string()),
            pa.field("folder", pa.string()),
            pa.field("tags", pa.string()),  # comma-joined
            pa.field("created", pa.string()),
            pa.field("chunk_index", pa.int32()),
            pa.field("note_mtime", pa.float64()),
        ]
    )
    return db.create_table(TABLE_NAME, schema=schema)


def reindex(root: Optional[Path] = None, embedder=None) -> IndexStats:
    """Incrementally re-embed notes whose mtime changed since last index.
    New notes are added; deleted notes are pruned; unchanged notes are
    skipped entirely (no re-embedding cost)."""
    import lancedb

    vault_root = root or vault.get_vault_root()
    db = lancedb.connect(str(get_index_path()))
    table = _open_table(db)

    note_paths = vault.list_notes(root=vault_root)
    stats = IndexStats()

    existing_mtimes = {}
    if table.count_rows() > 0:
        # Read via Arrow directly (no pandas dependency needed). Multiple
        # chunk rows share the same note_mtime for a given note_path, so a
        # plain dict overwrite during iteration is an effective dedup.
        arrow_tbl = table.to_arrow().select(["note_path", "note_mtime"])
        for note_path, note_mtime in zip(
            arrow_tbl.column("note_path").to_pylist(),
            arrow_tbl.column("note_mtime").to_pylist(),
        ):
            existing_mtimes[note_path] = note_mtime

    seen_paths = set()
    for path in note_paths:
        stats.notes_scanned += 1
        str_path = str(path)
        seen_paths.add(str_path)
        mtime = path.stat().st_mtime

        if existing_mtimes.get(str_path) == mtime:
            stats.notes_skipped_unchanged += 1
            continue

        note = vault.read_note(path)
        chunks = chunk_note_body(note.content)
        if not chunks:
            continue

        # Drop any prior chunks for this note before writing fresh ones.
        table.delete(f"note_path = '{str_path}'")

        vectors = embed_texts(chunks, embedder=embedder)
        rows_to_add = [
            {
                "vector": vec,
                "text": chunk,
                "note_path": str_path,
                "note_title": note.title,
                "folder": path.parent.name,
                "tags": ",".join(note.tags),
                "created": note.created,
                "chunk_index": i,
                "note_mtime": mtime,
            }
            for i, (chunk, vec) in enumerate(zip(chunks, vectors))
        ]
        table.add(rows_to_add)
        stats.notes_reindexed += 1
        stats.chunks_written += len(rows_to_add)

    # Prune notes that were deleted from the vault since the last index.
    stale_paths = set(existing_mtimes) - seen_paths
    for stale in stale_paths:
        table.delete(f"note_path = '{stale}'")

    return stats


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@dataclass
class SearchResult:
    text: str
    note_path: str
    note_title: str
    folder: str
    score: float


def search(query: str, k: int = 5, embedder=None) -> list:
    import lancedb

    db = lancedb.connect(str(get_index_path()))
    if TABLE_NAME not in db.table_names():
        return []
    table = db.open_table(TABLE_NAME)
    if table.count_rows() == 0:
        return []

    query_vector = embed_texts([query], embedder=embedder)[0]
    results = table.search(query_vector).limit(k).to_list()
    return [
        SearchResult(
            text=r["text"],
            note_path=r["note_path"],
            note_title=r["note_title"],
            folder=r["folder"],
            score=r.get("_distance", 0.0),
        )
        for r in results
    ]
