# everything-mcp

An MCP server (stdio) for the local [voidtools Everything](https://www.voidtools.com/) HTTP
search API. It gives any agent instant filename and path search across every NTFS volume Everything
indexes, and the agent never sees the Everything credentials.

- Speaks stdio only and never opens a listening port.
- Talks to Everything at `http://127.0.0.1:80`, and refuses any non-loopback `base_url`.
- Does **not** index WSL paths (`/home/...`) or file contents. A miss only rules out the Windows side.

**Targets:** the HTTP server built into Everything 1.4 (tested against 1.4.1.1022, JSON mode `j=1`).

**Hardening tip:** Everything's HTTP server listens on all interfaces by default. If only local
tools use it, set Tools → Options → HTTP Server → "Bind to interfaces" to `127.0.0.1`, and use a
strong HTTP password. Consider disabling file download if you don't need `read_file`.

## Install

Requires [uv](https://docs.astral.sh/uv/) and a running Everything with its HTTP server
enabled (Tools → Options → HTTP Server).

```powershell
git clone https://github.com/jwingnut/everything-search-mcp.git
cd everything-search-mcp
copy secrets.example config.local.json       # then fill in username/password
uv sync                                      # creates .venv and the everything-mcp.exe entry point
uv run pytest -q                             # live tests against the running Everything server
```

Launch command for MCP clients. It takes no arguments and works from any working directory:

```
<repo>\.venv\Scripts\everything-mcp.exe
```

`<repo>` is wherever you cloned this repository, e.g. `C:\tools\everything-search-mcp`.

## Config file

| What | Where |
|------|-------|
| Default location | `config.local.json` in the repo root, next to `pyproject.toml` (git-ignored) |
| Override | Environment variable `EVERYTHING_MCP_CONFIG=<path to json>` |
| Template | `secrets.example` |

```json
{
  "base_url": "http://127.0.0.1:80",
  "username": "...",
  "password": "...",
  "timeout_seconds": 15
}
```

The server sends the credentials only in the HTTP `Authorization` header. They never go into
URLs, logs, error messages or tool output. `read_file` refuses to open this config file and
`Everything*.ini`, since both contain the password.

## Tool reference

Clients show the tools under the `everything` server, e.g. `mcp__everything__search` in Claude Code.

### `search`

Searches file and folder names (or full paths) using Everything's query syntax.

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `query` | (required) | Everything syntax. See the cheat sheet below. |
| `match_case` | `false` | Case-sensitive match. |
| `match_whole_word` | `false` | Whole words only. |
| `match_path` | `false` | Match against the full path instead of just the name. |
| `regex` | `false` | Treat the whole query as a regular expression. |
| `match_diacritics` | `false` | Treat `é` and `e` as different characters. |
| `sort` | `name` | `name`, `path`, `size` or `date_modified`. |
| `ascending` | `true` | Sort direction. |
| `offset` / `count` | `0` / `50` | Paging. `count` max is 1000; `total_results` is always the full match count. |
| `include_path` | `true` | Include the parent folder (`path`). `full_path` is always included. |
| `include_size` | `false` | Size in bytes (`null` for folders). |
| `include_date_modified` | `false` | ISO-8601 UTC timestamp. |

Example: the three largest ISO files.

```json
// search {"query": "ext:iso", "sort": "size", "ascending": false, "count": 3,
//         "include_size": true, "include_date_modified": true, "include_path": false}
{"total_results": 12, "offset": 0, "returned": 3, "results": [
  {"type": "file", "name": "example-os-installer.iso",
   "full_path": "E:\\Images\\example-os-installer.iso",
   "size": 5000000000, "date_modified": "2025-01-15T09:00:00Z"},
  {"type": "file", "name": "sample-disk.iso",
   "full_path": "C:\\Users\\me\\Downloads\\sample-disk.iso",
   "size": 4000000000, "date_modified": "2025-02-01T12:00:00Z"},
  ...]}
```

Example: a regex search.

```json
// search {"query": "^everything.*\\.exe$", "regex": true, "include_path": false}
{"total_results": 2, "results": [
  {"type": "file", "name": "everything-mcp.exe", "full_path": "C:\\tools\\everything-search-mcp\\.venv\\Scripts\\everything-mcp.exe"},
  {"type": "file", "name": "Everything.exe", "full_path": "C:\\Program Files\\Everything\\Everything.exe"}]}
```

Example: paging. Get the second page of 50 with `{"query": "ext:pdf", "offset": 50, "count": 50}`.

### `count`

Returns the number of matches without fetching results, which makes it a cheap existence
check. It takes the same `query` and match flags as `search`.

```json
// count {"query": "ext:pdf"}
{"total_results": 4127}
// count {"query": "everything.ini", "match_case": true}
{"total_results": 0}
```

### `list_folder`

Lists the direct children of a folder, using `parent:"<path>"`.

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `path` | (required) | Absolute Windows folder path (`C:\dir` or `C:/dir`; a drive root like `D:` works too). |
| `filter` | `""` | Extra Everything filter ANDed in, e.g. `ext:py` or `folder:`. |
| `sort`, `ascending`, `offset`, `count` | `name`, `true`, `0`, `200` | Same as `search`. |
| `include_size`, `include_date_modified` | `true`, `true` | Columns. |

```json
// list_folder {"path": "C:\\tools\\everything-search-mcp", "filter": "file:"}
{"total_results": 5, "results": [
  {"type": "file", "name": "secrets.example", "full_path": "...\\secrets.example", "size": 119, "date_modified": "2025-03-01T10:00:00Z"},
  {"type": "file", "name": "pyproject.toml", "full_path": "...\\pyproject.toml", "size": 385, "date_modified": "..."},
  ...]}
```

### `read_file`

Reads a file through Everything's file-download feature, in chunks fetched with HTTP Range
requests.

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `path` | (required) | Absolute Windows file path, e.g. a `full_path` from `search`. |
| `offset` | `0` | Byte offset to start at. |
| `max_bytes` | `65536` | Chunk size, max 1 MiB. |
| `as_base64` | `false` | Return raw bytes as base64. Without it, binary files are refused. |

```json
// read_file {"path": "C:\\tools\\everything-search-mcp\\pyproject.toml", "max_bytes": 60}
{"path": "...\\pyproject.toml", "offset": 0, "bytes_returned": 60, "total_size": 385,
 "next_offset": 60, "encoding": "utf-8", "content": "[project]\r\nname = \"everything-mcp\"\r\n..."}
```

To read the whole file, keep calling with `offset = next_offset` until `next_offset` is `null`.

### Errors

Tools return `{"error": "..."}` instead of throwing. Possible errors: Everything unreachable,
rejected credentials, path not found, offset past the end of the file, binary file without
`as_base64`, blocked credential file.

### Query cheat sheet

| Syntax | Meaning |
|--------|---------|
| `foo bar` / `foo \| bar` / `!foo` | AND / OR / NOT |
| `"exact phrase"`, `*.pdf`, `report?.docx` | Phrases and wildcards |
| `ext:pdf;docx` | Extension list |
| `file:` / `folder:` | Files only / folders only |
| `path:"C:\Projects" foo` | Restrict to a subtree |
| `parent:"C:\dir"` | Direct children only |
| `!\node_modules\ !\.venv\` | Exclude folders |
| `size:>100mb`, `size:1kb..10kb` | Size filters |
| `dm:today`, `dm:last6months`, `dm:2026-01-01..2026-06-30` | Date modified |
| `dc:thisweek` | Date created (filter only; it can't be returned as a column) |
| `attrib:H`, `len:>200`, `dupe:` | Attributes, name length, duplicate names |
| `regex:^foo\d+$`, `case:`, `wholeword:`, `nopath:` | Inline modifiers |

## Limitations (Everything 1.4.1 HTTP server)

The HTTP server returns only the path, size and date-modified columns, and only honours
the name, path, size and date_modified sort keys. It silently ignores any other column
or sort key, so the server doesn't expose them. You can still *filter* on them in the query.

## Registering with agents

See [`registration/README.md`](registration/README.md) for config snippets and how to remove them again. In short:

- **Claude Code:** `claude mcp add --scope user everything -- <exe>`, or put [`registration/claude.mcp.json`](registration/claude.mcp.json) in a project's `.mcp.json`.
- **Codex:** append [`registration/codex.config.toml`](registration/codex.config.toml) to `~/.codex/config.toml`, or run `codex mcp add everything -- <exe>`.
- **Pi (1.0.4, built-in MCP):** `pi mcp add everything -- <exe>`, which writes [`registration/pi.mcp.json`](registration/pi.mcp.json) to `~/.pi/agent/mcp.json`.

## Development

```powershell
uv run pytest -q   # 5 live tests: drive the server over real MCP stdio against Everything
```

The MCP SDK is pinned to `mcp<2`, because 2.x renamed `FastMCP`.

## License

MIT. See [LICENSE](LICENSE).
