import json
from pathlib import Path

import pytest

from app.services.retrieval_evaluation import (
    evaluate_retrieval_modes,
    retrieval_metrics,
)


@pytest.fixture
def evaluation_set():
    path = (
        Path(__file__).parents[1]
        / "data"
        / "retrieval_evaluation.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_versioned_evaluation_set_covers_required_question_types(
    evaluation_set,
):
    assert evaluation_set["version"] == 1
    query_ids = {item["id"] for item in evaluation_set["queries"]}

    assert query_ids == {
        "exact_product_identifier",
        "semantic_leave_carryover",
        "multiple_leave_chunks",
        "absent_answer",
    }
    assert any(
        len(item["relevant_chunk_ids"]) > 1
        for item in evaluation_set["queries"]
    )
    assert any(
        not item["relevant_chunk_ids"]
        for item in evaluation_set["queries"]
    )
    corpus_ids = {
        item["chunk_id"] for item in evaluation_set["corpus"]
    }
    assert all(
        set(query["relevant_chunk_ids"]).issubset(corpus_ids)
        for query in evaluation_set["queries"]
    )


def test_retrieval_metrics_compare_semantic_keyword_and_hybrid_rankings():
    result = evaluate_retrieval_modes(
        {
            "semantic": ["leave-carryover", "leave-eligibility", "other"],
            "keyword": ["other", "leave-carryover", "leave-eligibility"],
            "hybrid": ["leave-carryover", "leave-eligibility", "other"],
        },
        {"leave-carryover", "leave-eligibility"},
        k=3,
    )

    assert result["semantic"]["recall@3"] == 1.0
    assert result["keyword"]["recall@3"] == 1.0
    assert result["hybrid"]["ndcg@3"] == 1.0
    assert result["semantic"]["reciprocal_rank@3"] == 1.0


def test_retrieval_metrics_treat_absent_answers_explicitly():
    result = retrieval_metrics(
        ["candidate-a", "candidate-b"],
        set(),
        k=2,
    )

    assert result["recall@2"] is None
    assert result["precision@2"] == 0.0
    assert result["reciprocal_rank@2"] == 0.0
    assert result["ndcg@2"] == 0.0


def test_retrieval_metrics_reject_nonpositive_k():
    with pytest.raises(ValueError, match="positive"):
        retrieval_metrics([], set(), k=0)
