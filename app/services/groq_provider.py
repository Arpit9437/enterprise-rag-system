from groq import Groq

from app.core.config import settings
from app.services.llm_provider import LLMConfigurationError


class GroqProvider:
    """Groq implementation of the application's LLMProvider interface."""

    def __init__(self) -> None:
        if not settings.groq_api_key:
            raise LLMConfigurationError(
                "GROQ_API_KEY is not configured"
            )

        if settings.llm_timeout_seconds <= 0:
            raise LLMConfigurationError("LLM timeout must be positive")

        if settings.llm_max_completion_tokens < 1:
            raise LLMConfigurationError(
                "LLM max completion tokens must be positive"
            )

        self.model = settings.llm_model
        self.max_completion_tokens = (
            settings.llm_max_completion_tokens
        )

        self.client = Groq(
            api_key=settings.groq_api_key,
            timeout=settings.llm_timeout_seconds,
            max_retries=2,
        )

    def generate(self, prompt: str) -> str:
        if not prompt or not prompt.strip():
            raise ValueError("Prompt cannot be blank")

        completion = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            temperature=0,
            max_completion_tokens=self.max_completion_tokens,
        )

        try:
            choices = completion.choices
            message = choices[0].message if choices else None
            content = getattr(message, "content", None)
        except (AttributeError, IndexError, TypeError):
            raise RuntimeError("Groq returned an invalid response") from None

        if not isinstance(content, str) or not content.strip():
            raise RuntimeError(
                "Groq returned an empty response or invalid response"
            )

        return content.strip()
