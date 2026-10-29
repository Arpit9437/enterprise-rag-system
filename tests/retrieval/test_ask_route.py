from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import ask as ask_route
from app.core.dependencies import get_current_user
from app.core.llm_dependencies import get_llm_provider
from app.core.tenant_dependencies import require_tenant_member
from app.db.session import get_db
from app.services.answer_service import GeneratedAnswer
from app.services.context_service import ContextCitation
from app.services.hybrid_search_service import HybridSearchResult


def create_test_client(provider):
    app = FastAPI()
    app.include_router(ask_route.router)

    app.dependency_overrides[get_current_user] = lambda: object()
    app.dependency_overrides[require_tenant_member] = lambda: None
    app.dependency_overrides[get_db] = lambda: object()
    app.dependency_overrides[get_llm_provider] = lambda: provider

    return app, TestClient(app)


def test_ask_endpoint_returns_answer_and_citation(monkeypatch):
    provider = object()
    app, client = create_test_client(provider)

    tenant_id = uuid4()
    document_id = uuid4()
    chunk_id = uuid4()

    result = HybridSearchResult(
        chunk_id=chunk_id,
        document_id=document_id,
        chunk_index=0,
        content="The manuscript title is Example Title.",
        semantic_score=0.9,
        keyword_score=0.5,
        fused_score=0.03,
    )

    def fake_search(**kwargs):
        assert kwargs["tenant_id"] == tenant_id
        assert kwargs["query"] == "What is the title?"
        return [result]

    def fake_generate_answer(**kwargs):
        assert kwargs["provider"] is provider
        return GeneratedAnswer(
            answer="The title is Example Title [Source 1]. [Source 99]",
            citations=[
                ContextCitation(
                    source_id=1,
                    document_id=document_id,
                    chunk_id=chunk_id,
                    chunk_index=0,
                )
            ],
        )

    monkeypatch.setattr(ask_route, "hybrid_search", fake_search)
    monkeypatch.setattr(
        ask_route, "generate_answer", fake_generate_answer
    )

    try:
        response = client.post(
            f"/tenants/{tenant_id}/ask",
            json={"question": "What is the title?"},
        )

        assert response.status_code == 200
        body = response.json()

        assert body["question"] == "What is the title?"
        assert "Example Title" in body["answer"]
        assert "[Source 99]" not in body["answer"]
        assert len(body["citations"]) == 1
        assert body["citations"][0]["document_id"] == str(document_id)
        assert body["citations"][0]["chunk_id"] == str(chunk_id)
    finally:
        app.dependency_overrides.clear()


def test_ask_endpoint_excludes_uncited_sources(monkeypatch):
    app, client = create_test_client(object())

    tenant_id = uuid4()
    first_document = uuid4()
    second_document = uuid4()

    monkeypatch.setattr(ask_route, "hybrid_search", lambda **kwargs: [])
    monkeypatch.setattr(
        ask_route,
        "generate_answer",
        lambda **kwargs: GeneratedAnswer(
            answer="The title is Example Title [Source 1].",
            citations=[
                ContextCitation(
                    source_id=1,
                    document_id=first_document,
                    chunk_id=uuid4(),
                    chunk_index=0,
                ),
                ContextCitation(
                    source_id=2,
                    document_id=second_document,
                    chunk_id=uuid4(),
                    chunk_index=1,
                ),
            ],
        ),
    )

    try:
        response = client.post(
            f"/tenants/{tenant_id}/ask",
            json={"question": "What is the title?"},
        )

        assert response.status_code == 200
        citations = response.json()["citations"]

        assert len(citations) == 1
        assert citations[0]["source_id"] == 1
        assert citations[0]["document_id"] == str(first_document)
    finally:
        app.dependency_overrides.clear()


def test_ask_endpoint_handles_no_relevant_documents(monkeypatch):
    provider = object()
    app, client = create_test_client(provider)

    tenant_id = uuid4()

    monkeypatch.setattr(
        ask_route, "hybrid_search", lambda **kwargs: []
    )

    def unexpected_provider_call(**kwargs):
        raise AssertionError(
            "The provider should not be called when no context exists"
        )

    monkeypatch.setattr(
        ask_route, "generate_answer", unexpected_provider_call
    )

    # Test the real answer service separately below the route boundary:
    # the route uses the real implementation for this test.
    from app.services.answer_service import generate_answer

    monkeypatch.setattr(ask_route, "generate_answer", generate_answer)

    try:
        response = client.post(
            f"/tenants/{tenant_id}/ask",
            json={"question": "What does the document say?"},
        )

        assert response.status_code == 200
        body = response.json()

        assert body["answer"] == (
            "I couldn't find relevant information in the available documents."
        )
        assert body["citations"] == []
    finally:
        app.dependency_overrides.clear()


def test_ask_endpoint_returns_502_when_answer_generation_fails(
    monkeypatch,
):
    app, client = create_test_client(object())
    tenant_id = uuid4()

    monkeypatch.setattr(
        ask_route, "hybrid_search", lambda **kwargs: []
    )

    def fail_generation(**kwargs):
        raise RuntimeError("Simulated provider failure")

    monkeypatch.setattr(
        ask_route, "generate_answer", fail_generation
    )

    try:
        response = client.post(
            f"/tenants/{tenant_id}/ask",
            json={"question": "What does the document say?"},
        )

        assert response.status_code == 502
        assert response.json()["detail"] == (
            "The answer service is temporarily unavailable."
        )
        assert "Simulated provider failure" not in response.text
    finally:
        app.dependency_overrides.clear()
