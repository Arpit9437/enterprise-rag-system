from collections import defaultdict
from typing import TypeVar
from uuid import UUID

T = TypeVar("T")


def reciprocal_rank_fusion(
    result_lists: list[list[T]],
    *,
    rank_constant: int = 60,
    top_k: int = 5,
) -> list[T]:
    """Combine ranked result lists using Reciprocal Rank Fusion.

    Each result must expose a unique `chunk_id` attribute.
    Results in each input list must already be ranked best-first.
    """

    if rank_constant < 1:
        raise ValueError("rank_constant must be at least 1")

    if not 1 <= top_k <= 20:
        raise ValueError("top_k must be between 1 and 20")

    scores: dict[UUID, float] = defaultdict(float)
    items: dict[UUID, T] = {}

    for result_list in result_lists:
        seen_in_list: set[UUID] = set()

        for rank, item in enumerate(result_list, start=1):
            chunk_id = item.chunk_id

            # A chunk should contribute only once per retrieval list.
            if chunk_id in seen_in_list:
                continue

            seen_in_list.add(chunk_id)
            items[chunk_id] = item
            scores[chunk_id] += 1.0 / (rank_constant + rank)

    ranked_items = sorted(
        items.values(),
        key=lambda item: (-scores[item.chunk_id], str(item.chunk_id)),
    )

    return ranked_items[:top_k]
