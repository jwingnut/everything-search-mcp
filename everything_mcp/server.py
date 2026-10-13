"""MCP server exposing the local voidtools Everything search engine (stdio transport only)."""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Literal

try:
    from mcp.server.mcpserver import MCPServer  # mcp 2.x
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer

from .client import DEFAULT_CONFIG, EverythingClient, EverythingError, load_config

MAX_COUNT = 1000
MAX_READ_BYTES = 1_048_576

SortKey = Literal["name", "path", "size", "date_modified"]

mcp = MCPServer(
    "everything",
    instructions=(
        "Instant filename/path search across every NTFS volume on this Windows machine "
        "via voidtools Everything. Prefer it over find/glob/recursive "
        "listing for 'where is X' questions. It does not index WSL (/home/...) paths or file "
        "contents. Credentials are handled by the server; never ask for them."
    ),
)

_client: EverythingClient | None = None


def client() -> EverythingClient:
    global _client
    if _client is None:
        _client = EverythingClient(load_config())
    return _client


SEARCH_SYNTAX = """
Query uses Everything search syntax:
  space = AND, | = OR, ! = NOT, "quoted phrase", wildcards * and ?
  ext:pdf;docx   folder: / file:   path:"C:\\Projects"   parent:"C:\\dir" (direct children)
  size:>100mb   dm:today / dm:last6months / dm:2026-01-01..2026-06-30   dc: (created)
  attrib:H   len:>200   dupe:   regex:^foo\\d+$   case:   wholeword:   nopath:
  Exclude a folder: !\\node_modules\\
"""


@mcp.tool(description="Search files and folders by name/path with the Everything engine.\n" + SEARCH_SYNTAX + """
Options mirror the Everything HTTP API: match_case, match_whole_word, match_path (match the
query against the full path instead of just the name), regex (treat the whole query as a regex),
match_diacritics. sort is one of name|path|size|date_modified (the only keys the Everything 1.4
HTTP server honours). Page with offset/count (count max 1000). include_size / include_date_modified
add those columns (size is bytes, null for folders; date is ISO-8601 UTC). total_results is the full
match count regardless of paging.""")
def search(
    query: str,
    match_case: bool = False,
    match_whole_word: bool = False,
    match_path: bool = False,
    regex: bool = False,
    match_diacritics: bool = False,
    sort: SortKey = "name",
    ascending: bool = True,
    offset: int = 0,
    count: int = 50,
    include_path: bool = True,
    include_size: bool = False,
    include_date_modified: bool = False,
) -> dict:
    try:
        return client().search(
            query,
            match_case=match_case,
            match_whole_word=match_whole_word,
            match_path=match_path,
            regex=regex,
            match_diacritics=match_diacritics,
            sort=sort,
            ascending=ascending,
            offset=offset,
            count=min(max(count, 0), MAX_COUNT),
            include_path=include_path,
            include_size=include_size,
            include_date_modified=include_date_modified,
        )
    except EverythingError as e:
        return {"error": str(e)}


@mcp.tool(description="Count matches for a query without returning results (cheap existence check). "
          "Same query syntax and match options as `search`.")
def count(
    query: str,
    match_case: bool = False,
    match_whole_word: bool = False,
    match_path: bool = False,
    regex: bool = False,
    match_diacritics: bool = False,
) -> dict:
    try:
        r = client().search(
            query,
            match_case=match_case,
            match_whole_word=match_whole_word,
            match_path=match_path,
            regex=regex,
            match_diacritics=match_diacritics,
            count=0,
        )
        return {"total_results": r["total_results"]}
    except EverythingError as e:
        return {"error": str(e)}


@mcp.tool(description="List the direct children of a folder (absolute Windows path), optionally "
          "narrowed by an extra Everything filter such as `ext:py` or `folder:`. Supports the same "
          "sort, paging and column options as `search`.")
def list_folder(
    path: str,
    filter: str = "",
    sort: SortKey = "name",
    ascending: bool = True,
    offset: int = 0,
    count: int = 200,
    include_size: bool = True,
    include_date_modified: bool = True,
) -> dict:
    folder = path.rstrip("\\/").replace("/", "\\")
    if folder.endswith(":"):
        folder += "\\"
    query = f'parent:"{folder}" {filter}'.strip()
    try:
        return client().search(
            query,
            sort=sort,
            ascending=ascending,
            offset=offset,
            count=min(max(count, 0), MAX_COUNT),
            include_path=False,
            include_size=include_size,
            include_date_modified=include_date_modified,
        )
    except EverythingError as e:
        return {"error": str(e)}


@mcp.tool(description="Read a file's contents through Everything's HTTP file-download feature. "
          "path is an absolute Windows path (e.g. from search's full_path). Reads max_bytes "
          f"(default 65536, max {MAX_READ_BYTES}) starting at offset; use next_offset to continue. "
          "Text is decoded as UTF-8 (invalid bytes replaced). Binary files are refused unless "
          "as_base64=true.")
def read_file(path: str, offset: int = 0, max_bytes: int = 65536, as_base64: bool = False) -> dict:
    max_bytes = min(max(max_bytes, 1), MAX_READ_BYTES)
    if _holds_credentials(path):
        return {"error": "Reading this file is blocked: it holds Everything credentials."}
    try:
        r = client().read_file(path, offset=offset, max_bytes=max_bytes)
    except EverythingError as e:
        return {"error": str(e)}
    data: bytes = r["data"]
    end = offset + len(data)
    out = {
        "path": path,
        "offset": offset,
        "bytes_returned": len(data),
        "total_size": r["total_size"],
        "next_offset": end if r["total_size"] is not None and end < r["total_size"] else None,
    }
    if as_base64:
        out["encoding"] = "base64"
        out["content"] = base64.b64encode(data).decode("ascii")
    elif b"\x00" in data[:8192]:
        return {**out, "error": "File looks binary; call again with as_base64=true to get raw bytes."}
    else:
        out["encoding"] = "utf-8"
        out["content"] = data.decode("utf-8", errors="replace")
    return out


def _holds_credentials(path: str) -> bool:
    """Our own config and Everything's ini both contain the HTTP password."""
    p = Path(path.replace("/", "\\"))
    name = p.name.lower()
    if name.startswith("everything") and name.endswith(".ini"):
        return True
    config = Path(os.environ.get("EVERYTHING_MCP_CONFIG") or DEFAULT_CONFIG)
    return name == config.name.lower() or os.path.normcase(str(p)) == os.path.normcase(str(config))


def main() -> None:
    mcp.run()  # stdio only; this server never opens a network listener


if __name__ == "__main__":
    main()
