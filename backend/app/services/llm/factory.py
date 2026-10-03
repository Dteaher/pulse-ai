from ...config import Settings, DEFAULT_BASE_URLS
from .base import LLMProvider, ProviderError
from .openai_provider import OpenAIProvider
from .openai_compatible_provider import OpenAICompatibleProvider
from .yandex_provider import YandexProvider
from .mock_provider import MockLLMProvider
from .router import LLMRouter
from .cooldown import cooldowns
from .multiai_provider import MultiAIProvider
from .gemini_provider import GeminiProvider
from .vertex_gemini_provider import VertexGeminiProvider


def _create(settings: Settings, name: str, *, fallback=False) -> LLMProvider:
    prefix = 'fallback_llm_' if fallback else 'llm_'
    key = getattr(settings, prefix + 'api_key')
    model = getattr(settings, prefix + 'model')
    url = getattr(settings, prefix + 'base_url') or DEFAULT_BASE_URLS.get(name, '')
    structured = getattr(settings, prefix + 'structured_output')
    if not fallback:
        key = settings.primary_llm_api_key if settings.primary_llm_api_key is not None else key
        model = settings.primary_llm_model if settings.primary_llm_model is not None else model
        url = settings.primary_llm_base_url if settings.primary_llm_base_url is not None else url
    options = dict(structured=structured, timeout=settings.llm_timeout, max_tokens=settings.llm_max_tokens, max_retries=settings.llm_max_retries)
    if name == 'mock':
        if fallback:
            raise ProviderError('Mock нельзя использовать как резерв реальной модели.')
        return MockLLMProvider()
    if name == 'openai':
        if structured is None:
            options['structured'] = True
        return OpenAIProvider(key, url, model, **options)
    if name == 'openai_compatible':
        return OpenAICompatibleProvider(key, url, model, **options)
    if name == 'multiai':
        return MultiAIProvider(key, url, model, reasoning_effort=settings.llm_reasoning_effort,
                               **options)
    if name == 'gemini':
        return GeminiProvider(key, url, model, **options)
    if name == 'vertex_gemini':
        return VertexGeminiProvider(key, model, timeout=settings.llm_timeout,
                                    max_tokens=settings.llm_max_tokens, max_retries=settings.llm_max_retries)
    if name == 'yandex':
        folder_id = settings.fallback_llm_folder_id if fallback else settings.yandex_folder_id
        return YandexProvider(key, url, model, folder_id=folder_id, **options)
    raise ProviderError('Неизвестный LLM_PROVIDER. Используйте multiai, vertex_gemini, gemini, openai, openai_compatible, yandex или mock.')


def get_llm_provider(settings: Settings | None = None) -> LLMProvider:
    """Build a connection snapshot. No singleton: .env changes apply next request."""
    settings = settings or Settings()
    name = (settings.primary_llm_provider or settings.llm_provider).strip().replace('-', '_')
    primary = _create(settings, name)
    if not settings.llm_fallback_enabled or not settings.fallback_llm_provider.strip():
        return LLMRouter(primary, cooldowns=cooldowns)
    fallback = _create(settings, settings.fallback_llm_provider.strip().replace('-', '_'), fallback=True)
    return LLMRouter(primary, fallback, cooldowns)
