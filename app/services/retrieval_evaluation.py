import math
from collections.abc import Sequence


def retrieval_metrics(
    ranked_chunk_ids: Sequence[str],
    relevant_chunk_ids: set[str],
    *,
    k: int,
) -> dict[str, float | None]:
    if k < 1:
        raise ValueError("k must be positive")

    top_results = list(ranked_chunk_ids[:k])
    relevant_retrieved = sum(
        chunk_id in relevant_chunk_ids for chunk_id in top_results
    )
    recall = (
        relevant_retrieved / len(relevant_chunk_ids)
        if relevant_chunk_ids
        else None
    )

    reciprocal_rank = 0.0
    for rank, chunk_id in enumerate(top_results, start=1):
        if chunk_id in relevant_chunk_ids:
            reciprocal_rank = 1.0 / rank
            break

    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, chunk_id in enumerate(top_results, start=1)
        if chunk_id in relevant_chunk_ids
    )
    ideal_relevant_count = min(len(relevant_chunk_ids), k)
    ideal_dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank in range(1, ideal_relevant_count + 1)
    )

    return {
        f"recall@{k}": recall,
        f"precision@{k}": relevant_retrieved / k,
        f"reciprocal_rank@{k}": reciprocal_rank,
        f"ndcg@{k}": dcg / ideal_dcg if ideal_dcg else 0.0,
    }


def evaluate_retrieval_modes(
    rankings: dict[str, Sequence[str]],
    relevant_chunk_ids: set[str],
    *,
    k: int,
) -> dict[str, dict[str, float | None]]:
    return {
        mode: retrieval_metrics(
            ranking,
            relevant_chunk_ids,
            k=k,
        )
        for mode, ranking in rankings.items()
    }
