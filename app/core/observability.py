import hashlib
import json
import logging

from redis.exceptions import RedisError

from app.core.config import settings
from app.core.redis_client import get_redis_client

logger = logging.getLogger(__name__)


def log_event(level: int, event: str, **fields: str | int | float) -> None:
    logger.log(
        level,
        json.dumps(
            {"event": event, **fields},
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def increment(name: str, **labels: str) -> None:
    if not settings.metrics_enabled:
        return
    labels_json = json.dumps(labels, sort_keys=True, separators=(",", ":"))
    key = _metric_key(name, labels_json)
    try:
        client = get_redis_client()
        client.hincrby(key, "value", 1)
        client.hset(key, "labels", labels_json)
        client.expire(key, settings.metrics_retention_seconds)
    except RedisError:
        logger.exception("Could not record metric name=%s", name)


def observe(name: str, value: float, **labels: str) -> None:
    if not settings.metrics_enabled:
        return
    labels_json = json.dumps(labels, sort_keys=True, separators=(",", ":"))
    key = _metric_key(name, labels_json)
    try:
        client = get_redis_client()
        with client.pipeline(transaction=True) as pipeline:
            pipeline.hincrby(key, "count", 1)
            pipeline.hincrbyfloat(key, "sum", value)
            pipeline.hset(key, "labels", labels_json)
            pipeline.expire(key, settings.metrics_retention_seconds)
            pipeline.execute()
    except RedisError:
        logger.exception("Could not record metric name=%s", name)


def render_prometheus_metrics() -> str:
    if not settings.metrics_enabled:
        return ""
    client = get_redis_client()
    lines: list[str] = []
    try:
        for key in client.scan_iter(match="metrics:*"):
            fields = client.hgetall(key)
            labels = json.loads(fields["labels"])
            label_text = _format_labels(labels)
            if "value" in fields:
                metric_name = key.split(":")[1]
                lines.append(
                    f"{metric_name}{label_text} {fields['value']}"
                )
            else:
                metric_name = key.split(":")[1]
                lines.extend(
                    (
                        f"{metric_name}_count{label_text} {fields['count']}",
                        f"{metric_name}_sum{label_text} {fields['sum']}",
                    )
                )
    except (RedisError, KeyError, ValueError, TypeError):
        logger.exception("Could not read metrics from Redis")
        raise
    return "\n".join(sorted(lines)) + "\n"


def _metric_key(name: str, labels_json: str) -> str:
    label_hash = hashlib.sha256(
        labels_json.encode("utf-8")
    ).hexdigest()[:16]
    return f"metrics:{name}:{label_hash}"


def _format_labels(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    formatted_labels = []
    for key, value in sorted(labels.items()):
        escaped_value = value.replace("\\", "\\\\").replace('"', '\\"')
        formatted_labels.append(f'{key}="{escaped_value}"')
    return "{" + ",".join(formatted_labels) + "}"
