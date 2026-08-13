"""Read/write access to the Obsidian vault — the single source of truth.

Step 1 of the Mnemos build: no voice, no LLM, no vector index. Just notes
on disk, with frontmatter, that later layers will read from and write back to.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

VAULT_FOLDERS = ["Notes", "Meetings", "Research", "Reference", "Journal"]

FRONTMATTER_DELIM = "---"


def get_vault_root() -> Path:
    """Vault location, overridable via MNEMOS_VAULT_PATH so the backend
    never hardcodes where a user's real vault lives."""
    root = os.environ.get("MNEMOS_VAULT_PATH", "./vault")
    return Path(root).expanduser().resolve()


def init_vault(root: Optional[Path] = None) -> Path:
    root = root or get_vault_root()
    for folder in VAULT_FOLDERS:
        (root / folder).mkdir(parents=True, exist_ok=True)
    return root


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.strip().lower()).strip("-")
    return slug or "untitled"


@dataclass
class Note:
    path: Path
    title: str
    content: str
    source: str = "manual"
    created: str = ""
    tags: list = field(default_factory=list)
    related_notes: list = field(default_factory=list)

    @property
    def frontmatter(self) -> dict:
        return {
            "source": self.source,
            "created": self.created,
            "tags": self.tags,
            "related_notes": self.related_notes,
        }

    def to_markdown(self) -> str:
        fm = yaml.safe_dump(self.frontmatter, sort_keys=False, default_flow_style=None).strip()
        return f"{FRONTMATTER_DELIM}\n{fm}\n{FRONTMATTER_DELIM}\n\n# {self.title}\n\n{self.content}\n"


def create_note(
    folder: str,
    title: str,
    content: str = "",
    tags: Optional[list] = None,
    source: str = "manual",
    related_notes: Optional[list] = None,
    root: Optional[Path] = None,
) -> Path:
    if folder not in VAULT_FOLDERS:
        raise ValueError(f"Unknown vault folder '{folder}'. Expected one of {VAULT_FOLDERS}")

    root = init_vault(root)
    created = datetime.now().astimezone().isoformat(timespec="seconds")
    filename = f"{created[:10]}-{slugify(title)}.md"
    path = root / folder / filename

    note = Note(
        path=path,
        title=title,
        content=content,
        source=source,
        created=created,
        tags=tags or [],
        related_notes=related_notes or [],
    )
    path.write_text(note.to_markdown(), encoding="utf-8")
    return path


def read_note(path: os.PathLike) -> Note:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if not text.startswith(FRONTMATTER_DELIM):
        raise ValueError(f"Note {path} has no frontmatter block")

    _, fm_raw, body = text.split(FRONTMATTER_DELIM, 2)
    frontmatter = yaml.safe_load(fm_raw) or {}
    body = body.strip("\n")

    title = path.stem
    heading_match = re.match(r"#\s+(.+)", body)
    if heading_match:
        title = heading_match.group(1).strip()
        body = body[heading_match.end():].strip("\n")

    return Note(
        path=path,
        title=title,
        content=body,
        source=frontmatter.get("source", ""),
        created=frontmatter.get("created", ""),
        tags=frontmatter.get("tags") or [],
        related_notes=frontmatter.get("related_notes") or [],
    )


def list_notes(folder: Optional[str] = None, root: Optional[Path] = None) -> list:
    root = root or get_vault_root()
    folders = [folder] if folder else VAULT_FOLDERS
    paths = []
    for f in folders:
        folder_path = root / f
        if folder_path.exists():
            paths.extend(sorted(folder_path.glob("*.md")))
    return paths
