from app.services.groq_provider import GroqProvider
from app.services.llm_provider import LLMProvider
from app.core.config import settings


class ConfiguredLLMProvider:
    def generate(self, prompt: str) -> str:
        if settings.llm_provider == "groq":
            return GroqProvider().generate(prompt)
        raise ValueError("Unsupported LLM provider configuration")


def get_llm_provider() -> LLMProvider:
    return ConfiguredLLMProvider()
