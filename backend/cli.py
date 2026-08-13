"""CLI entrypoint for the Mnemos backend.

Step 1 only: create and read notes in the vault. No voice, no LLM, no
retrieval. Run with `python -m backend.cli <command> ...` from the repo root.
"""
import argparse
from pathlib import Path

from backend import vault


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

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
