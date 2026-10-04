import logging
import asyncio
from time import monotonic
from .performance import call_context
from .base import LLMProvider, ProviderError
from ...models import LLMMetadata

logger = logging.getLogger('pulse.llm')


class LLMRouter(LLMProvider):
    """Request-scoped failover over the common, validated provider contract."""
    def __init__(self, primary: LLMProvider, fallback: LLMProvider | None = None, cooldowns=None, *, budget=None, fallback_reserve=30):
        self.primary, self.fallback = primary, fallback
        self.name, self.model = primary.name, primary.model
        self._active = primary
        self._attempts = 0
        self.cooldowns = cooldowns
        self.budget, self.fallback_reserve = budget, fallback_reserve
        self._deadline = None

    def new_request(self):
        return LLMRouter(self.primary.new_request(), self.fallback.new_request() if self.fallback else None, self.cooldowns, budget=self.budget, fallback_reserve=self.fallback_reserve)

    async def close(self):
        await self.primary.close()
        if self.fallback:
            await self.fallback.close()

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
    def business_coverage_enabled(self):
        return self._active.business_coverage_enabled

    async def check_business_coverage(self, process, source_text, answers=None):
        return await self._call('check_business_coverage', process, source_text, answers)

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
        correction = operation == 'repair_process' or operation in ('parse_process', 'modify_process', 'clarify_process') and bool(args[-1]) and '"errors"' in str(args[-1])
        token = call_context.set({'fallback': self._active is self.fallback, 'corrective': correction})
        try:
            if self.budget is None:
                return await getattr(self._active, operation)(*args)
            if self._deadline is None:
                self._deadline = monotonic() + self.budget
            remaining_budget = self._deadline - monotonic()
            reserve = min(self.fallback_reserve, self.budget / 3) if self._active is self.primary and self.fallback else 0
            allowance = min(getattr(self._active, 'timeout', self.budget), remaining_budget - reserve)
            if allowance <= 0:
                raise ProviderError('Бюджет ожидания модели исчерпан. Повторите запрос позже.', retryable=True, reason='timeout')
            try:
                return await asyncio.wait_for(getattr(self._active, operation)(*args), timeout=allowance)
            except asyncio.TimeoutError:
                raise ProviderError('Модель не ответила в пределах бюджета запроса.', retryable=True, reason='timeout') from None
        except ProviderError as exc:
            if self.cooldowns and exc.reason == 'http_429':
                self.cooldowns.block(self._active, exc.retry_after)
                logger.warning('LLM quota cooldown: provider=%s reason=http_429 retry_after=%s', self._active.name, exc.retry_after or 60)
            raise
        finally:
            self._attempts += max(0 if operation == 'prepare_process' else 1, self._active.attempt_count - before)
            call_context.reset(token)

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

    @property
    def patch_corrective_enabled(self):
        return self._active.patch_corrective_enabled

    @property
    def supports_structural_repair(self):
        return self._active.supports_structural_repair

    async def repair_process(self, context):
        return await self._call('repair_process', context)

    async def analyze_ambiguities(self, text, context=None):
        return await self._call('analyze_ambiguities', text, context)

    async def prepare_process(self, text, context=None):
        enabled = getattr(self._active, 'combined_parse_enabled', False) and (context or {}).get('operation') != 'modify' or getattr(self._active, 'combined_enabled', False) or getattr(self._active, 'patch_enabled', False) and (context or {}).get('operation') == 'modify'
        if not enabled or not self._active.supports_preparation:
            return None
        return await self._call('prepare_process', text, context)

    async def clarify_process(self, process, answers, correction=''):
        return await self._call('clarify_process', process, answers, correction)

    async def modify_process(self, process, command, correction=''):
        return await self._call('modify_process', process, command, correction)

    async def audit_process(self, process):
        return await self._call('audit_process', process)
