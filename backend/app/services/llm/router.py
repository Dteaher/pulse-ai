import logging
from .base import LLMProvider, ProviderError
from ...models import LLMMetadata

logger = logging.getLogger('pulse.llm')


class LLMRouter(LLMProvider):
    """Request-scoped failover over the common, validated provider contract."""
    def __init__(self, primary: LLMProvider, fallback: LLMProvider | None = None):
        self.primary, self.fallback = primary, fallback
        self.name, self.model = primary.name, primary.model
        self._active = primary
        self._attempts = 0

    def new_request(self):
        return LLMRouter(self.primary.new_request(), self.fallback.new_request() if self.fallback else None)

    @property
    def business_audit_available(self):
        return self._active.business_audit_available

    @property
    def metadata(self):
        return LLMMetadata(provider_used=self._active.name, model_used=self._active.model,
                           fallback_used=self._active is self.fallback, attempts=self._attempts)

    def missing_settings(self):
        return self.primary.missing_settings()

    @property
    def connection_info(self):
        return {'primary': {'provider': self.primary.name, 'model': self.primary.model},
                'fallback': {'enabled': self.fallback is not None,
                             'provider': self.fallback.name if self.fallback else '',
                             'model': self.fallback.model if self.fallback else ''}}

    async def _invoke(self, operation, *args):
        before = self._active.attempt_count
        try:
            return await getattr(self._active, operation)(*args)
        finally:
            self._attempts += max(1, self._active.attempt_count - before)

    async def _call(self, operation, *args):
        try:
            return await self._invoke(operation, *args)
        except ProviderError as exc:
            if not exc.retryable or self.fallback is None:
                raise
            if self._active is self.fallback:
                raise ProviderError('Не удалось получить ответ от AI-моделей. Попробуйте ещё раз позже.') from exc
            logger.warning('LLM failover: %s -> %s', self.primary.name, self.fallback.name)
            self._active = self.fallback
            try:
                return await self._invoke(operation, *args)
            except ProviderError as backup_error:
                raise ProviderError('Не удалось получить ответ от AI-моделей. Попробуйте ещё раз позже.') from backup_error

    async def parse_process(self, text, correction=''):
        return await self._call('parse_process', text, correction)

    async def clarify_process(self, process, answers, correction=''):
        return await self._call('clarify_process', process, answers, correction)

    async def modify_process(self, process, command, correction=''):
        return await self._call('modify_process', process, command, correction)

    async def audit_process(self, process):
        return await self._call('audit_process', process)
