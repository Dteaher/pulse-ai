import asyncio
import json
from time import perf_counter
from functools import lru_cache
import httpx
from google import genai
from google.genai import types, errors
from pydantic import ValidationError
from .base import LLMProvider, ProviderError
from .common import prompt_text, performance_prompt, validated_json, corrective_message, explicit_unknown
from .performance import compact_process, operation_budget, call_context
from ...telemetry import record_call
from ...models import ProcessDefinition, ClarificationResult, ModificationResult, AuditResult, AmbiguityAnalysis, ProcessPreparation, ProcessPatch, ModificationPreparation


@lru_cache(maxsize=16)
def vertex_schema(model_class):
    """Project JSON Schema onto Vertex's supported Schema vocabulary.

    Constraint keywords stay enforced by the original Pydantic model on return.
    """
    source = model_class.model_json_schema()
    definitions = source.get('$defs', {})
    def project(value):
        if '$ref' in value:
            return project(definitions[value['$ref'].split('/')[-1]])
        alternatives = value.get('anyOf', value.get('oneOf', []))
        non_null = [v for v in alternatives if v.get('type') != 'null']
        if alternatives and len(non_null) == 1 and len(non_null) < len(alternatives):
            return {**project(non_null[0]), 'nullable': True}
        result = {key: value[key] for key in ('type', 'description', 'enum', 'required') if key in value}
        if 'const' in value:
            result['enum'] = [value['const']]
        if 'properties' in value:
            result['properties'] = {name: project(prop) for name, prop in value['properties'].items()}
        if 'items' in value:
            result['items'] = project(value['items'])
        if alternatives:
            result['anyOf'] = [project(v) for v in alternatives]
        return result
    return project(source)


