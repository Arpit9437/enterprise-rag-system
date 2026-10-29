import logging
import time
from uuid import UUID, uuid4

from celery.exceptions import CeleryError
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from kombu.exceptions import OperationalError
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.routes.ask import router as ask_router
from app.api.routes.auth import router as auth_router
from app.api.routes.conversations import router as conversations_router
from app.api.routes.documents import router as documents_router
from app.api.routes.search import router as search_router
from app.api.routes.tenants import router as tenants_router
from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.observability import (
    increment,
    log_event,
    observe,
    render_prometheus_metrics,
)
from app.core.redis_client import get_redis_client
from app.db.session import get_db

logger = logging.getLogger(__name__)
app = FastAPI(
    title=settings.application_name,
    version="1.0.0",
    description=(
        "Tenant-scoped document ingestion and retrieval-augmented "
        "question answering. Most tenant routes require a bearer access "
        "token and an active membership in the path tenant. Administrators "
        "are required for membership management and failed-document retries. "
        "Upload supports PDF and plain-text files; document processing is "
        "asynchronous. Search returns ranked source passages, while ask "
        "returns a grounded answer with validated citations."
    ),
    openapi_tags=[
        {
            "name": "Authentication",
            "description": (
                "Register, sign in, inspect the current account, and delete "
                "the authenticated account."
            ),
        },
        {
            "name": "Tenants",
            "description": (
                "Create workspaces, list memberships, and manage members. "
                "Tenant membership and administrator permissions are "
                "validated from database records."
            ),
        },
        {
            "name": "Documents",
            "description": (
                "Upload and manage tenant documents. Uploaded files move "
                "through pending, processing, completed, or failed states."
            ),
        },
        {
            "name": "Search",
            "description": (
                "Tenant-scoped hybrid semantic and keyword retrieval over "
                "completed documents."
            ),
        },
        {
            "name": "RAG",
            "description": (
                "Generate answers grounded in tenant search results. The "
                "response includes only validated citations used by the "
                "answer."
            ),
        },
        {
            "name": "Conversations",
            "description": (
                "Owner-private tenant conversations with bounded message "
                "history and pagination."
            ),
        },
    ],
)
if settings.cors_allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )

app.include_router(auth_router)
app.include_router(tenants_router)
app.include_router(documents_router)
app.include_router(search_router)
app.include_router(ask_router)
app.include_router(conversations_router)


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    try:
        request_id = str(UUID(request.headers.get("X-Request-ID", "")))
    except (ValueError, TypeError, AttributeError):
        request_id = str(uuid4())

    request.state.request_id = request_id
    started_at = time.perf_counter()
    log_event(
        logging.INFO,
        "request_started",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
    )
    try:
        response = await call_next(request)
    except Exception:
        duration = time.perf_counter() - started_at
        increment("http_requests_total", method=request.method, status="error")
        observe("http_request_duration_seconds", duration, method=request.method)
        log_event(
            logging.ERROR,
            "request_failed",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            duration_ms=round(duration * 1000, 2),
        )
        logger.exception(
            "request exception request_id=%s",
            request_id,
        )
        raise

    duration = time.perf_counter() - started_at
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    increment(
        "http_requests_total",
        method=request.method,
        status=str(response.status_code),
    )
    observe("http_request_duration_seconds", duration, method=request.method)
    log_event(
        logging.INFO,
        "request_completed",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=round(duration * 1000, 2),
    )
    return response


@app.get("/health")
def health_check():
    return {"mssg": "FastAPI application running."}


@app.get("/health/live")
def liveness_check():
    return {"status": "alive"}


@app.get("/health/ready")
def readiness_check(db: Session = Depends(get_db)):
    checks = {"database": False, "redis": False, "worker": False}
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = True
    except SQLAlchemyError:
        logger.exception("Readiness database check failed")

    try:
        checks["redis"] = bool(get_redis_client().ping())
    except RedisError:
        logger.exception("Readiness Redis check failed")

    try:
        worker_responses = celery_app.control.inspect(timeout=1.0).ping()
        checks["worker"] = bool(worker_responses)
    except (CeleryError, OSError, OperationalError, TimeoutError):
        logger.exception("Readiness Celery worker check failed")

    ready = all(checks.values())
    if not ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "not_ready", "checks": checks},
        )
    return {"status": "ready", "checks": checks}


@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    try:
        return render_prometheus_metrics()
    except RedisError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Metrics are temporarily unavailable.",
        ) from None


@app.get("/db-check")
def check_db(session: Session = Depends(get_db)):
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.exception("Database check failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is temporarily unavailable.",
        ) from None
    return {"success": True, "message": "Database connection successful"}
