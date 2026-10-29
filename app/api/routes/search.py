import logging
import time

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.core.observability import observe
from app.core.rate_limit import rate_limit
from app.core.tenant_dependencies import require_tenant_member
from app.db.session import get_db
from app.models.users import User
from app.schemas.search import (
    SearchRequest,
    SearchResponse,
    SearchResultResponse,
)
from app.services.hybrid_search_service import hybrid_search

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/tenants/{tenant_id}/search",
    tags=["Search"],
)


@router.post(
    "",
    response_model=SearchResponse,
    summary="Hybrid search",
    description=(
        "Search completed documents in the authorized tenant using semantic "
        "and keyword retrieval with reciprocal-rank fusion. Supports up to "
        "20 document IDs, media type, and timezone-aware creation date "
        "filters. Empty matches return an empty results array."
    ),
    responses={
        401: {"description": "Missing, invalid, or expired token."},
        404: {"description": "Tenant not found or access denied."},
        422: {"description": "Invalid query, result limit, or filter."},
        429: {"description": "Search rate limit exceeded."},
        503: {"description": "Search is temporarily unavailable."},
    },
    dependencies=[
        Depends(rate_limit("search", "rate_limit_search_requests"))
    ],
)
def search_tenant_documents(
    tenant_id: UUID,
    payload: SearchRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_tenant_member),
) -> SearchResponse:
    """Return bounded hybrid-ranked chunks, scoped to tenant membership."""
    started_at = time.perf_counter()
    try:
        results = hybrid_search(
            db=db,
            tenant_id=tenant_id,
            query=payload.query,
            top_k=payload.top_k,
            document_ids=payload.document_ids,
            content_type=payload.content_type,
            created_after=payload.created_after,
            created_before=payload.created_before,
        )
    except (OSError, RuntimeError, ValueError, SQLAlchemyError):
        observe(
            "rag_retrieval_duration_seconds",
            time.perf_counter() - started_at,
            operation="search",
            outcome="failure",
        )
        logger.exception(
            "Search retrieval failed request_id=%s tenant_id=%s",
            request.state.request_id,
            tenant_id,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Search is temporarily unavailable.",
        ) from None
    observe(
        "rag_retrieval_duration_seconds",
        time.perf_counter() - started_at,
        operation="search",
        outcome="success",
    )

    return SearchResponse(
        query=payload.query,
        results=[
            SearchResultResponse(
                chunk_id=result.chunk_id,
                document_id=result.document_id,
                chunk_index=result.chunk_index,
                content=result.content,
                semantic_score=result.semantic_score,
                keyword_score=result.keyword_score,
                fused_score=result.fused_score,
            )
            for result in results
        ],
        total_results=len(results),
    )
