"""CLI entrypoint for the Mnemos backend.

Step 1 only: create and read notes in the vault. No voice, no LLM, no
retrieval. Run with `python -m backend.cli <command> ...` from the repo root.
"""
import argparse
from pathlib import Path

from backend import retrieval, vault


def cmd_init(args):
    root = vault.init_vault()
    print(f"Vault initialized at {root}")


def cmd_create(args):
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    related = [r.strip() for r in args.related.split(",") if r.strip()]

    content = args.content
    if args.content_file:
        content = Path(args.content_file).read_text(encoding="utf-8")

    path = vault.create_note(
        folder=args.folder,
        title=args.title,
        content=content,
        tags=tags,
        source=args.source,
        related_notes=related,
    )
    print(f"Created {path}")


def cmd_read(args):
    note = vault.read_note(args.path)
    print(f"Title: {note.title}")
    print(f"Source: {note.source}")
    print(f"Created: {note.created}")
    print(f"Tags: {note.tags}")
    print(f"Related notes: {note.related_notes}")
    print("---")
    print(note.content)


def cmd_list(args):
    for path in vault.list_notes(folder=args.folder):
        print(path)


def cmd_reindex(args):
    stats = retrieval.reindex()
    print(f"Scanned: {stats.notes_scanned}")
    print(f"Reindexed (new/changed): {stats.notes_reindexed}")
    print(f"Skipped (unchanged): {stats.notes_skipped_unchanged}")
    print(f"Chunks written: {stats.chunks_written}")


def cmd_search(args):
    results = retrieval.search(args.query, k=args.k)
    if not results:
        print("No results. Have you run `python -m backend.cli reindex`?")
        return
    for i, r in enumerate(results, 1):
        print(f"[{i}] {r.note_title}  ({r.note_path})  distance={r.score:.4f}")
        snippet = r.text.strip().replace("\n", " ")
        print(f"    {snippet[:200]}{'...' if len(snippet) > 200 else ''}")
        print()


def build_parser():
    parser = argparse.ArgumentParser(prog="mnemos", description="Mnemos vault CLI (step 1: storage only)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Create the vault folder structure")
    p_init.set_defaults(func=cmd_init)

    p_create = sub.add_parser("create", help="Create a new note")
    p_create.add_argument("--folder", required=True, choices=vault.VAULT_FOLDERS)
    p_create.add_argument("--title", required=True)
    p_create.add_argument("--content", default="")
    p_create.add_argument("--content-file", help="Read note body from this file instead of --content")
    p_create.add_argument("--tags", default="", help="Comma-separated tags")
    p_create.add_argument("--related", default="", help="Comma-separated related note titles/paths")
    p_create.add_argument("--source", default="manual")
    p_create.set_defaults(func=cmd_create)

    p_read = sub.add_parser("read", help="Read a note by path")
    p_read.add_argument("path")
    p_read.set_defaults(func=cmd_read)

    p_list = sub.add_parser("list", help="List notes in the vault")
    p_list.add_argument("--folder", choices=vault.VAULT_FOLDERS)
    p_list.set_defaults(func=cmd_list)

    p_reindex = sub.add_parser("reindex", help="Embed new/changed vault notes into LanceDB")
    p_reindex.set_defaults(func=cmd_reindex)

    p_search = sub.add_parser("search", help="Semantic search over the vault")
    p_search.add_argument("query")
    p_search.add_argument("--k", type=int, default=5, help="Number of results (default 5)")
    p_search.set_defaults(func=cmd_search)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
