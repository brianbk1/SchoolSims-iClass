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

def _find_rest() -> tuple[str | None, str | None]:
    """Upstash REST credentials. Vercel names them with a prefix you can choose when
    connecting (KV_..., UPSTASH_REDIS_..., STORAGE_..., etc.), so look for any pair."""
    env = os.environ
    for url_key in sorted(env):
        for suffix, token_suffix in (("REST_API_URL", "REST_API_TOKEN"), ("REDIS_REST_URL", "REDIS_REST_TOKEN")):
            if url_key.endswith(suffix) and env[url_key].startswith("https://"):
                token = env.get(url_key[: -len(suffix)] + token_suffix)
                if token:
                    return env[url_key], token
    return None, None


def _find_tcp() -> str | None:
    """A plain Redis connection string (Vercel's Redis / Redis Cloud): REDIS_URL, KV_URL…"""
    env = os.environ
    for key in sorted(env):
        if (key.endswith("REDIS_URL") or key.endswith("KV_URL")) and env[key].startswith(("redis://", "rediss://")):
            return env[key]
    return None


_URL, _TOKEN = _find_rest()
_TCP_URL = None if _URL else _find_tcp()
_tcp_client = None
DATA_FILE = Path(__file__).parent / "data" / "store.json"
_lock = threading.Lock()


def backend_name() -> str:
    if _URL and _TOKEN:
        return "redis"
    if _TCP_URL:
        return "redis"
    return "temporary" if os.getenv("VERCEL") else "file"


def is_persistent() -> bool:
    return backend_name() != "temporary"


def storage_vars_seen() -> list[str]:
    """Names (never values) of settings that look like storage, for error messages."""
    return sorted(k for k in os.environ if any(w in k for w in ("REDIS", "KV_", "UPSTASH")))


def _redis(*cmd):
    if _URL and _TOKEN:
        r = httpx.post(_URL, headers={"Authorization": f"Bearer {_TOKEN}"}, json=list(cmd), timeout=10)
        r.raise_for_status()
        return r.json().get("result")
    global _tcp_client
    if _tcp_client is None:
        import redis  # lazy: only needed with a plain Redis URL
        _tcp_client = redis.Redis.from_url(_TCP_URL, decode_responses=True, socket_timeout=10)
    result = _tcp_client.execute_command(*cmd)
    if isinstance(result, dict):  # redis-py turns HGETALL into a dict; keep the REST shape
        flat = []
        for k, v in result.items():
            flat += [k, v]
        return flat
    return result


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
