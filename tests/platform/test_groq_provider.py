from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services.groq_provider import GroqProvider


@pytest.fixture
def mock_groq_client(monkeypatch):
    calls = {}

    class FakeCompletions:
        def create(self, **kwargs):
            calls["request"] = kwargs
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="  The answer is Example Title.  "
                        )
                    )
                ]
            )

    class FakeGroq:
        def __init__(self, **kwargs):
            calls["client_config"] = kwargs
            self.chat = SimpleNamespace(
                completions=FakeCompletions()
            )

    monkeypatch.setattr(
        "app.services.groq_provider.Groq",
        FakeGroq,
    )

    return calls


def test_groq_provider_generates_response(
    monkeypatch, mock_groq_client
):
    monkeypatch.setattr(
        settings, "groq_api_key", "test-api-key"
    )
    monkeypatch.setattr(
        settings, "llm_model", "test-model"
    )
    monkeypatch.setattr(
        settings, "llm_timeout_seconds", 10.0
    )
    monkeypatch.setattr(
        settings, "llm_max_completion_tokens", 200
    )

    provider = GroqProvider()
    answer = provider.generate("What is the title?")

    assert answer == "The answer is Example Title."

    assert mock_groq_client["client_config"]["api_key"] == (
        "test-api-key"
    )
    assert mock_groq_client["client_config"]["timeout"] == 10.0
    assert mock_groq_client["client_config"]["max_retries"] == 2

    request = mock_groq_client["request"]
    assert request["model"] == "test-model"
    assert request["temperature"] == 0
    assert request["max_completion_tokens"] == 200
    assert request["messages"][0]["content"] == (
        "What is the title?"
    )


def test_groq_provider_rejects_missing_api_key(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", None)

    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        GroqProvider()


@pytest.mark.parametrize(
    "timeout",
    [0, -1],
)
def test_groq_provider_rejects_invalid_timeout(
    monkeypatch, timeout
):
    monkeypatch.setattr(
        settings, "groq_api_key", "test-api-key"
    )
    monkeypatch.setattr(
        settings, "llm_timeout_seconds", timeout
    )

    with pytest.raises(ValueError, match="timeout"):
        GroqProvider()


@pytest.mark.parametrize(
    "max_tokens",
    [0, -1],
)
def test_groq_provider_rejects_invalid_token_limit(
    monkeypatch, max_tokens
):
    monkeypatch.setattr(
        settings, "groq_api_key", "test-api-key"
    )
    monkeypatch.setattr(
        settings, "llm_timeout_seconds", 30.0
    )
    monkeypatch.setattr(
        settings, "llm_max_completion_tokens", max_tokens
    )

    with pytest.raises(ValueError, match="tokens"):
        GroqProvider()


def test_groq_provider_rejects_blank_prompt(
    monkeypatch, mock_groq_client
):
    monkeypatch.setattr(
        settings, "groq_api_key", "test-api-key"
    )

    provider = GroqProvider()

    with pytest.raises(ValueError, match="blank"):
        provider.generate("   ")


def test_groq_provider_rejects_empty_model_response(
    monkeypatch,
):
    monkeypatch.setattr(
        settings, "groq_api_key", "test-api-key"
    )

    class FakeCompletions:
        def create(self, **kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="  ")
                    )
                ]
            )

    class FakeGroq:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(
                completions=FakeCompletions()
            )

    monkeypatch.setattr(
        "app.services.groq_provider.Groq",
        FakeGroq,
    )

    provider = GroqProvider()

    with pytest.raises(RuntimeError, match="empty response"):
        provider.generate("Explain the document.")


def test_groq_provider_rejects_missing_completion_choices(monkeypatch):
    monkeypatch.setattr(
        settings,
        "groq_api_key",
        "test-api-key",
    )

    class FakeGroq:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **request: SimpleNamespace(choices=[])
                )
            )

    monkeypatch.setattr(
        "app.services.groq_provider.Groq",
        FakeGroq,
    )

    with pytest.raises(RuntimeError, match="invalid response"):
        GroqProvider().generate("Explain the document.")
