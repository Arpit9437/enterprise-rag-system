from functools import lru_cache
import math

from sentence_transformers import SentenceTransformer

from app.core.config import settings
from app.models.document_chunk import EMBEDDING_DIMENSIONS


MODEL_NAME = settings.embedding_model


@lru_cache(maxsize=1)
def get_embedding_model() -> SentenceTransformer:
    """Load the embedding model once per Python process."""
    return SentenceTransformer(MODEL_NAME)


def generate_embeddings(texts: list[str]) -> list[list[float]]:
    """Generate normalized embeddings for a batch of text strings."""
    if not texts:
        return []

    if any(not text.strip() for text in texts):
        raise ValueError("Embedding input cannot contain blank text")

    model = get_embedding_model()

    embeddings: list[list[float]] = []

    for batch_start in range(0, len(texts), settings.embedding_batch_size):
        batch = texts[
            batch_start : batch_start + settings.embedding_batch_size
        ]
        vectors = model.encode(
            batch,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )

        if len(vectors) != len(batch):
            raise ValueError(
                "Embedding model returned an unexpected result count"
            )

        for vector in vectors:
            embedding = [float(value) for value in vector]

            if len(embedding) != EMBEDDING_DIMENSIONS:
                raise ValueError(
                    f"Expected {EMBEDDING_DIMENSIONS} dimensions, "
                    f"received {len(embedding)}"
                )

            if not all(math.isfinite(value) for value in embedding):
                raise ValueError("Embedding contains a non-finite value")

            embeddings.append(embedding)

    return embeddings
