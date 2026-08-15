"""Step 6 (connector 2 of 5): read-only Google Docs search/fetch.

Per the project brief's Step 6 ordering, MCP connectors are added one at a
time, lowest-risk first, each verified working before the next is started.
Google Docs follows Notion (backend/notion.py) in the read-only-first
pattern.

Design decisions (justified per project convention — not defaults):

- API: Google's own Drive v3 (search) and Docs v1 (fetch) REST APIs via
  the official `google-api-python-client` + `google-auth-oauthlib`, rather
  than an MCP server. Unlike Notion, there's no single official hosted MCP
  server for Google Docs to integrate against directly, so this connector
  talks to Google's REST APIs the way any other desktop app would. The
  interface still matches backend/notion.py — `search()`/`fetch()` each
  return plain text — so the CLI and server wiring stay identical across
  connectors regardless of what's underneath.
- Auth: OAuth 2.0 "installed app" flow (`InstalledAppFlow.run_local_server`),
  the standard consumer pattern for a desktop app with no backend server of
  its own. MNEMOS_GOOGLE_CREDENTIALS points at an OAuth client secret JSON
  the user downloads once from Google Cloud Console for a "Desktop app"
  client (machine-specific, required, no default — same pattern as
  MNEMOS_PIPER_MODEL). The resulting token is cached at
  MNEMOS_GOOGLE_TOKEN (default `.mnemos/google_token.json`, alongside the
  LanceDB cache — a derived, regenerable local artifact, not vault
  content) so the one-time browser consent only happens once, not on
  every call.
- Scopes: drive.readonly + documents.readonly only. Nothing here can
  create, edit, or share a file — matching Step 6's read-only-first rule
  and the project's confirm-before-send safety rule for anything that
  isn't read-only.
- Status: like backend/notion.py, this hasn't been exercised against a
  real Google account yet (no OAuth client/credentials available in this
  environment) — verify manually before relying on it.
"""
from __future__ import annotations

import os
import re

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/documents.readonly",
]
DEFAULT_TOKEN_PATH = ".mnemos/google_token.json"

_DOC_ID_IN_URL = re.compile(r"/d/([a-zA-Z0-9_-]+)")


class GoogleDocsConfigError(RuntimeError):
    """Raised when MNEMOS_GOOGLE_CREDENTIALS isn't set or a token can't be obtained."""


class GoogleDocsConnectionError(RuntimeError):
    """Raised when the Google APIs can't be reached or return an error."""


def _get_credentials_path() -> str:
    path = os.environ.get("MNEMOS_GOOGLE_CREDENTIALS")
    if not path:
        raise GoogleDocsConfigError(
            "MNEMOS_GOOGLE_CREDENTIALS is not set. Download an OAuth client "
            "secret JSON for a Desktop app from Google Cloud Console (see "
            "README's Google Docs connector section) and point this at it."
        )
    return path


def _get_token_path() -> str:
    return os.environ.get("MNEMOS_GOOGLE_TOKEN", DEFAULT_TOKEN_PATH)


def _get_credentials() -> Credentials:
    token_path = _get_token_path()
    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(_get_credentials_path(), SCOPES)
            creds = flow.run_local_server(port=0)
        token_dir = os.path.dirname(token_path)
        if token_dir:
            os.makedirs(token_dir, exist_ok=True)
        with open(token_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    return creds


def _resolve_document_id(id_or_url: str) -> str:
    match = _DOC_ID_IN_URL.search(id_or_url)
    return match.group(1) if match else id_or_url


def _extract_text(doc: dict) -> str:
    parts = []
    for element in doc.get("body", {}).get("content", []):
        paragraph = element.get("paragraph")
        if not paragraph:
            continue
        for run in paragraph.get("elements", []):
            text_run = run.get("textRun")
            if text_run:
                parts.append(text_run.get("content", ""))
    return "".join(parts).strip()


def search(query: str) -> str:
    """Search Google Drive for Google Docs matching the query. Read-only."""
    try:
        creds = _get_credentials()
        drive = build("drive", "v3", credentials=creds)
        escaped = query.replace("\\", "\\\\").replace("'", "\\'")
        result = (
            drive.files()
            .list(
                q=(
                    "mimeType='application/vnd.google-apps.document' "
                    f"and fullText contains '{escaped}' and trashed=false"
                ),
                fields="files(id, name, webViewLink)",
                pageSize=10,
            )
            .execute()
        )
    except GoogleDocsConfigError:
        raise
    except HttpError as e:
        raise GoogleDocsConnectionError(f"Google Drive search failed: {e}") from e
    except Exception as e:
        raise GoogleDocsConnectionError(f"Could not reach Google Drive: {e}") from e

    files = result.get("files", [])
    if not files:
        return "No matching Google Docs found."
    return "\n".join(f"{f['name']}  (id={f['id']})  {f.get('webViewLink', '')}" for f in files)


def fetch(document_id_or_url: str) -> str:
    """Fetch the text content of a single Google Doc by id or URL. Read-only."""
    document_id = _resolve_document_id(document_id_or_url)
    try:
        creds = _get_credentials()
        docs = build("docs", "v1", credentials=creds)
        doc = docs.documents().get(documentId=document_id).execute()
    except GoogleDocsConfigError:
        raise
    except HttpError as e:
        raise GoogleDocsConnectionError(f"Google Docs fetch failed: {e}") from e
    except Exception as e:
        raise GoogleDocsConnectionError(f"Could not reach Google Docs: {e}") from e

    return _extract_text(doc)
