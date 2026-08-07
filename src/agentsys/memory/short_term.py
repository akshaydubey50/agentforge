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


def append_value(task_id: str, field: str, item: Any) -> None:
    """Appends to a list field with a single atomic Redis RPUSH, instead of the
    read-modify-write (get -> append -> set) pattern set_value would require.
    Required once parallel subtasks can write to the same task's scratchpad
    concurrently: two branches both reading the same list and writing back
    would silently lose one branch's note (last write wins)."""
    get_redis().rpush(_key(task_id, field), json.dumps(item))


def get_list(task_id: str, field: str) -> list[Any]:
    raw_items = get_redis().lrange(_key(task_id, field), 0, -1)
    return [json.loads(raw) for raw in raw_items]


def get_all(task_id: str) -> dict[str, Any]:
    """Only reads string-valued fields written via set_value -- a field
    written with append_value is a Redis list and client.get() on it raises
    WRONGTYPE, so callers mixing both must use get_list for list fields."""
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
