
from typing import Protocol


class LLMConfigurationError(ValueError):
    """Raised when the configured answer provider cannot be initialized."""


class LLMProvider(Protocol):
    """Contract that every LLM provider must satisfy."""

    def generate(self, prompt: str) -> str:
        """Generate a response from a complete prompt."""
        ...