class VertexGeminiProvider(LLMProvider):
    """Google Cloud Vertex Express API-key mode, never the AI Studio API."""
    name = 'vertex_gemini'
    supports_preparation = True
    supports_structural_repair = True

    def __init__(self, key, model, timeout=90, max_tokens=8000, max_retries=2):
        self.key, self.model = key.strip(), model.strip()
        self.timeout, self.max_tokens, self.max_retries = timeout, max_tokens, max_retries
        self.attempt_count = 0
        self.performance_enabled = False
        self.combined_enabled = False
        self.operation_budgets = {}
        self._clients = {}

    def new_request(self):
        from copy import copy
        result = copy(self)
        result.attempt_count = 0
        return result

    async def close(self):
        loop = asyncio.get_running_loop()
        client = self._clients.pop(loop, None)
        if client:
            await client.aio.aclose()
            client.close()

    def missing_settings(self):
        return [name for name, value in [('FALLBACK_LLM_API_KEY', self.key), ('FALLBACK_LLM_MODEL', self.model)] if not value]

    async def _request(self, operation, payload, model_class, correction=''):
        patch_base = None
        preparation_base = ProcessDefinition.model_validate(payload['context']['base_process']) if model_class is ModificationPreparation else None
        if getattr(self, 'patch_enabled', False) and model_class is ProcessDefinition:
            if operation == 'modification' and not correction.strip().startswith('{'):
                patch_base = ProcessDefinition.model_validate(payload['process'])
                operation, model_class = 'modification_patch', ProcessPatch
            elif correction.strip().startswith('{'):
                try:
                    fix = json.loads(correction)
                    if 'errors' in fix and 'previous_result' in fix:
                        patch_base = ProcessDefinition.model_validate(fix['previous_result'])
                        payload = {'process': compact_process(patch_base), 'errors': fix['errors'], 'business_context': fix.get('business_context', {})}
                        operation, model_class, correction = 'corrective_patch', ProcessPatch, ''
                except (ValueError, KeyError):
                    pass
        if not self.configured:
            raise ProviderError('Заполните API-ключ Google Cloud и модель Vertex в .env.')
        initial_correction = correction
        retries = min(self.max_retries, 1) if self.performance_enabled else self.max_retries
        for attempt in range(retries + 1):
            token = call_context.set({**call_context.get(), 'json_corrective': attempt > 0})
            try:
                result = await self._request_once(operation, payload, model_class, correction)
                if preparation_base is not None:
                    return validated_json(result.apply(preparation_base).model_dump_json(), ProcessPreparation)
                return validated_json(result.apply(patch_base, allow_business_removal=operation != 'corrective_patch').model_dump_json(), ProcessDefinition) if patch_base is not None else result
            except (ValidationError, ValueError) as exc:
                correction = ('Исправь ProcessPatch: ' + str(exc)[:500]) if (patch_base is not None or preparation_base is not None) and not isinstance(exc, ValidationError) else corrective_message(exc)
                if initial_correction:
                    correction = initial_correction + '\n' + correction
            finally:
                call_context.reset(token)
        raise ProviderError(f'Vertex вернул некорректный JSON после {retries} попыток исправления.', retryable=True, reason='invalid_response')

    async def _request_once(self, operation, payload, model_class, correction):
        if self.performance_enabled and correction.strip().startswith('{'):
            try:
                fix = json.loads(correction)
                if 'errors' in fix and 'previous_result' in fix:
                    operation = 'corrective'
                    payload = {'process': fix['previous_result'], 'errors': fix['errors'], 'business_context': fix.get('business_context', {})}
                    correction = ''
            except json.JSONDecodeError:
                pass
        schema = model_class.model_json_schema()
        user = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        if correction:
            user += '\nИсправь ошибки предыдущего результата: ' + correction
        system = performance_prompt(operation) if self.performance_enabled else prompt_text(operation) + '\nJSON Schema:\n' + json.dumps(schema, ensure_ascii=False)
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type='application/json', response_schema=vertex_schema(model_class),
            max_output_tokens=None if getattr(self, 'omit_token_limit', False) else operation_budget(self, operation, payload, operation == 'corrective'),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        if self.model.startswith(('gemini-2.5-flash', 'publishers/google/models/gemini-2.5-flash')):
            # Keep the configured output budget for the structured process,
            # rather than consuming it on hidden thinking in Flash 2.5.
            config.thinking_config = types.ThinkingConfig(thinking_budget=0)
        options = types.HttpOptions(api_version='v1', timeout=self.timeout * 1000,
                                    retry_options=types.HttpRetryOptions(attempts=1))
        entry = {'provider': self.name, 'model': self.model, 'operation': operation,
                 'reasoning_effort': 'thinking_budget=0' if config.thinking_config else None,
                 'system_prompt_chars': len(system), 'user_payload_chars': len(user),
                 'prompt_size_chars': len(system)+len(user), 'max_tokens': config.max_output_tokens,
                 'corrective': operation == 'corrective' or call_context.get().get('corrective', False) or call_context.get().get('json_corrective', False),
                 'json_corrective': call_context.get().get('json_corrective', False),
                 'fallback': call_context.get().get('fallback', False), 'input_tokens': None, 'output_tokens': None}
        started = perf_counter()
        try:
            if self.performance_enabled:
                loop = asyncio.get_running_loop()
                if loop not in self._clients:
                    self._clients[loop] = genai.Client(vertexai=True, api_key=self.key, http_options=options)
                self.attempt_count += 1
                response = await asyncio.wait_for(self._clients[loop].aio.models.generate_content(
                    model=self.model, contents=[types.Content(role='user', parts=[types.Part.from_text(text=user)])], config=config), timeout=self.timeout)
            else:
                with genai.Client(vertexai=True, api_key=self.key, http_options=options) as client:
                    async with client.aio as asynchronous:
                        self.attempt_count += 1
                        response = await asyncio.wait_for(asynchronous.models.generate_content(
                            model=self.model,
                            contents=[types.Content(role='user', parts=[types.Part.from_text(text=user)])],
                            config=config), timeout=self.timeout)
            entry['http_status'] = 200
            usage = getattr(response, 'usage_metadata', None)
            if usage:
                entry.update(input_tokens=usage.prompt_token_count, output_tokens=usage.candidates_token_count,
                             reasoning_tokens=usage.thoughts_token_count)
            if response.candidates:
                reason = str(response.candidates[0].finish_reason or '')
                if 'MAX_TOKENS' in reason:
                    raise ProviderError('Ответ Vertex обрезан. Увеличьте LLM_MAX_TOKENS или сократите описание.')
                if any(value in reason for value in ('SAFETY', 'BLOCKLIST', 'PROHIBITED_CONTENT')):
                    raise ProviderError('Vertex отказался обрабатывать запрос. Уточните описание процесса.')
            return validated_json(response.text, model_class)
        except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
            raise ProviderError('Vertex не ответил вовремя.', retryable=True, reason='timeout') from exc
        except httpx.RequestError as exc:
            raise ProviderError('Не удалось соединиться с Vertex AI.', retryable=True, reason='network_error') from exc
        except errors.APIError as exc:
            code = exc.code
            entry['http_status'] = code
            if code in (401, 403):
                raise ProviderError('Vertex отклонил доступ. Проверьте Google Cloud API key и права Vertex Express.', retryable=code == 401, reason=f'http_{code}') from exc
            if code == 429:
                from .cooldown import retry_delay
                raise ProviderError('Лимит запросов Vertex исчерпан.', retryable=True, reason='http_429', retry_after=retry_delay(exc.response.headers)) from exc
            if code == 404 or code == 408 or code >= 500:
                raise ProviderError('Модель или сервис Vertex временно недоступны.', retryable=True, reason=f'http_{code}') from exc
            raise ProviderError(f'Vertex вернул HTTP {code}. Проверьте модель и настройки Google Cloud.') from exc
        finally:
            entry['duration_ms'] = round((perf_counter()-started)*1000, 2)
            record_call(entry)

    async def parse_process(self, text, correction=''):
        return await self._request('extraction', {'description': text}, ProcessDefinition, correction)

    async def repair_process(self, context):
        from ...repair import RepairPatch
        return await self._request('structural_repair', context, RepairPatch)

    async def analyze_ambiguities(self, text, context=None):
        return await self._request('ambiguity', {'description': text, 'context': context or {}}, AmbiguityAnalysis)

    async def prepare_process(self, text, context=None):
        context = context or {}
        if getattr(self, 'combined_parse_enabled', False) and context.get('operation') != 'modify':
            from .decision import ParseDecision, ReadyDecision
            decision = (await self._request('preparation_v2', {'description': text, 'context': context}, ParseDecision)).result
            if isinstance(decision, ReadyDecision):
                process = validated_json(decision.process.model_dump_json(), ProcessDefinition)
                return ProcessPreparation(status='ready', process=process, analysis=AmbiguityAnalysis(assumptions=process.assumptions))
            return ProcessPreparation(status='clarification_required', process=None, analysis=decision.analysis)
        if getattr(self, 'patch_enabled', False) and context.get('operation') == 'modify' and context.get('base_process'):
            result = await self._request('preparation_patch', {'command': text, 'context': context}, ModificationPreparation)
            return result
        if not self.combined_enabled or explicit_unknown(text, context):
            return None
        return await self._request('preparation', {'description': text, 'context': context}, ProcessPreparation)

    async def clarify_process(self, process, answers, correction=''):
        result = await self._request('clarification', {'process': compact_process(process), 'answers': answers}, ProcessDefinition, correction)
        return ClarificationResult(process=result)

    async def modify_process(self, process, command, correction=''):
        result = await self._request('modification', {'process': compact_process(process), 'command': command}, ProcessDefinition, correction)
        return ModificationResult(process=result)

    async def audit_process(self, process):
        return await self._request('audit', {'process': compact_process(process)}, AuditResult)

    async def check_business_coverage(self, process, source_text, answers=None):
        from ...business_coverage import CoverageReport
        from .common import checked_coverage
        async def call(correction):
            return await self._request('business_coverage', {'source_text': source_text,
                'answers': answers or {}, 'process': compact_process(process)}, CoverageReport, correction)
        return await checked_coverage(call, process, source_text, answers, self.max_retries)
