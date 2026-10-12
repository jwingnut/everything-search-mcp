"""Thin client for the voidtools Everything HTTP server (JSON mode).

Credentials come from a local, git-ignored config file and are only ever placed
in the Authorization header. They are never returned, logged, or put in URLs.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.local.json"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

# Everything 1.4's HTTP server only honours these sort keys and columns;
# anything else silently falls back to name order / is omitted.
SORT_KEYS = ("name", "path", "size", "date_modified")

_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


class EverythingError(RuntimeError):
    """User-facing error; message must never contain credentials."""


@dataclass(frozen=True)
class Config:
    base_url: str
    username: str
    password: str
    timeout_seconds: float = 15.0


def load_config(path: str | os.PathLike | None = None) -> Config:
    path = Path(path or os.environ.get("EVERYTHING_MCP_CONFIG") or DEFAULT_CONFIG)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise EverythingError(
            f"Config file not found: {path}. Copy secrets.example to config.local.json."
        ) from None
    except json.JSONDecodeError as e:
        raise EverythingError(f"Config file {path} is not valid JSON (line {e.lineno}).") from None

    base_url = str(raw.get("base_url", "http://127.0.0.1:80")).rstrip("/")
    host = urllib.parse.urlsplit(base_url).hostname
    if host not in LOOPBACK_HOSTS:
        # Refuse to send credentials anywhere but this machine.
        raise EverythingError(f"base_url host must be loopback ({', '.join(sorted(LOOPBACK_HOSTS))}).")
    return Config(
        base_url=base_url,
        username=str(raw.get("username", "")),
        password=str(raw.get("password", "")),
        timeout_seconds=float(raw.get("timeout_seconds", 15)),
    )


def filetime_to_iso(value: str | int | None) -> str | None:
    if value in (None, ""):
        return None
    return (_FILETIME_EPOCH + timedelta(microseconds=int(value) // 10)).isoformat().replace("+00:00", "Z")


class EverythingClient:
    def __init__(self, config: Config):
        self.config = config
        token = base64.b64encode(f"{config.username}:{config.password}".encode()).decode()
        self._auth = f"Basic {token}"

    def _get(self, url: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
        req = urllib.request.Request(url, headers={"Authorization": self._auth, **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise EverythingError("Everything rejected the configured credentials.") from None
            if e.code == 404:
                raise EverythingError("Not found on the Everything server.") from None
            if e.code == 416:
                raise EverythingError("offset is at or beyond the end of the file.") from None
            raise EverythingError(f"Everything HTTP error {e.code}.") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            reason = getattr(e, "reason", e)
            raise EverythingError(
                f"Cannot reach Everything at {self.config.base_url} ({reason}). Is the HTTP server enabled?"
            ) from None

    def search(
        self,
        query: str,
        *,
        match_case: bool = False,
        match_whole_word: bool = False,
        match_path: bool = False,
        regex: bool = False,
        match_diacritics: bool = False,
        sort: str = "name",
        ascending: bool = True,
        offset: int = 0,
        count: int = 50,
        include_path: bool = True,
        include_size: bool = False,
        include_date_modified: bool = False,
    ) -> dict:
        if sort not in SORT_KEYS:
            raise EverythingError(f"sort must be one of {', '.join(SORT_KEYS)}.")
        params = {
            "s": query,
            "j": 1,
            "o": max(0, int(offset)),
            "c": max(0, int(count)),
            "i": int(match_case),
            "w": int(match_whole_word),
            "p": int(match_path),
            "r": int(regex),
            "m": int(match_diacritics),
            "sort": sort,
            "ascending": int(ascending),
            # Always fetch path so full_path can be built; strip it later if unwanted.
            "path_column": 1,
            "size_column": int(include_size),
            "date_modified_column": int(include_date_modified),
        }
        url = f"{self.config.base_url}/?{urllib.parse.urlencode(params)}"
        _, _, body = self._get(url)
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise EverythingError("Everything returned a non-JSON response.") from None

        results = []
        for r in data.get("results", []):
            parent = r.get("path", "")
            item = {"type": r.get("type"), "name": r.get("name")}
            item["full_path"] = f"{parent}\\{r.get('name')}" if parent else r.get("name")
            if include_path:
                item["path"] = parent
            if include_size:
                item["size"] = int(r["size"]) if r.get("size") else None  # folders have no size
            if include_date_modified:
                item["date_modified"] = filetime_to_iso(r.get("date_modified"))
            results.append(item)
        return {
            "total_results": data.get("totalResults", 0),
            "offset": params["o"],
            "returned": len(results),
            "results": results,
        }

    def read_file(self, path: str, offset: int = 0, max_bytes: int = 65536) -> dict:
        """Fetch (part of) a file via Everything's file-download feature using a Range request."""
        p = path.replace("\\", "/")
        if len(p) < 3 or p[1:3] != ":/":
            raise EverythingError("path must be an absolute Windows path like C:\\dir\\file.txt.")
        url = f"{self.config.base_url}/{urllib.parse.quote(p, safe='/:')}"
        offset = max(0, int(offset))
        status, headers, body = self._get(url, {"Range": f"bytes={offset}-{offset + max_bytes - 1}"})
        if headers.get("Content-Type", "").startswith("text/html") and status == 200:
            raise EverythingError("path is a folder, not a file. Use list_folder instead.")

        total = None
        content_range = headers.get("Content-Range", "")  # "bytes 0-19/1211"
        if "/" in content_range:
            total = int(content_range.rsplit("/", 1)[1])
        elif status == 200:
            # Server ignored the Range header and sent the whole file.
            total = len(body)
            body = body[offset : offset + max_bytes]
        return {"total_size": total, "data": body}
