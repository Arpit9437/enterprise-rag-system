import logging
import time
from datetime import timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from groq import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.llm_dependencies import get_llm_provider
from app.core.observability import increment, observe
from app.core.rate_limit import rate_limit
from app.core.tenant_dependencies import require_tenant_member
from app.db.session import get_db
from app.models.conversation import Conversation, ConversationMessage
from app.models.users import User
from app.schemas.answer import AnswerCitationResponse, AskRequest
from app.schemas.conversation import (
    ConversationCreate,
    ConversationDetailResponse,
    ConversationListResponse,
    ConversationMessageCreate,
    ConversationMessageResponse,
    ConversationResponse,
    ConversationTurnResponse,
)
from app.services.answer_service import generate_answer
from app.services.citation_service import validate_answer_citations
from app.services.conversation_context import (
    bounded_history,
    reformulate_question,
)
from app.services.hybrid_search_service import hybrid_search
from app.services.llm_provider import LLMProvider

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/tenants/{tenant_id}/conversations",
    tags=["Conversations"],
)


def _owned_conversation_statement(
    tenant_id: UUID,
    conversation_id: UUID,
    owner_id: UUID,
):
    return select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.tenant_id == tenant_id,
        Conversation.owner_id == owner_id,
    )


@router.post(
    "",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a conversation",
    description=(
        "Create a conversation private to the authenticated user within the "
        "tenant. Other tenant members cannot read it."
    ),
)
def create_conversation(
    tenant_id: UUID,
    payload: ConversationCreate,
    owner: User = Depends(require_tenant_member),
    db: Session = Depends(get_db),
) -> Conversation:
    conversation = Conversation(
        tenant_id=tenant_id,
        owner_id=owner.id,
        title=payload.title or "New conversation",
    )
    db.add(conversation)
    try:
        db.commit()
        db.refresh(conversation)
    except SQLAlchemyError:
        db.rollback()
        logger.exception(
            "Could not create conversation tenant_id=%s owner_id=%s",
            tenant_id,
            owner.id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to create conversation",
        ) from None
    return conversation


@router.get(
    "",
    response_model=ConversationListResponse,
    summary="List my conversations",
    description=(
        "Returns a paginated list of conversations owned by the authenticated "
        "user in this tenant. limit is 1–100 (default 20); offset is "
        "non-negative. Results are ordered by most recently updated."
    ),
)
def list_conversations(
    tenant_id: UUID,
    owner: User = Depends(require_tenant_member),
    db: Session = Depends(get_db),
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
        description="Page size; minimum 1, maximum 100.",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="Number of conversations to skip; must be non-negative.",
    ),
) -> ConversationListResponse:
    filters = (
        Conversation.tenant_id == tenant_id,
        Conversation.owner_id == owner.id,
    )
    total = db.scalar(
        select(func.count()).select_from(Conversation).where(*filters)
    ) or 0
    conversations = db.scalars(
        select(Conversation)
        .where(*filters)
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return ConversationListResponse(
        conversations=list(conversations),
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{conversation_id}",
    response_model=ConversationDetailResponse,
    summary="Get my conversation",
    description=(
        "Return an owner-private conversation and a page of its messages. "
        "Message order is stable; message_limit is 1–100 (default 50) and "
        "message_offset is non-negative."
    ),
)
def get_conversation(
    tenant_id: UUID,
    conversation_id: UUID,
    owner: User = Depends(require_tenant_member),
    db: Session = Depends(get_db),
    message_limit: int = Query(
        default=50,
        ge=1,
        le=100,
        description="Message page size; minimum 1, maximum 100.",
    ),
    message_offset: int = Query(
        default=0,
        ge=0,
        description="Number of conversation messages to skip.",
    ),
) -> ConversationDetailResponse:
    conversation = db.scalar(
        _owned_conversation_statement(
            tenant_id,
            conversation_id,
            owner.id,
        )
    )
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )

    message_filters = (
        ConversationMessage.conversation_id == conversation_id,
    )
    message_total = db.scalar(
        select(func.count())
        .select_from(ConversationMessage)
        .where(*message_filters)
    ) or 0
    messages = db.scalars(
        select(ConversationMessage)
        .where(*message_filters)
        .order_by(ConversationMessage.message_index.asc())
        .offset(message_offset)
        .limit(message_limit)
    ).all()
    return ConversationDetailResponse(
        id=conversation.id,
        tenant_id=conversation.tenant_id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=messages,
        message_total=message_total,
        message_limit=message_limit,
        message_offset=message_offset,
    )


