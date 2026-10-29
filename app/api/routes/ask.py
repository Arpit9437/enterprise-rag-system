import logging
import time
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from groq import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.core.config import settings
from app.core.llm_dependencies import get_llm_provider
from app.core.observability import increment, observe
from app.core.rate_limit import rate_limit
from app.core.tenant_dependencies import require_tenant_member
from app.db.session import get_db
from app.models.users import User
from app.schemas.answer import (
    AnswerCitationResponse,
    AskRequest,
    AskResponse,
)
from app.services.answer_service import generate_answer
from app.services.hybrid_search_service import hybrid_search
from app.services.citation_service import validate_answer_citations
from app.services.llm_provider import LLMProvider

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/tenants/{tenant_id}/ask",
    tags=["RAG"],
)


@router.post(
    "",
    response_model=AskResponse,
    summary="Ask a question",
    description=(
        "Retrieve relevant completed tenant documents and generate a "
        "grounded answer. Only validated source labels appear in citations. "
        "When no relevant context exists, returns a deliberate no-context "
        "answer without calling the LLM. Retrieval outages return 503; "
        "provider throttling returns 429; provider failures return 502."
    ),
    responses={
        401: {"description": "Missing, invalid, or expired token."},
        404: {"description": "Tenant not found or access denied."},
        422: {"description": "Invalid question or retrieval filter."},
        429: {"description": "Ask rate limit or LLM-provider throttling."},
        502: {"description": "Answer provider is temporarily unavailable."},
        503: {"description": "Search is temporarily unavailable."},
    },
    dependencies=[Depends(rate_limit("ask", "rate_limit_ask_requests"))],
)
def ask_tenant_documents(
    tenant_id: UUID,
    payload: AskRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_tenant_member),
    provider: LLMProvider = Depends(get_llm_provider),
) -> AskResponse:
    retrieval_started = time.perf_counter()
    try:
        results = hybrid_search(
            db=db,
            tenant_id=tenant_id,
            query=payload.question,
            top_k=min(
                settings.search_default_top_k,
                settings.search_max_top_k,
            ),
            document_ids=payload.document_ids,
            content_type=payload.content_type,
            created_after=payload.created_after,
            created_before=payload.created_before,
        )
    except (OSError, RuntimeError, ValueError, SQLAlchemyError):
        increment("rag_errors_total", operation="ask")
        observe(
            "rag_retrieval_duration_seconds",
            time.perf_counter() - retrieval_started,
            operation="ask",
            outcome="failure",
        )
        logger.exception(
            "RAG retrieval failed request_id=%s tenant_id=%s",
            getattr(request.state, "request_id", "unknown"),
            tenant_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Search is temporarily unavailable.",
        ) from None

    observe(
        "rag_retrieval_duration_seconds",
        time.perf_counter() - retrieval_started,
        operation="ask",
        outcome="success",
    )

    generation_started = time.perf_counter()
    try:
        generated = generate_answer(
            question=payload.question,
            results=results,
            provider=provider,
            max_context_chars=settings.max_context_chars,
        )
    except RateLimitError:
        increment("llm_errors_total", category="rate_limit", operation="ask")
        observe(
            "llm_generation_duration_seconds",
            time.perf_counter() - generation_started,
            operation="ask",
            outcome="failure",
        )
        logger.warning(
            "LLM provider rate limit reached request_id=%s tenant_id=%s",
            getattr(request.state, "request_id", "unknown"),
            tenant_id,
        )
        raise HTTPException(
            status_code=429,
            detail="The answer service is busy. Please try again later.",
            headers={"Retry-After": "5"},
        ) from None

    except (
        APITimeoutError,
        APIConnectionError,
        APIStatusError,
        RuntimeError,
        ValueError,
    ):
        increment("llm_errors_total", category="provider", operation="ask")
        observe(
            "llm_generation_duration_seconds",
            time.perf_counter() - generation_started,
            operation="ask",
            outcome="failure",
        )
        logger.exception(
            "RAG answer generation failed request_id=%s tenant_id=%s",
            getattr(request.state, "request_id", "unknown"),
            tenant_id,
        )
        raise HTTPException(
            status_code=502,
            detail="The answer service is temporarily unavailable.",
        ) from None

    observe(
        "llm_generation_duration_seconds",
        time.perf_counter() - generation_started,
        operation="ask",
        outcome="success",
    )
    answer, citations = validate_answer_citations(
        generated.answer,
        generated.citations,
    )

    return AskResponse(
        question=payload.question,
        answer=answer,
        citations=[
            AnswerCitationResponse(
                source_id=citation.source_id,
                document_id=citation.document_id,
                chunk_id=citation.chunk_id,
                chunk_index=citation.chunk_index,
            )
            for citation in citations
        ],
    )