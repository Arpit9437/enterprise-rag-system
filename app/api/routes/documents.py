import logging
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
    Query,
    status,
)
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.tenant_dependencies import (
    require_tenant_admin,
    require_tenant_member,
)
from app.core.rate_limit import rate_limit
from app.db.session import get_db
from app.models.document import Document, DocumentStatus
from app.models.document_chunk import DocumentChunk
from app.models.users import User
from app.schemas.document import (
    DocumentCreate,
    DocumentListResponse,
    DocumentResponse,
)
from app.services.document_storage import (
    delete_stored_file,
    sanitize_filename,
    store_uploaded_file,
)
from app.tasks.document_tasks import process_document_task

logger = logging.getLogger(__name__)


def _mark_dispatch_failure(db: Session, document: Document) -> None:
    try:
        result = db.execute(
            update(Document)
            .where(
                Document.id == document.id,
                Document.tenant_id == document.tenant_id,
                Document.status == DocumentStatus.PENDING,
            )
            .values(
                status=DocumentStatus.FAILED,
                processing_claimed_at=None,
                processing_claim_id=None,
            )
        )
        db.commit()
        if result.rowcount == 1:
            document.status = DocumentStatus.FAILED
    except SQLAlchemyError:
        db.rollback()
        logger.exception(
            "Could not mark document %s failed after dispatch error",
            document.id,
        )


router = APIRouter(
    prefix="/tenants/{tenant_id}/documents",
    tags=["Documents"],
)


@router.post(
    "",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create document metadata",
    description=(
        "Create a metadata-only pending document record. This does not "
        "upload a file or schedule processing; use the upload endpoint for "
        "searchable documents."
    ),
)
def create_document(
    payload: DocumentCreate,
    tenant_id: UUID,
    current_user: Annotated[User, Depends(require_tenant_member)],
    db: Annotated[Session, Depends(get_db)],
) -> Document:
    filename = sanitize_filename(payload.filename)
    if not filename:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Filename cannot be blank",
        )

    document = Document(
        tenant_id=tenant_id,
        filename=filename,
    )

    db.add(document)

    try:
        db.flush()
        db.commit()
        db.refresh(document)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )

    return document

@router.get(
    "",
    response_model=DocumentListResponse,
    summary="List tenant documents",
    description=(
        "Returns a tenant-scoped page ordered by creation time descending "
        "with document ID as a deterministic tie-break. limit is 1–100 "
        "(default 20); offset must be non-negative. total counts all matches."
    ),
    responses={
        401: {"description": "Missing, invalid, or expired token."},
        404: {"description": "Tenant not found or access denied."},
    },
)
def list_documents(
    tenant_id: UUID,
    current_user: Annotated[User, Depends(require_tenant_member)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
        description="Page size; minimum 1, maximum 100.",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="Number of tenant documents to skip; must be non-negative.",
    ),
) -> DocumentListResponse:
    base_query = select(Document).where(
        Document.tenant_id == tenant_id
    )

    total = db.scalar(
        select(func.count()).select_from(base_query.subquery())
    ) or 0

    documents = db.scalars(
        base_query
        .order_by(Document.created_at.desc(), Document.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()

    return DocumentListResponse(
        documents=list(documents),
        total=total,
        limit=limit,
        offset=offset,
    )

@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    summary="Get document status",
    description=(
        "Returns public document metadata and lifecycle status. Internal "
        "storage keys and processing details are not exposed. A missing "
        "document in this tenant returns 404."
    ),
    responses={
        401: {"description": "Missing, invalid, or expired token."},
        404: {"description": "Tenant or document not found."},
    },
)
def get_document(
    tenant_id: UUID,
    document_id: UUID,
    current_user: Annotated[User, Depends(require_tenant_member)],
    db: Annotated[Session, Depends(get_db)],
) -> Document:
    document = db.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.tenant_id == tenant_id,
        )
    )

    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    return document

