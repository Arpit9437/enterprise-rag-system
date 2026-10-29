import pytest
from pydantic import ValidationError

from app.core.celery_app import celery_app
from app.core.config import Settings


def make_settings(**overrides):
    values = {
        "database_url": "postgresql+psycopg://user:pass@localhost/db",
        "postgres_password": "local-password",
        "jwt_secret_key": "test-secret-with-more-than-thirty-two-characters",
    }
    values.update(overrides)
    return Settings(**values)


def test_settings_accept_explicit_cors_origins():
    settings = make_settings(
        cors_allowed_origins=["https://app.example.com/"]
    )

    assert settings.cors_allowed_origins == [
        "https://app.example.com"
    ]


@pytest.mark.parametrize(
    "origins",
    [
        ["*"],
        ["https://example.com/path"],
        ["javascript:alert(1)"],
        ["https://user:password@example.com"],
    ],
)
def test_settings_reject_unsafe_cors_origins(origins):
    with pytest.raises(ValidationError):
        make_settings(cors_allowed_origins=origins)


@pytest.mark.parametrize(
    "overrides",
    [
        {"jwt_secret_key": "too-short"},
        {"jwt_algorithm": "none"},
        {"access_token_expire_minutes": 0},
    ],
)
def test_settings_reject_weak_jwt_configuration(overrides):
    with pytest.raises(ValidationError):
        make_settings(**overrides)


def test_settings_expose_database_pool_and_worker_limits():
    settings = make_settings(
        database_pool_size=3,
        database_max_overflow=2,
        database_pool_timeout_seconds=15,
        document_processing_worker_concurrency=4,
        document_task_soft_time_limit_seconds=120,
        document_task_time_limit_seconds=180,
        document_worker_max_memory_per_child_kb=512_000,
    )

    assert settings.database_pool_size == 3
    assert settings.database_max_overflow == 2
    assert settings.database_pool_timeout_seconds == 15
    assert settings.document_processing_worker_concurrency == 4
    assert settings.document_task_soft_time_limit_seconds == 120
    assert settings.document_task_time_limit_seconds == 180
    assert settings.document_worker_max_memory_per_child_kb == 512_000


@pytest.mark.parametrize(
    "overrides",
    [
        {"database_pool_size": 0},
        {"database_max_overflow": -1},
        {"database_pool_timeout_seconds": 0},
        {"document_processing_worker_concurrency": 0},
        {
            "document_task_soft_time_limit_seconds": 1800,
            "document_task_time_limit_seconds": 1800,
        },
        {"document_worker_max_memory_per_child_kb": 1},
    ],
)
def test_settings_reject_invalid_resource_limits(overrides):
    with pytest.raises(ValidationError):
        make_settings(**overrides)


def test_celery_app_uses_configured_worker_resource_limits():
    from app.core.config import settings

    assert (
        celery_app.conf.worker_concurrency
        == settings.document_processing_worker_concurrency
    )
    assert (
        celery_app.conf.task_soft_time_limit
        == settings.document_task_soft_time_limit_seconds
    )
    assert (
        celery_app.conf.task_time_limit
        == settings.document_task_time_limit_seconds
    )
    assert (
        celery_app.conf.worker_max_memory_per_child
        == settings.document_worker_max_memory_per_child_kb
    )
