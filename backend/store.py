"""Small key-value store for data the app writes at runtime: generated locales,
learner requests for missing languages, and reviews.

- Locally: a JSON file in backend/data/ (git-ignored).
- On Vercel: Upstash Redis (add it from Vercel's Marketplace -> Storage; it sets
  KV_REST_API_URL / KV_REST_API_TOKEN, or UPSTASH_REDIS_REST_URL / _TOKEN).
  Vercel's disk is temporary, so without Redis nothing written there survives.
"""
import json
import os
import threading
from pathlib import Path

import httpx

_URL = os.getenv("KV_REST_API_URL") or os.getenv("UPSTASH_REDIS_REST_URL")
_TOKEN = os.getenv("KV_REST_API_TOKEN") or os.getenv("UPSTASH_REDIS_REST_TOKEN")
DATA_FILE = Path(__file__).parent / "data" / "store.json"
_lock = threading.Lock()


def backend_name() -> str:
    if _URL and _TOKEN:
        return "redis"
    return "temporary" if os.getenv("VERCEL") else "file"


def is_persistent() -> bool:
    return backend_name() != "temporary"


def _redis(*cmd):
    r = httpx.post(_URL, headers={"Authorization": f"Bearer {_TOKEN}"}, json=list(cmd), timeout=10)
    r.raise_for_status()
    return r.json().get("result")


def _file_path() -> Path:
    if os.getenv("VERCEL"):
        import tempfile
        return Path(tempfile.gettempdir()) / "iclass_store.json"
    return DATA_FILE


def _load() -> dict:
    p = _file_path()
    return json.loads(p.read_text()) if p.exists() else {}


def _save(data: dict) -> None:
    p = _file_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(p)


# --- hashes: name -> {field: json value} ---

def hget_all(name: str) -> dict:
    if backend_name() == "redis":
        flat = _redis("HGETALL", name) or []
        return {flat[i]: json.loads(flat[i + 1]) for i in range(0, len(flat), 2)}
    with _lock:
        return _load().get(name, {})


def hget(name: str, field: str):
    if backend_name() == "redis":
        v = _redis("HGET", name, field)
        return json.loads(v) if v else None
    with _lock:
        return _load().get(name, {}).get(field)


def hset(name: str, field: str, value) -> None:
    if backend_name() == "redis":
        _redis("HSET", name, field, json.dumps(value, ensure_ascii=False))
        return
    with _lock:
        data = _load()
        data.setdefault(name, {})[field] = value
        _save(data)


def hdel(name: str, field: str) -> None:
    if backend_name() == "redis":
        _redis("HDEL", name, field)
        return
    with _lock:
        data = _load()
        data.get(name, {}).pop(field, None)
        _save(data)


def hincr(name: str, field: str) -> int:
    if backend_name() == "redis":
        return int(_redis("HINCRBY", name, field, 1))
    with _lock:
        data = _load()
        h = data.setdefault(name, {})
        h[field] = int(h.get(field, 0)) + 1
        _save(data)
        return h[field]


# --- list ---

def lpush(name: str, value) -> None:
    if backend_name() == "redis":
        _redis("LPUSH", name, json.dumps(value, ensure_ascii=False))
        return
    with _lock:
        data = _load()
        data.setdefault(name, []).insert(0, value)
        _save(data)


def lrange(name: str, count: int = 200) -> list:
    if backend_name() == "redis":
        return [json.loads(v) for v in (_redis("LRANGE", name, 0, count - 1) or [])]
    with _lock:
        return _load().get(name, [])[:count]
