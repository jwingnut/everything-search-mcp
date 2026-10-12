# Registering `everything` with MCP clients

The server is a stdio command with no arguments:

```
<repo>\.venv\Scripts\everything-mcp.exe
```

Replace `<repo>` (and `C:\path\to\everything-search-mcp` in the snippets) with the folder you cloned
this repository into. Run `uv sync` there first so the `.venv` exists.

| Client | Command | Or edit this file |
|--------|---------|-------------------|
| Claude Code (all projects) | `claude mcp add --scope user everything -- <repo>\.venv\Scripts\everything-mcp.exe` | `~/.claude.json` → `mcpServers` |
| Claude Code (one project) | `claude mcp add --scope project everything -- <exe>` | `<project>\.mcp.json`, see [`claude.mcp.json`](claude.mcp.json) |
| Codex | `codex mcp add everything -- <exe>` | `~/.codex/config.toml`, see [`codex.config.toml`](codex.config.toml) |
| Pi (built-in MCP) | `pi mcp add everything --description "Instant Windows filename/path search (voidtools Everything)" -- <exe>` | `~/.pi/agent/mcp.json`, see [`pi.mcp.json`](pi.mcp.json) |
| Claude Desktop | n/a | `%APPDATA%\Claude\claude_desktop_config.json` → `mcpServers` (same entry as `claude.mcp.json`) |
| Cursor | n/a | `~/.cursor/mcp.json` → `mcpServers` (same entry) |

Clients pick up a new server when a session starts (or after `/mcp` reconnects in Claude Code).

**From WSL:** call the Windows exe through interop, e.g.
`command = "/mnt/c/path/to/everything-search-mcp/.venv/Scripts/everything-mcp.exe"`.
Everything itself still runs on Windows; the server reaches it at `127.0.0.1`.

## Removing it again

- Claude Code: `claude mcp remove --scope user everything`
- Codex: `codex mcp remove everything` (or delete the `[mcp_servers.everything]` block)
- Pi: `pi mcp remove everything`
- Claude Desktop / Cursor: delete the `everything` entry from `mcpServers`
