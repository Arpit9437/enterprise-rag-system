from dataclasses import dataclass
import re
from uuid import UUID

from app.services.hybrid_search_service import HybridSearchResult


@dataclass(frozen=True)
class ContextCitation:
    source_id: int
    document_id: UUID
    chunk_id: UUID
    chunk_index: int


@dataclass(frozen=True)
class BuiltContext:
    context: str
    citations: list[ContextCitation]


def _content_ngrams(content: str) -> set[tuple[str, ...]]:
    words = re.findall(r"\w+", content.casefold())
    return {
        tuple(words[index : index + 4])
        for index in range(max(0, len(words) - 3))
    }


def build_context(
    results: list[HybridSearchResult],
    max_context_chars: int = 12000,
) -> BuiltContext:
    """
    Format retrieved chunks into bounded context for an LLM.

    Results are assumed to be ordered by retrieval relevance.
    Each source receives a citation ID for the generated answer.
    """

    if max_context_chars < 1:
        raise ValueError("max_context_chars must be positive")

    sections: list[str] = []
    citations: list[ContextCitation] = []
    seen_chunk_ids: set[UUID] = set()
    seen_content_ngrams: list[set[tuple[str, ...]]] = []
    used_chars = 0

    for result in results:
        if result.chunk_id in seen_chunk_ids:
            continue

        content = result.content.strip()
        if not content:
            continue

        content_ngrams = _content_ngrams(content)
        if len(content_ngrams) >= 16 and any(
            len(content_ngrams & previous_ngrams)
            / min(len(content_ngrams), len(previous_ngrams))
            >= 0.9
            for previous_ngrams in seen_content_ngrams
        ):
            continue

        citation = ContextCitation(
            source_id=len(citations) + 1,
            document_id=result.document_id,
            chunk_id=result.chunk_id,
            chunk_index=result.chunk_index,
        )

        header = (
            f"[Source {citation.source_id} | "
            f"document_id={citation.document_id} | "
            f"chunk_index={citation.chunk_index}]\n"
        )
        section = f"{header}{content}"

        separator = "\n\n" if sections else ""
        available_chars = max_context_chars - used_chars - len(separator)

        if available_chars <= len(header):
            break

        if len(section) > available_chars:
            # Keep the source header and truncate the content if necessary.
            truncated_content = content[: available_chars - len(header)]
            section = f"{header}{truncated_content}"

        sections.append(section)
        citations.append(citation)
        seen_chunk_ids.add(result.chunk_id)
        if len(content_ngrams) >= 16:
            seen_content_ngrams.append(content_ngrams)

        used_chars += len(separator) + len(section)

        if used_chars >= max_context_chars:
            break

    return BuiltContext(
        context="\n\n".join(sections),
        citations=citations,
    )
