"""Step 6 (connector 1 of 5): read-only Notion search/fetch via Notion's
official hosted MCP server.

Per the project brief's Step 6 ordering, MCP connectors are added one at a
time, lowest-risk first, each verified working before the next is started.
Notion is first because a hosted MCP server already exists at mcp.notion.com
— the brief calls for integrating directly against it rather than building a
bespoke wrapper around Notion's REST API.

Design decisions (justified per project convention — not defaults):

- Client: the official `mcp` Python SDK's streamable-HTTP client, pointed at
  https://mcp.notion.com/mcp, rather than Notion's own REST API client. This
  keeps every future MCP-based connector (Google Docs, etc.) the same shape:
  one small tool-calling adapter per service, not a different auth/HTTP
  convention for each.
- Auth: a bearer token from MNEMOS_NOTION_TOKEN (machine-specific, no
  default — same pattern as MNEMOS_PIPER_MODEL). Notion's hosted MCP server
  authenticates over OAuth; Mnemos doesn't run its own OAuth flow yet, so
  the token has to come from completing that flow once via another
  MCP-aware client or bridge (documented in README) and pasting the
  resulting access token here.
- Read-only only: this file exposes `search()` and `fetch()` and nothing
  else. Notion's hosted server also exposes create/update/comment tools;
  the simplest way to guarantee the project's confirm-before-send rule can
  never be accidentally skipped for Notion is to not implement any write
  tool calls at all until that gating exists, rather than trust every call
  site to remember to check first.
- Status: not yet exercised against a real Notion workspace (no token was
  available while writing this) — the request/response shape is inferred
  from the MCP spec and Notion's published hosted-server behavior, not
  verified end-to-end. Treat this the same as the untested Ctrl+Space
  dictation change: verify manually with a real token before relying on
  it, and paste back the actual output.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

DEFAULT_MCP_URL = "https://mcp.notion.com/mcp"
REQUEST_TIMEOUT_SECONDS = 30


class NotionConfigError(RuntimeError):
    """Raised when MNEMOS_NOTION_TOKEN isn't set."""


class NotionConnectionError(RuntimeError):
    """Raised when the Notion MCP server can't be reached or returns an error."""


def _get_token() -> str:
    token = os.environ.get("MNEMOS_NOTION_TOKEN")
    if not token:
        raise NotionConfigError(
            "MNEMOS_NOTION_TOKEN is not set. Complete Notion's OAuth flow "
            "(see README's Notion connector section) and set this to the "
            "resulting access token."
        )
    return token


def _get_mcp_url() -> str:
    return os.environ.get("MNEMOS_NOTION_MCP_URL", DEFAULT_MCP_URL)


def _text_from_content(content: Any) -> str:
    parts = []
    for block in content or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


def _innermost_message(e: BaseException) -> str:
    # anyio's TaskGroup wraps connection/transport failures in an
    # ExceptionGroup; the actual reason (e.g. an HTTP 403) is one level
    # down and far more useful to surface than "unhandled errors in a
    # TaskGroup (1 sub-exception)".
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    return f"{type(e).__name__}: {e}"


async def _call_tool(tool_name: str, arguments: dict) -> str:
    token = _get_token()
    url = _get_mcp_url()
    http_client = httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    try:
        async with streamable_http_client(url, http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
    except Exception as e:
        raise NotionConnectionError(
            f"Could not complete Notion MCP call '{tool_name}' at {url}: {_innermost_message(e)}"
        ) from e
    finally:
        await http_client.aclose()

    if result.is_error:
        raise NotionConnectionError(
            f"Notion MCP tool '{tool_name}' returned an error: {_text_from_content(result.content)}"
        )

    return _text_from_content(result.content)


def search(query: str) -> str:
    """Search the connected Notion workspace. Read-only — no confirm gate needed."""
    return asyncio.run(_call_tool("search", {"query": query}))


def fetch(page_id_or_url: str) -> str:
    """Fetch a single Notion page/database by id or url. Read-only."""
    return asyncio.run(_call_tool("fetch", {"id": page_id_or_url}))
