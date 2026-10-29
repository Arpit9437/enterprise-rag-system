import re

from app.services.context_service import ContextCitation


SOURCE_PATTERN = re.compile(r"\[Source\s+(\d+)\]")


def validate_answer_citations(
    answer: str,
    citations: list[ContextCitation],
) -> tuple[str, list[ContextCitation]]:
    """
    Keep only citation labels that map to retrieved context sources.

    This validates source references, not whether a source semantically
    supports the associated claim.
    """
    available_sources = {
        citation.source_id: citation
        for citation in citations
    }

    cited_source_ids = {
        int(match)
        for match in SOURCE_PATTERN.findall(answer)
    }

    valid_source_ids = cited_source_ids & set(available_sources)

    cleaned_answer = SOURCE_PATTERN.sub(
        lambda match: (
            match.group(0)
            if int(match.group(1)) in valid_source_ids
            else ""
        ),
        answer,
    )

    valid_citations = [
        available_sources[source_id]
        for source_id in sorted(valid_source_ids)
    ]

    return cleaned_answer.strip(), valid_citations
