"""Live tests: spawn the server over stdio and call every tool against the real Everything API."""

import json
import sys
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((PROJECT / "config.local.json").read_text())


def call(tool: str, args: dict, env: dict | None = None) -> dict:
    async def run():
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "everything_mcp.server"], cwd=str(PROJECT), env=env
        )
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool(tool, args)
            return json.loads(res.content[0].text)

    out = anyio.run(run)
    if tool != "read_file" or "missing" not in str(args):
        assert "error" not in out or env, out
    return out


def test_search_columns_and_sort():
    r = call("search", {"query": "ext:iso", "sort": "size", "ascending": False, "count": 3,
                        "include_size": True, "include_date_modified": True})
    assert r["total_results"] >= r["returned"] > 0
    sizes = [x["size"] for x in r["results"]]
    assert sizes == sorted(sizes, reverse=True)
    assert r["results"][0]["date_modified"].endswith("Z")


def test_paging():
    a = call("search", {"query": "ext:iso", "sort": "size", "count": 2})
    b = call("search", {"query": "ext:iso", "sort": "size", "offset": 1, "count": 1})
    assert a["results"][1]["full_path"] == b["results"][0]["full_path"]


def test_match_flags():
    assert call("count", {"query": "everything.ini"})["total_results"] > 0
    assert call("count", {"query": "everything.ini", "match_case": True})["total_results"] == 0
    assert call("count", {"query": r"^Everything\.ini$", "regex": True})["total_results"] > 0
    assert call("count", {"query": r"Roaming\Everything", "match_path": True})["total_results"] > 0


def test_list_folder_and_read_file():
    r = call("list_folder", {"path": str(PROJECT)})
    names = {x["name"] for x in r["results"]}
    assert {"pyproject.toml", "everything_mcp"} <= names
    f = call("read_file", {"path": str(PROJECT / "pyproject.toml"), "max_bytes": 9})
    assert f["content"] == "[project]" and f["next_offset"] == 9


def test_errors_do_not_leak(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({**CONFIG, "password": "wrong-pw-xyz"}))
    import os
    r = call("search", {"query": "x"}, env={**os.environ, "EVERYTHING_MCP_CONFIG": str(bad)})
    assert "credentials" in r["error"] and "wrong-pw-xyz" not in json.dumps(r)
    remote = tmp_path / "remote.json"
    remote.write_text(json.dumps({**CONFIG, "base_url": "http://example.com"}))
    r = call("count", {"query": "x"}, env={**os.environ, "EVERYTHING_MCP_CONFIG": str(remote)})
    assert "loopback" in r["error"]
    r = call("read_file", {"path": r"C:\definitely\missing.txt"})
    assert "Not found" in r["error"]
    for secret_file in (PROJECT / "config.local.json", r"C:\Program Files\Everything\Everything.ini"):
        r = call("read_file", {"path": str(secret_file)}, env=dict(os.environ))
        assert "blocked" in r["error"] and "content" not in r
