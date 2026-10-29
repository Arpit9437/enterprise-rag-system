from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.core.config import settings
from sqlalchemy.orm import Session

from app.services.keyword_search_service import (
    keyword_search,
)
from app.services.ranking_service import (
    reciprocal_rank_fusion,
)
from app.services.retrieval_service import (
    semantic_search,
)


@dataclass(frozen=True)
class HybridSearchResult:
    chunk_id: UUID
    document_id: UUID
    chunk_index: int
    content: str
    semantic_score: float | None
    keyword_score: float | None
    fused_score: float


def hybrid_search(
    db: Session,
    tenant_id: UUID,
    query: str,
    top_k: int = min(
        settings.search_default_top_k,
        settings.search_max_top_k,
    ),
    rank_constant: int = settings.search_rank_constant,
    document_ids: list[UUID] | None = None,
    content_type: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
) -> list[HybridSearchResult]:
    """
    Combine semantic and keyword retrieval using Reciprocal Rank Fusion.
    """

    if not query or not query.strip():
        raise ValueError("Search query cannot be blank")

    if not 1 <= top_k <= settings.search_max_top_k:
        raise ValueError(
            f"top_k must be between 1 and {settings.search_max_top_k}"
        )

    if rank_constant < 1:
        raise ValueError("rank_constant must be at least 1")

    semantic_results = semantic_search(
        db=db,
        tenant_id=tenant_id,
        query=query,
        top_k=top_k,
        document_ids=document_ids,
        content_type=content_type,
        created_after=created_after,
        created_before=created_before,
    )

    keyword_results = keyword_search(
        db=db,
        tenant_id=tenant_id,
        query=query,
        top_k=top_k,
        document_ids=document_ids,
        content_type=content_type,
        created_after=created_after,
        created_before=created_before,
    )

    # Use RRF to determine the final ranking.
    ranked_results = reciprocal_rank_fusion(
        [semantic_results, keyword_results],
        rank_constant=rank_constant,
        top_k=top_k,
    )

    # Preserve the results from both retrieval methods so their
    # individual scores remain available in the final response.
    semantic_by_id = {
        result.chunk_id: result for result in semantic_results
    }
    keyword_by_id = {
        result.chunk_id: result for result in keyword_results
    }

    # Calculate each chunk's RRF score.
    fused_scores: dict[UUID, float] = {}

    for result_list in (semantic_results, keyword_results):
        seen: set[UUID] = set()

        for rank, result in enumerate(result_list, start=1):
            if result.chunk_id in seen:
                continue

            seen.add(result.chunk_id)

            fused_scores[result.chunk_id] = (
                fused_scores.get(result.chunk_id, 0.0)
                + 1.0 / (rank_constant + rank)
            )

    combined_results: list[HybridSearchResult] = []

    for ranked_result in ranked_results:
        chunk_id = ranked_result.chunk_id
        semantic = semantic_by_id.get(chunk_id)
        keyword = keyword_by_id.get(chunk_id)

        # Use whichever retrieval result exists for shared chunk metadata.
        source = semantic if semantic is not None else keyword

        combined_results.append(
            HybridSearchResult(
                chunk_id=chunk_id,
                document_id=source.document_id,
                chunk_index=source.chunk_index,
                content=source.content,
                semantic_score=(
                    semantic.similarity_score
                    if semantic is not None
                    else None
                ),
                keyword_score=(
                    keyword.keyword_score
                    if keyword is not None
                    else None
                ),
                fused_score=fused_scores[chunk_id],
            )
        )

    return combined_results