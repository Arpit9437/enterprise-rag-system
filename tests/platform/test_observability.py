from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.core import observability
from app.core.config import settings
from app.core.rate_limit import rate_limit
from app.main import app
from fastapi.testclient import TestClient


class FakeRedis:
    def __init__(self):
        self.hashes = {}
        self.count = 0

    def hincrby(self, key, field, amount):
        values = self.hashes.setdefault(key, {})
        values[field] = int(values.get(field, 0)) + amount
        return values[field]

    def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value

    def expire(self, key, seconds):
        return True

    def pipeline(self, transaction=True):
        return FakePipeline(self)

    def scan_iter(self, match):
        return iter(key for key in self.hashes if key.startswith("metrics:"))

    def hgetall(self, key):
        return self.hashes[key]

    def eval(self, script, key_count, key, window):
        self.count += 1
        return self.count


class FakePipeline:
    def __init__(self, client):
        self.client = client
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def hincrby(self, key, field, amount):
        self.calls.append(("hincrby", key, field, amount))

    def hincrbyfloat(self, key, field, amount):
        self.calls.append(("hincrbyfloat", key, field, amount))

    def hset(self, key, field, value):
        self.calls.append(("hset", key, field, value))

    def expire(self, key, seconds):
        self.calls.append(("expire", key, seconds, None))

    def execute(self):
        for operation, key, field, value in self.calls:
            if operation == "hincrby":
                self.client.hincrby(key, field, value)
            elif operation == "hincrbyfloat":
                values = self.client.hashes.setdefault(key, {})
                values[field] = float(values.get(field, 0)) + value
            elif operation == "expire":
                continue
            else:
                self.client.hset(key, field, value)


def make_request():
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/test",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("192.0.2.1", 1234),
            "scheme": "http",
        }
    )


def test_request_id_is_returned_and_invalid_incoming_ids_are_replaced():
    with TestClient(app) as client:
        legacy_health = client.get("/health")
        assert legacy_health.json() == {
            "mssg": "FastAPI application running."
        }

        incoming_id = str(uuid4())
        response = client.get(
            "/health/live",
            headers={"X-Request-ID": incoming_id},
        )
        assert response.headers["X-Request-ID"] == incoming_id

        invalid = client.get(
            "/health/live",
            headers={"X-Request-ID": "not-an-id"},
        )
        assert _is_valid_uuid(invalid.headers["X-Request-ID"])


def _is_valid_uuid(value: str) -> bool:
    try:
        UUID(value)
    except (TypeError, ValueError):
        return False
    return True


def test_metrics_are_shared_and_render_prometheus_format(monkeypatch):
    redis_client = FakeRedis()
    monkeypatch.setattr(settings, "metrics_enabled", True)
    monkeypatch.setattr(
        observability,
        "get_redis_client",
        lambda: redis_client,
    )

    observability.increment("rag_requests_total", operation="ask")
    observability.observe(
        "rag_duration_seconds",
        0.25,
        operation="ask",
    )
    rendered = observability.render_prometheus_metrics()

    assert 'rag_requests_total{operation="ask"} 1' in rendered
    assert 'rag_duration_seconds_count{operation="ask"} 1' in rendered
    assert 'rag_duration_seconds_sum{operation="ask"} 0.25' in rendered


def test_rate_limit_returns_429_after_configured_limit(monkeypatch):
    redis_client = FakeRedis()
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_ask_requests", 1)
    monkeypatch.setattr(
        "app.core.rate_limit.get_redis_client",
        lambda: redis_client,
    )
    dependency = rate_limit("ask", "rate_limit_ask_requests")
    request = make_request()

    dependency(request)
    with pytest.raises(HTTPException) as exc_info:
        dependency(request)

    assert exc_info.value.status_code == 429
    assert "Retry-After" in exc_info.value.headers


def test_rate_limit_fails_closed_when_redis_is_unavailable(monkeypatch):
    class UnavailableRedis:
        def eval(self, *args):
            from redis.exceptions import RedisError

            raise RedisError("unavailable")

    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(
        "app.core.rate_limit.get_redis_client",
        UnavailableRedis,
    )
    with pytest.raises(HTTPException) as exc_info:
        rate_limit("login", "rate_limit_login_requests")(make_request())

    assert exc_info.value.status_code == 503


def test_readiness_requires_database_redis_and_worker(
    db_session,
    monkeypatch,
):
    from app.db.session import get_db

    class HealthyRedis:
        def ping(self):
            return True

    class HealthyInspect:
        def ping(self):
            return {"worker@local": {"ok": "pong"}}

    app.dependency_overrides[get_db] = lambda: db_session
    monkeypatch.setattr(
        "app.main.get_redis_client",
        lambda: HealthyRedis(),
    )
    monkeypatch.setattr(
        "app.main.celery_app",
        SimpleNamespace(
            control=SimpleNamespace(
                inspect=lambda timeout: HealthyInspect()
            )
        ),
    )
    try:
        with TestClient(app) as client:
            response = client.get("/health/ready")
        assert response.status_code == 200
        assert response.json()["checks"] == {
            "database": True,
            "redis": True,
            "worker": True,
        }
    finally:
        app.dependency_overrides.clear()
