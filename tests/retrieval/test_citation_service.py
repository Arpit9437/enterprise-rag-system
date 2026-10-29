from uuid import uuid4

from app.services.citation_service import validate_answer_citations
from app.services.context_service import ContextCitation


def make_citation(source_id: int) -> ContextCitation:
    return ContextCitation(
        source_id=source_id,
        document_id=uuid4(),
        chunk_id=uuid4(),
        chunk_index=source_id - 1,
    )


def test_keeps_valid_citation_labels():
    citation = make_citation(1)

    answer, citations = validate_answer_citations(
        "The manuscript uses dual-path extraction [Source 1].",
        [citation],
    )

    assert answer == (
        "The manuscript uses dual-path extraction [Source 1]."
    )
    assert citations == [citation]


def test_removes_unknown_source_labels():
    citation = make_citation(1)

    answer, citations = validate_answer_citations(
        "The result is reliable [Source 99].",
        [citation],
    )

    assert "[Source 99]" not in answer
    assert citations == []


def test_keeps_valid_and_removes_invalid_labels():
    citation_1 = make_citation(1)
    citation_2 = make_citation(2)

    answer, citations = validate_answer_citations(
        "First claim [Source 1]. Second claim [Source 99].",
        [citation_1, citation_2],
    )

    assert "[Source 1]" in answer
    assert "[Source 99]" not in answer
    assert citations == [citation_1]


def test_returns_only_citations_referenced_by_answer():
    citation_1 = make_citation(1)
    citation_2 = make_citation(2)

    answer, citations = validate_answer_citations(
        "The result is supported [Source 2].",
        [citation_1, citation_2],
    )

    assert answer == "The result is supported [Source 2]."
    assert citations == [citation_2]


def test_repeated_source_labels_return_one_citation():
    citation = make_citation(1)

    answer, citations = validate_answer_citations(
        "Claim A [Source 1]. Claim B [Source 1].",
        [citation],
    )

    assert answer.count("[Source 1]") == 2
    assert citations == [citation]


def test_answer_without_citations_returns_empty_list():
    citation = make_citation(1)

    answer, citations = validate_answer_citations(
        "The document does not specify the answer.",
        [citation],
    )

    assert answer == "The document does not specify the answer."
    assert citations == []


def test_empty_answer_and_citations():
    answer, citations = validate_answer_citations("", [])

    assert answer == ""
    assert citations == []
