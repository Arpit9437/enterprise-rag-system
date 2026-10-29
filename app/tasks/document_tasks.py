import logging
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, update

# Register all ORM models before worker database operations.
import app.models

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.observability import increment, observe
from app.db.session import LocalSession
from app.models.document import Document, DocumentStatus
from app.services.document_pipeline import (
    prepare_document,
    persist_prepared_document,
)

logger = logging.getLogger(__name__)


@celery_app.task(
    name="documents.process_document",
    bind=True,
    max_retries=0,
)
def process_document_task(
    self,
    document_id: str,
    tenant_id: str,
) -> dict[str, str | int]:
    """Claim, prepare and atomically persist one tenant-scoped document."""
    document_uuid = UUID(document_id)
    tenant_uuid = UUID(tenant_id)
    claim_id = uuid4()
    claimed = False
    processing_started = time.perf_counter()

    try:
        with LocalSession() as db:
            claim = db.execute(
                update(Document)
                .where(
                    Document.id == document_uuid,
                    Document.tenant_id == tenant_uuid,
                    Document.storage_key.is_not(None),
                    Document.content_type.is_not(None),
                    or_(
                        Document.status == DocumentStatus.PENDING,
                        (
                            (Document.status == DocumentStatus.PROCESSING)
                            & (
                                or_(
                                    Document.processing_claimed_at.is_(None),
                                    Document.processing_claimed_at
                                    < func.now()
                                    - timedelta(
                                        seconds=(
                                            settings.document_processing_lease_seconds
                                        )
                                    ),
                                )
                            )
                        ),
                    ),
                )
                .values(
                    status=DocumentStatus.PROCESSING,
                    processing_claimed_at=func.now(),
                    processing_claim_id=claim_id,
                )
            )

            if claim.rowcount != 1:
                db.rollback()
                return {
                    "document_id": document_id,
                    "status": "skipped",
                    "chunks_created": 0,
                }

            claimed = True
            source = db.execute(
                select(
                    Document.storage_key,
                    Document.content_type,
                    Document.created_at,
                ).where(
                    Document.id == document_uuid,
                    Document.tenant_id == tenant_uuid,
                    Document.processing_claim_id == claim_id,
                )
            ).one()
            storage_key, content_type, created_at = source
            db.commit()

        pending_age_seconds = max(
            0.0,
            (datetime.now(timezone.utc) - created_at).total_seconds(),
        )
        observe("document_pending_age_seconds", pending_age_seconds)
        prepared = prepare_document(storage_key, content_type)

        persistence_started = time.perf_counter()
        with LocalSession() as db:
            try:
                chunk_count = persist_prepared_document(
                    db=db,
                    document_id=document_uuid,
                    tenant_id=tenant_uuid,
                    prepared=prepared,
                    claim_id=claim_id,
                )
                db.commit()
                observe(
                    "document_stage_duration_seconds",
                    time.perf_counter() - persistence_started,
                    stage="persistence",
                    outcome="success",
                )
            except Exception:
                db.rollback()
                observe(
                    "document_stage_duration_seconds",
                    time.perf_counter() - persistence_started,
                    stage="persistence",
                    outcome="failure",
                )
                raise

        increment("document_processing_total", status="completed")
        observe(
            "document_processing_duration_seconds",
            time.perf_counter() - processing_started,
            status="completed",
        )
        return {
            "document_id": document_id,
            "status": "completed",
            "chunks_created": chunk_count,
        }

    except Exception:
        increment("document_processing_total", status="failed")
        observe(
            "document_processing_duration_seconds",
            time.perf_counter() - processing_started,
            status="failed",
        )
        logger.exception(
            "Processing failed for document %s in tenant %s",
            document_id,
            tenant_id,
        )

        if claimed:
            try:
                with LocalSession() as db:
                    try:
                        db.execute(
                            update(Document)
                            .where(
                                Document.id == document_uuid,
                                Document.tenant_id == tenant_uuid,
                                Document.status == DocumentStatus.PROCESSING,
                                Document.processing_claim_id == claim_id,
                            )
                            .values(
                                status=DocumentStatus.FAILED,
                                processing_claimed_at=None,
                                processing_claim_id=None,
                            )
                        )
                        db.commit()
                    except Exception:
                        db.rollback()
                        raise
            except Exception:
                logger.exception(
                    "Could not update failure status for document %s "
                    "in tenant %s",
                    document_id,
                    tenant_id,
                )

        raise


@celery_app.task(name="documents.reconcile_stuck_processing")
def reconcile_stuck_processing_documents() -> int:
    """Mark expired processing claims failed so an admin can retry them."""
    with LocalSession() as db:
        try:
            result = db.execute(
                update(Document)
                .where(
                    Document.status == DocumentStatus.PROCESSING,
                    or_(
                        Document.processing_claimed_at.is_(None),
                        Document.processing_claimed_at
                        < func.now()
                        - timedelta(
                            seconds=(
                                settings.document_processing_lease_seconds
                            )
                        ),
                    ),
                )
                .values(
                    status=DocumentStatus.FAILED,
                    processing_claimed_at=None,
                    processing_claim_id=None,
                )
            )
            db.commit()
            return result.rowcount or 0
        except Exception:
            db.rollback()
            logger.exception("Could not reconcile stuck document claims")
            raise


celery_app.conf.beat_schedule = {
    **celery_app.conf.beat_schedule,
    "reconcile-stuck-document-processing-every-five-minutes": {
        "task": "documents.reconcile_stuck_processing",
        "schedule": 300.0,
    },
}