@router.post(
    "/upload",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and process a document",
    description=(
        "Accepts PDF or UTF-8 plain text up to MAX_DOCUMENT_SIZE_BYTES "
        "(10 MiB by default). The API validates the uploaded bytes, stores "
        "the file, and queues background processing. The response is "
        "normally pending; poll document retrieval for pending, processing, "
        "completed, or failed status. A queue outage returns 503 and includes "
        "the saved document ID when available."
    ),
    responses={
        401: {"description": "Missing, invalid, or expired token."},
        404: {"description": "Tenant not found or access denied."},
        413: {"description": "Uploaded file exceeds the configured size limit."},
        415: {"description": "Only matching PDF and TXT file types are accepted."},
        422: {"description": "Invalid PDF signature or non-UTF-8 text file."},
        429: {"description": "Upload rate limit exceeded."},
        503: {"description": "File was saved, but background dispatch failed."},
    },
    dependencies=[
        Depends(rate_limit("upload", "rate_limit_upload_requests"))
    ],
)
async def upload_document(
    tenant_id: UUID,
    current_user: Annotated[User, Depends(require_tenant_member)],
    db: Annotated[Session, Depends(get_db)],
    file: UploadFile = File(
        ...,
        description=(
            "PDF or plain-text file. Enforced size limit defaults to 10 MiB; "
            "the server sanitizes the filename and generates its storage key."
        ),
    ),
) -> Document:
    original_filename = sanitize_filename(file.filename or "")

    if not original_filename:
        await file.close()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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
            except (OSError, ValueError):
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
            except (OSError, ValueError):
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
            except (OSError, ValueError):
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
        _mark_dispatch_failure(db, document)
        logger.exception(
            "Document %s was saved but could not be queued",
            document.id,
        )

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": (
                    "The document was saved, but background processing "
                    "could not be queued. The document is marked failed "
                    "and can be retried by a tenant administrator."
                ),
                "document_id": str(document.id),
                "status": document.status.value,
            },
        ) from None

    return document


@router.post(
    "/{document_id}/retry",
    response_model=DocumentResponse,
    summary="Retry a failed document",
    description=(
        "Administrator-only. Queues a failed uploaded document for "
        "processing again. Only failed documents with stored file metadata "
        "can be retried; concurrent or invalid retries return 409."
    ),
    responses={
        401: {"description": "Missing, invalid, or expired token."},
        403: {"description": "Tenant administrator privileges required."},
        404: {"description": "Tenant or document not found."},
        409: {"description": "Document is not eligible for retry."},
        429: {"description": "Retry rate limit exceeded."},
        503: {"description": "Processing task could not be dispatched."},
    },
    dependencies=[
        Depends(rate_limit("document_retry", "rate_limit_retry_requests"))
    ],
)
def retry_document_processing(
    tenant_id: UUID,
    document_id: UUID,
    admin: Annotated[User, Depends(require_tenant_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> Document:
    document = db.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.tenant_id == tenant_id,
        )
    )

    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    if not document.storage_key or not document.content_type:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document has no uploaded file to process",
        )

    try:
        result = db.execute(
            update(Document)
            .where(
                Document.id == document_id,
                Document.tenant_id == tenant_id,
                Document.status == DocumentStatus.FAILED,
                Document.storage_key.is_not(None),
                Document.content_type.is_not(None),
            )
            .values(status=DocumentStatus.PENDING)
        )
        if result.rowcount != 1:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only failed documents can be retried",
            )
        db.commit()
        db.refresh(document)
    except SQLAlchemyError:
        db.rollback()
        logger.exception(
            "Could not schedule retry for document %s",
            document_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to schedule document processing",
        ) from None

    try:
        process_document_task.delay(str(document_id), str(tenant_id))
    except Exception:
        _mark_dispatch_failure(db, document)
        logger.exception(
            "Document %s retry dispatch failed",
            document_id,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": (
                    "Background processing could not be queued. "
                    "The document can be retried by a tenant administrator."
                ),
                "document_id": str(document_id),
                "status": document.status.value,
            },
        ) from None

    return document


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document",
    description=(
        "Delete tenant-scoped document metadata and chunks, then remove its "
        "stored file. Members can delete documents in their tenant."
    ),
    responses={
        401: {"description": "Missing, invalid, or expired token."},
        404: {"description": "Tenant or document not found."},
    },
)
def delete_document(
    tenant_id: UUID,
    document_id: UUID,
    current_user: Annotated[User, Depends(require_tenant_member)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    document = db.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.tenant_id == tenant_id,
        )
    )

    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    storage_key = document.storage_key

    try:
        # Remove chunks and embeddings before deleting the document.
        db.query(DocumentChunk).filter(
            DocumentChunk.document_id == document_id,
            DocumentChunk.tenant_id == tenant_id,
        ).delete(synchronize_session=False)

        db.delete(document)
        db.commit()

    except SQLAlchemyError:
        db.rollback()
        logger.exception(
            "Failed to delete document %s from the database",
            document_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to delete document",
        ) from None

    # The database deletion is committed. Now clean up the physical file.
    if storage_key:
        try:
            delete_stored_file(storage_key)
        except (OSError, ValueError):
            logger.exception(
                "Document %s was deleted from the database, "
                "but file cleanup failed for storage key %s. "
                "Run the orphan-file cleanup procedure.",
                document_id,
                storage_key,
            )