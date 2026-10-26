from uuid import UUID

from sqlalchemy import select

# Import all models so their tables are registered in Base.metadata
# before the worker performs ORM operations.
import app.models

from app.core.celery_app import celery_app
from app.db.session import LocalSession
from app.models.document import Document, DocumentStatus
from app.services.document_pipeline import process_document


@celery_app.task(name="documents.process_document", bind=True, max_retries=0)
def process_document_task(
    self,
    document_id: str,
    tenant_id: str,
) -> dict[str, str | int]:
    """Process a document using a worker-owned database session."""
    document_uuid = UUID(document_id)
    tenant_uuid = UUID(tenant_id)

    try:
        with LocalSession() as db:
            chunk_count = process_document(
                db=db,
                document_id=document_uuid,
                tenant_id=tenant_uuid,
            )
            db.commit()

        return {
            "document_id": document_id,
            "status": "completed",
            "chunks_created": chunk_count,
        }

    except Exception:
        # The processing session has exited; its uncommitted changes
        # are rolled back when the session closes.
        with LocalSession() as db:
            try:
                document = db.scalar(
                    select(Document).where(
                        Document.id == document_uuid,
                        Document.tenant_id == tenant_uuid,
                    )
                )

                if document is not None:
                    document.status = DocumentStatus.FAILED
                    db.commit()

            except Exception:
                db.rollback()

        raise
