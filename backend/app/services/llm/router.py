import logging
from .base import LLMProvider, ProviderError
from ...models import LLMMetadata

logger = logging.getLogger('pulse.llm')


class LLMRouter(LLMProvider):
    """Request-scoped failover over the common, validated provider contract."""
    def __init__(self, primary: LLMProvider, fallback: LLMProvider | None = None, cooldowns=None):
        self.primary, self.fallback = primary, fallback
        self.name, self.model = primary.name, primary.model
        self._active = primary
        self._attempts = 0
        self.cooldowns = cooldowns

    def new_request(self):
        return LLMRouter(self.primary.new_request(), self.fallback.new_request() if self.fallback else None, self.cooldowns)

    def use_validation_fallback(self):
        if self.fallback is None or self._active is self.fallback:
            return False
        logger.warning('LLM failover: provider=%s fallback=%s reason=graph_validation operation=corrective_retry', self.primary.name, self.fallback.name)
        self._active = self.fallback
        return True

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
        remaining = self.cooldowns.remaining(self._active) if self.cooldowns else 0
        if remaining:
            raise ProviderError(f'Лимит модели исчерпан. Повторите через {int(remaining) + 1} с.', retryable=True, reason='http_429', retry_after=remaining)
        before = self._active.attempt_count
        try:
            return await getattr(self._active, operation)(*args)
        except ProviderError as exc:
            if self.cooldowns and exc.reason == 'http_429':
                self.cooldowns.block(self._active, exc.retry_after)
                logger.warning('LLM quota cooldown: provider=%s reason=http_429 retry_after=%s', self._active.name, exc.retry_after or 60)
            raise
        finally:
            self._attempts += max(1, self._active.attempt_count - before)

    async def _call(self, operation, *args):
        try:
            return await self._invoke(operation, *args)
        except ProviderError as exc:
            if not exc.retryable or self.fallback is None:
                raise
            if self._active is self.fallback:
                logger.warning('LLM fallback failed: provider=%s reason=%s operation=%s', self.fallback.name, exc.reason, operation)
                raise ProviderError('Не удалось получить ответ от AI-моделей. Попробуйте ещё раз позже.', reason=exc.reason) from exc
            logger.warning('LLM failover: provider=%s fallback=%s reason=%s operation=%s', self.primary.name, self.fallback.name, exc.reason, operation)
            self._active = self.fallback
            try:
                return await self._invoke(operation, *args)
            except ProviderError as backup_error:
                logger.warning('LLM fallback failed: provider=%s reason=%s operation=%s', self.fallback.name, backup_error.reason, operation)
                raise ProviderError('Не удалось получить ответ от AI-моделей. Попробуйте ещё раз позже.', reason=backup_error.reason) from backup_error

    async def parse_process(self, text, correction=''):
        return await self._call('parse_process', text, correction)

    async def analyze_ambiguities(self, text, context=None):
        return await self._call('analyze_ambiguities', text, context)

    async def clarify_process(self, process, answers, correction=''):
        return await self._call('clarify_process', process, answers, correction)

    async def modify_process(self, process, command, correction=''):
        return await self._call('modify_process', process, command, correction)

    async def audit_process(self, process):
        return await self._call('audit_process', process)
