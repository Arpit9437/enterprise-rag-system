from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.services import hybrid_search_service as hybrid_service


@dataclass(frozen=True)
class FakeSemanticResult:
    chunk_id: UUID
    document_id: UUID
    chunk_index: int
    content: str
    cosine_distance: float
    similarity_score: float


@dataclass(frozen=True)
class FakeKeywordResult:
    chunk_id: UUID
    document_id: UUID
    chunk_index: int
    content: str
    keyword_score: float


@pytest.fixture
def tenant_id():
    return uuid4()


@pytest.fixture
def db():
    # The retrieval functions are mocked, so no real session is needed.
    return object()


def semantic_result(chunk_id, rank, content=None):
    return FakeSemanticResult(
        chunk_id=chunk_id,
        document_id=uuid4(),
        chunk_index=rank,
        content=content or f"Semantic content {rank}",
        cosine_distance=0.1 * rank,
        similarity_score=1.0 - (0.1 * rank),
    )


def keyword_result(chunk_id, rank, content=None):
    return FakeKeywordResult(
        chunk_id=chunk_id,
        document_id=uuid4(),
        chunk_index=rank,
        content=content or f"Keyword content {rank}",
        keyword_score=1.0 / (rank + 1),
    )


def test_hybrid_search_combines_both_retrieval_methods(
    monkeypatch, db, tenant_id
):
    shared_id = uuid4()
    semantic_only_id = uuid4()
    keyword_only_id = uuid4()

    semantic_results = [
        semantic_result(shared_id, 0, "Shared document chunk"),
        semantic_result(semantic_only_id, 1),
    ]

    keyword_results = [
        keyword_result(shared_id, 0, "Shared document chunk"),
        keyword_result(keyword_only_id, 1),
    ]

    monkeypatch.setattr(
        hybrid_service,
        "semantic_search",
        lambda **kwargs: semantic_results,
    )
    monkeypatch.setattr(
        hybrid_service,
        "keyword_search",
        lambda **kwargs: keyword_results,
    )

    results = hybrid_service.hybrid_search(
        db, tenant_id, "distributed systems", top_k=5
    )

    result_ids = [result.chunk_id for result in results]

    assert shared_id in result_ids
    assert semantic_only_id in result_ids
    assert keyword_only_id in result_ids

    shared_result = next(
        result for result in results if result.chunk_id == shared_id
    )
    assert shared_result.semantic_score is not None
    assert shared_result.keyword_score is not None
    assert shared_result.fused_score > 0


def test_hybrid_search_prioritizes_results_found_by_both_methods(
    monkeypatch, db, tenant_id
):
    shared_id = uuid4()
    semantic_only_id = uuid4()
    keyword_only_id = uuid4()

    monkeypatch.setattr(
        hybrid_service,
        "semantic_search",
        lambda **kwargs: [
            semantic_result(shared_id, 0),
            semantic_result(semantic_only_id, 1),
        ],
    )
    monkeypatch.setattr(
        hybrid_service,
        "keyword_search",
        lambda **kwargs: [
            keyword_result(shared_id, 0),
            keyword_result(keyword_only_id, 1),
        ],
    )

    results = hybrid_service.hybrid_search(
        db, tenant_id, "distributed systems", top_k=5
    )

    assert results[0].chunk_id == shared_id
    assert results[0].fused_score > results[1].fused_score
    assert results[0].fused_score > results[2].fused_score


def test_hybrid_search_preserves_missing_source_scores(
    monkeypatch, db, tenant_id
):
    semantic_id = uuid4()
    keyword_id = uuid4()

    monkeypatch.setattr(
        hybrid_service,
        "semantic_search",
        lambda **kwargs: [semantic_result(semantic_id, 0)],
    )
    monkeypatch.setattr(
        hybrid_service,
        "keyword_search",
        lambda **kwargs: [keyword_result(keyword_id, 0)],
    )

    results = hybrid_service.hybrid_search(
        db, tenant_id, "some query", top_k=5
    )

    by_id = {result.chunk_id: result for result in results}

    assert by_id[semantic_id].semantic_score is not None
    assert by_id[semantic_id].keyword_score is None

    assert by_id[keyword_id].semantic_score is None
    assert by_id[keyword_id].keyword_score is not None


def test_hybrid_search_respects_top_k(monkeypatch, db, tenant_id):
    ids = [uuid4() for _ in range(4)]

    monkeypatch.setattr(
        hybrid_service,
        "semantic_search",
        lambda **kwargs: [
            semantic_result(chunk_id, index)
            for index, chunk_id in enumerate(ids)
        ],
    )
    monkeypatch.setattr(
        hybrid_service,
        "keyword_search",
        lambda **kwargs: [],
    )

    results = hybrid_service.hybrid_search(
        db, tenant_id, "some query", top_k=2
    )

    assert len(results) == 2


def test_hybrid_search_applies_the_same_filters_to_both_retrievers(
    monkeypatch,
    db,
    tenant_id,
):
    document_ids = [uuid4()]
    created_after = datetime(2026, 1, 1, tzinfo=timezone.utc)
    calls = {}

    def fake_semantic(**kwargs):
        calls["semantic"] = kwargs
        return []

    def fake_keyword(**kwargs):
        calls["keyword"] = kwargs
        return []

    monkeypatch.setattr(hybrid_service, "semantic_search", fake_semantic)
    monkeypatch.setattr(hybrid_service, "keyword_search", fake_keyword)

    hybrid_service.hybrid_search(
        db=db,
        tenant_id=tenant_id,
        query="leave policy",
        document_ids=document_ids,
        content_type="text/plain",
        created_after=created_after,
    )

    assert calls["semantic"]["document_ids"] == document_ids
    assert calls["keyword"]["document_ids"] == document_ids
    assert calls["semantic"]["content_type"] == "text/plain"
    assert calls["keyword"]["content_type"] == "text/plain"
    assert calls["semantic"]["created_after"] == created_after
    assert calls["keyword"]["created_after"] == created_after


def test_hybrid_search_returns_empty_when_both_sources_are_empty(
    monkeypatch, db, tenant_id
):
    monkeypatch.setattr(
        hybrid_service, "semantic_search", lambda **kwargs: []
    )
    monkeypatch.setattr(
        hybrid_service, "keyword_search", lambda **kwargs: []
    )

    results = hybrid_service.hybrid_search(
        db, tenant_id, "no matching content", top_k=5
    )

    assert results == []


def test_hybrid_search_rejects_blank_query(db, tenant_id):
    with pytest.raises(ValueError, match="blank"):
        hybrid_service.hybrid_search(db, tenant_id, "   ")


@pytest.mark.parametrize("top_k", [0, -1, 21])
def test_hybrid_search_rejects_invalid_top_k(db, tenant_id, top_k):
    with pytest.raises(ValueError, match="top_k"):
        hybrid_service.hybrid_search(
            db, tenant_id, "some query", top_k=top_k
        )


@pytest.mark.parametrize("rank_constant", [0, -1])
def test_hybrid_search_rejects_invalid_rank_constant(
    db, tenant_id, rank_constant
):
    with pytest.raises(ValueError, match="rank_constant"):
        hybrid_service.hybrid_search(
            db,
            tenant_id,
            "some query",
            rank_constant=rank_constant,
        )