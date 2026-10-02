from .openai_compatible_provider import OpenAICompatibleProvider


class GeminiProvider(OpenAICompatibleProvider):
    """Gemini's official OpenAI-compatible API, preserving the internal contract."""
    name = 'gemini'
