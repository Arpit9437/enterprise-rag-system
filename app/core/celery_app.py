from celery import Celery

from app.core.config import settings


celery_app = Celery(
    "enterprise_rag",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.tasks.document_tasks",
        "app.tasks.conversation_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    worker_concurrency=settings.document_processing_worker_concurrency,
    task_soft_time_limit=settings.document_task_soft_time_limit_seconds,
    task_time_limit=settings.document_task_time_limit_seconds,
    worker_max_memory_per_child=(
        settings.document_worker_max_memory_per_child_kb
    ),
)
