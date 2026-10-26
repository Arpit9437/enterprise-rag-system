import logging
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.tenant_dependencies import require_tenant_member
from app.db.session import get_db
from app.models.document import Document, DocumentStatus
from app.models.users import User
from app.schemas.document import (
    DocumentCreate,
    DocumentListResponse,
    DocumentResponse,
)
from app.services.document_storage import (
    delete_stored_file,
    store_uploaded_file,
)
from app.tasks.document_tasks import process_document_task

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/tenants/{tenant_id}/documents",
    tags=["Documents"],
)


@router.post(
    "",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_document(
    payload: DocumentCreate,
    tenant_id: UUID,
    current_user: Annotated[User, Depends(require_tenant_member)],
    db: Annotated[Session, Depends(get_db)],
) -> Document:
    document = Document(
        tenant_id=tenant_id,
        filename=payload.filename.strip(),
    )

    if not document.filename:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Filename cannot be blank",
        )

    db.add(document)

    try:
        db.flush()
        db.refresh(document)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )

    return document


@router.get("", response_model=DocumentListResponse)
def list_documents(
    tenant_id: UUID,
    current_user: Annotated[User, Depends(require_tenant_member)],
    db: Annotated[Session, Depends(get_db)],
) -> DocumentListResponse:
    documents = db.scalars(
        select(Document)
        .where(Document.tenant_id == tenant_id)
        .order_by(Document.created_at.desc())
    ).all()

    return DocumentListResponse(documents=list(documents))


@router.post(
    "/upload",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    tenant_id: UUID,
    current_user: Annotated[User, Depends(require_tenant_member)],
    db: Annotated[Session, Depends(get_db)],
    file: UploadFile = File(...),
) -> Document:
    original_filename = (file.filename or "").strip()

    if not original_filename:
        await file.close()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A filename is required",
        )

    storage_key = None

    # Phase 1: Store the file and persist document metadata.
    try:
        storage_key, content_type = await store_uploaded_file(file)

        document = Document(
            tenant_id=tenant_id,
            filename=original_filename,
            storage_key=storage_key,
            content_type=content_type,
            status=DocumentStatus.PENDING,
        )

        db.add(document)
        db.flush()
        db.refresh(document)
        db.commit()

    except HTTPException:
        db.rollback()

        if storage_key is not None:
            try:
                delete_stored_file(storage_key)
            except OSError:
                logger.exception(
                    "Failed to clean up uploaded file %s",
                    storage_key,
                )

        raise

    except SQLAlchemyError:
        db.rollback()

        if storage_key is not None:
            try:
                delete_stored_file(storage_key)
            except OSError:
                logger.exception(
                    "Failed to clean up uploaded file %s",
                    storage_key,
                )

        logger.exception("Failed to persist uploaded document metadata")

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to save document metadata",
        ) from None

    except Exception:
        db.rollback()

        if storage_key is not None:
            try:
                delete_stored_file(storage_key)
            except OSError:
                logger.exception(
                    "Failed to clean up uploaded file %s",
                    storage_key,
                )

        logger.exception("Document upload failed")

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Document upload failed",
        ) from None

    # Phase 2: Enqueue only after the document has been committed.
    # Do not delete the file or roll back the committed document if
    # Redis/Celery dispatch fails.
    try:
        process_document_task.delay(
            str(document.id),
            str(tenant_id),
        )
    except Exception:
        logger.exception(
            "Document %s was saved but could not be queued",
            document.id,
        )

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": (
                    "The document was saved, but background processing "
                    "could not be queued. The document remains pending."
                ),
                "document_id": str(document.id),
                "status": DocumentStatus.PENDING.value,
            },
        ) from None

    return document
