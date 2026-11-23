"""Shared helpers: config, paths, HTTP, DuckDB connections, run lock, downloads."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import os
import time
from collections.abc import Iterator
from pathlib import Path

import duckdb
import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    with open(ROOT / "config" / "sources.yml") as f:
        return yaml.safe_load(f)


def path(cfg: dict, key: str) -> Path:
    p = ROOT / cfg["paths"][key]
    if key.endswith("_dir"):
        p.mkdir(parents=True, exist_ok=True)
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
    return p


def http_client(cfg: dict) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": cfg["http"]["user_agent"]},
        timeout=cfg["http"]["timeout_s"],
        follow_redirects=True,
    )


def get_json(client: httpx.Client, url: str, retries: int = 5, **params) -> dict | list:
    """GET with exponential backoff on 429/5xx and connection errors."""
    for attempt in range(retries):
        try:
            r = client.get(url, params=params or None)
            if r.status_code == 429 or r.status_code >= 500:
                raise httpx.HTTPStatusError("retryable", request=r.request, response=r)
            r.raise_for_status()
            return r.json()
        except (httpx.TransportError, httpx.HTTPStatusError) as e:
            retryable = not isinstance(e, httpx.HTTPStatusError) or (
                e.response.status_code == 429 or e.response.status_code >= 500
            )
            if not retryable or attempt == retries - 1:
                raise
            time.sleep(min(2**attempt, 60))
    raise RuntimeError("unreachable")


def connect(cfg: dict, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(path(cfg, "warehouse")), read_only=read_only)
    con.execute(f"SET memory_limit = '{cfg['duckdb']['memory_limit']}'")
    con.execute(f"SET threads = {int(cfg['duckdb']['threads'])}")
    con.execute(f"SET temp_directory = '{path(cfg, 'tmp_dir')}'")
    con.execute("SET preserve_insertion_order = false")  # lower memory on big loads
    return con


@contextlib.contextmanager
def run_lock(cfg: dict) -> Iterator[None]:
    """Stop a scheduled run and a manual run from overlapping."""
    lock_path = path(cfg, "data_dir") / ".sync.lock"
    with open(lock_path, "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit(f"another sync holds {lock_path}; exiting") from None
        f.write(str(os.getpid()))
        f.flush()
        yield


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(8 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(client: httpx.Client, url: str, dest: Path, retries: int = 5) -> Path:
    """Resumable download to dest via dest.part. Verifies the final size."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return dest
    part = dest.with_name(dest.name + ".part")
    for attempt in range(retries):
        have = part.stat().st_size if part.exists() else 0
        # identity: byte ranges and Content-Length must refer to the file itself,
        # not a gzip-encoded transfer (CMS gzips by default)
        headers = {"Accept-Encoding": "identity"}
        if have:
            headers["Range"] = f"bytes={have}-"
        try:
            with client.stream("GET", url, headers=headers) as r:
                if r.status_code == 416:  # already complete
                    break
                r.raise_for_status()
                if r.headers.get("content-encoding", "identity") != "identity":
                    raise RuntimeError(f"server ignored identity encoding for {url}")
                if have and r.status_code != 206:  # server ignored Range: restart
                    have = 0
                total = have + int(r.headers.get("content-length", 0))
                with open(part, "ab" if have else "wb") as f:
                    last = time.monotonic()
                    for chunk in r.iter_raw(8 << 20):
                        f.write(chunk)
                        if time.monotonic() - last > 30:
                            print(f"  {dest.name}: {f.tell() / 1e9:.2f}/{total / 1e9:.2f} GB")
                            last = time.monotonic()
            if total and part.stat().st_size != total:
                if part.stat().st_size > total:
                    part.unlink()  # corrupt; start over
                raise OSError(
                    f"size mismatch {part.stat().st_size if part.exists() else 0} != {total}"
                )
            break
        except (httpx.TransportError, httpx.HTTPStatusError, OSError) as e:
            if attempt == retries - 1:
                raise
            print(f"  retry {attempt + 1} after {e!r}")
            time.sleep(min(2**attempt, 60))
    part.rename(dest)
    return dest


def ident(name: str) -> str:
    """Allow only simple identifiers into SQL built with f-strings."""
    if not name.replace("_", "").isalnum():
        raise ValueError(f"unsafe identifier: {name!r}")
    return name
