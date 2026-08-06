import json
from typing import Any

import redis

from agentsys.config import settings

_redis: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis


def _key(task_id: str, field: str) -> str:
    return f"task:{task_id}:mem:{field}"


def set_value(task_id: str, field: str, value: Any) -> None:
    get_redis().set(_key(task_id, field), json.dumps(value))


def get_value(task_id: str, field: str) -> Any | None:
    raw = get_redis().get(_key(task_id, field))
    return json.loads(raw) if raw is not None else None


def get_all(task_id: str) -> dict[str, Any]:
    client = get_redis()
    pattern = _key(task_id, "*")
    result = {}
    for key in client.scan_iter(match=pattern):
        field = key.split(":mem:", 1)[1]
        result[field] = json.loads(client.get(key))
    return result


def clear(task_id: str) -> None:
    """Working memory is scoped to a single task and cleared on completion —
    it shouldn't leak into unrelated future tasks the way long-term memory does."""
    client = get_redis()
    pattern = _key(task_id, "*")
    keys = list(client.scan_iter(match=pattern))
    if keys:
        client.delete(*keys)