@router.post(
    "/{conversation_id}/messages",
    response_model=ConversationTurnResponse,
    summary="Send a conversation message",
    description=(
        "Retrieve tenant knowledge and create a grounded assistant turn. "
        "Only the conversation owner can send messages. Concurrent requests "
        "return 409; provider throttling returns 429 and provider errors "
        "return 502."
    ),
    dependencies=[
        Depends(
            rate_limit(
                "conversation_ask",
                "rate_limit_ask_requests",
            )
        )
    ],
)
def send_conversation_message(
    tenant_id: UUID,
    conversation_id: UUID,
    payload: ConversationMessageCreate,
    request: Request,
    owner: User = Depends(require_tenant_member),
    db: Session = Depends(get_db),
    provider: LLMProvider = Depends(get_llm_provider),
) -> ConversationTurnResponse:
    claim_id = uuid4()
    try:
        claim = db.execute(
            update(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.tenant_id == tenant_id,
                Conversation.owner_id == owner.id,
                or_(
                    Conversation.active_request_id.is_(None),
                    Conversation.active_request_started_at.is_(None),
                    Conversation.active_request_started_at
                    < func.now()
                    - timedelta(
                        seconds=settings.conversation_request_lease_seconds
                    ),
                ),
            )
            .values(
                active_request_id=claim_id,
                active_request_started_at=func.now(),
            )
        )
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception(
            "Could not claim conversation request tenant_id=%s "
            "conversation_id=%s",
            tenant_id,
            conversation_id,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Conversation service is temporarily unavailable.",
        ) from None

    if claim.rowcount != 1:
        exists = db.scalar(
            select(Conversation.id).where(
                Conversation.id == conversation_id,
                Conversation.tenant_id == tenant_id,
                Conversation.owner_id == owner.id,
            )
        )
        if exists is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found",
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A message is already being processed for this conversation",
        )

    generation_started: float | None = None
    rewrite_started: float | None = None
    rewrite_completed = False
    try:
        conversation = db.scalar(
            _owned_conversation_statement(
                tenant_id,
                conversation_id,
                owner.id,
            )
        )
        if conversation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found",
            )

        recent_messages = db.execute(
            select(ConversationMessage.role, ConversationMessage.content)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.message_index.desc())
            .limit(settings.conversation_history_max_messages)
        ).all()
        history = bounded_history(
            [(role, content) for role, content in reversed(recent_messages)],
            max_messages=settings.conversation_history_max_messages,
            max_chars=settings.conversation_history_max_chars,
        )
        db.commit()

        if history:
            rewrite_started = time.perf_counter()
        retrieval_question = reformulate_question(
            payload.question,
            history,
            provider,
        )
        if history:
            observe(
                "llm_generation_duration_seconds",
                time.perf_counter() - rewrite_started,
                operation="conversation_rewrite",
                outcome="success",
            )
        rewrite_completed = True
        retrieval_started = time.perf_counter()
        try:
            results = hybrid_search(
                db=db,
                tenant_id=tenant_id,
                query=retrieval_question,
                top_k=min(
                    settings.search_default_top_k,
                    settings.search_max_top_k,
                ),
                document_ids=payload.document_ids,
                content_type=payload.content_type,
                created_after=payload.created_after,
                created_before=payload.created_before,
            )
            db.commit()
        except (OSError, RuntimeError, ValueError, SQLAlchemyError):
            db.rollback()
            increment("rag_errors_total", operation="conversation")
            observe(
                "rag_retrieval_duration_seconds",
                time.perf_counter() - retrieval_started,
                operation="conversation",
                outcome="failure",
            )
            logger.exception(
                "Conversation retrieval failed request_id=%s "
                "tenant_id=%s conversation_id=%s",
                getattr(request.state, "request_id", "unknown"),
                tenant_id,
                conversation_id,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Search is temporarily unavailable.",
            ) from None
        observe(
            "rag_retrieval_duration_seconds",
            time.perf_counter() - retrieval_started,
            operation="conversation",
            outcome="success",
        )

        generation_started = time.perf_counter()
        generated = generate_answer(
            question=payload.question,
            results=results,
            provider=provider,
            max_context_chars=settings.max_context_chars,
            history=history,
        )
        observe(
            "llm_generation_duration_seconds",
            time.perf_counter() - generation_started,
            operation="conversation",
            outcome="success",
        )
        answer, citations = validate_answer_citations(
            generated.answer,
            generated.citations,
        )

        last_message_index = db.scalar(
            select(func.max(ConversationMessage.message_index)).where(
                ConversationMessage.conversation_id == conversation_id
            )
        )
        next_message_index = (last_message_index or 0) + 1
        result = db.execute(
            update(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.tenant_id == tenant_id,
                Conversation.owner_id == owner.id,
                Conversation.active_request_id == claim_id,
            )
            .values(
                active_request_id=None,
                active_request_started_at=None,
                title=(
                    payload.question[:200]
                    if not recent_messages
                    and conversation.title == "New conversation"
                    else conversation.title
                ),
                updated_at=func.now(),
            )
        )
        if result.rowcount != 1:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Conversation changed while the message was being processed",
            )

        citation_responses = [
            AnswerCitationResponse(
                source_id=citation.source_id,
                document_id=citation.document_id,
                chunk_id=citation.chunk_id,
                chunk_index=citation.chunk_index,
            )
            for citation in citations
        ]
        db.add_all(
            [
                ConversationMessage(
                    conversation_id=conversation_id,
                    role="user",
                    content=payload.question,
                    message_index=next_message_index,
                    citations=[],
                ),
                ConversationMessage(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=answer,
                    message_index=next_message_index + 1,
                    citations=[
                        citation.model_dump(mode="json")
                        for citation in citation_responses
                    ],
                ),
            ]
        )
        db.commit()
        return ConversationTurnResponse(
            conversation_id=conversation_id,
            question=payload.question,
            answer=answer,
            citations=citation_responses,
        )
    except RateLimitError:
        db.rollback()
        increment(
            "llm_errors_total",
            category="rate_limit",
            operation="conversation",
        )
        if generation_started is not None:
            observe(
                "llm_generation_duration_seconds",
                time.perf_counter() - generation_started,
                operation="conversation",
                outcome="failure",
            )
        if rewrite_started is not None and not rewrite_completed:
            observe(
                "llm_generation_duration_seconds",
                time.perf_counter() - rewrite_started,
                operation="conversation_rewrite",
                outcome="failure",
            )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
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
        db.rollback()
        increment(
            "llm_errors_total",
            category="provider",
            operation="conversation",
        )
        if generation_started is not None:
            observe(
                "llm_generation_duration_seconds",
                time.perf_counter() - generation_started,
                operation="conversation",
                outcome="failure",
            )
        if rewrite_started is not None and not rewrite_completed:
            observe(
                "llm_generation_duration_seconds",
                time.perf_counter() - rewrite_started,
                operation="conversation_rewrite",
                outcome="failure",
            )
        logger.exception(
            "Conversation answer generation failed request_id=%s "
            "tenant_id=%s conversation_id=%s",
            getattr(request.state, "request_id", "unknown"),
            tenant_id,
            conversation_id,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The answer service is temporarily unavailable.",
        ) from None
    except SQLAlchemyError:
        db.rollback()
        logger.exception(
            "Conversation persistence failed tenant_id=%s "
            "conversation_id=%s",
            tenant_id,
            conversation_id,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Conversation service is temporarily unavailable.",
        ) from None
    except Exception:
        db.rollback()
        increment("rag_errors_total", operation="conversation")
        logger.exception(
            "Conversation turn failed request_id=%s "
            "tenant_id=%s conversation_id=%s",
            getattr(request.state, "request_id", "unknown"),
            tenant_id,
            conversation_id,
        )
        raise
    finally:
        try:
            db.execute(
                update(Conversation)
                .where(
                    Conversation.id == conversation_id,
                    Conversation.tenant_id == tenant_id,
                    Conversation.owner_id == owner.id,
                    Conversation.active_request_id == claim_id,
                )
                .values(
                    active_request_id=None,
                    active_request_started_at=None,
                )
            )
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            logger.exception(
                "Could not release conversation request claim "
                "tenant_id=%s conversation_id=%s",
                tenant_id,
                conversation_id,
            )


@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete my conversation",
    description=(
        "Delete an owner-private conversation and its messages. A conversation "
        "owned by another user is returned as 404."
    ),
)
def delete_conversation(
    tenant_id: UUID,
    conversation_id: UUID,
    owner: User = Depends(require_tenant_member),
    db: Session = Depends(get_db),
) -> None:
    conversation = db.scalar(
        _owned_conversation_statement(
            tenant_id,
            conversation_id,
            owner.id,
        )
    )
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    db.delete(conversation)
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception(
            "Could not delete conversation tenant_id=%s conversation_id=%s",
            tenant_id,
            conversation_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to delete conversation",
        ) from None
