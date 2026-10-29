from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.search import RetrievalFilters


class AskRequest(RetrievalFilters):
    question: str = Field(
        min_length=1,
        max_length=2000,
        description=(
            "Question to answer from completed documents in the authorized "
            "tenant; maximum 2,000 characters."
        ),
        examples=["How does parental leave eligibility work?"],
    )


class AnswerCitationResponse(BaseModel):
    source_id: int
    document_id: UUID
    chunk_id: UUID
    chunk_index: int


class AskResponse(BaseModel):
    question: str = Field(description="Original question.")
    answer: str = Field(
        description=(
            "Grounded answer. Source labels use the form [Source N]; when "
            "there is no relevant context, the response explains that "
            "instead of inventing an answer."
        )
    )
    citations: list[AnswerCitationResponse] = Field(
        description=(
            "Only source citations whose labels were validated in the answer."
        )
    )
