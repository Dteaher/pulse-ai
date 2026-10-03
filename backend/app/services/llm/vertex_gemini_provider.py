import asyncio
import json
import httpx
from google import genai
from google.genai import types, errors
from pydantic import ValidationError
from .base import LLMProvider, ProviderError
from .common import prompt_text, validated_json, corrective_message
from ...models import ProcessDefinition, ClarificationResult, ModificationResult, AuditResult, AmbiguityAnalysis


def vertex_schema(model_class):
    """Project JSON Schema onto Vertex's supported Schema vocabulary.

    Constraint keywords stay enforced by the original Pydantic model on return.
    """
    source = model_class.model_json_schema()
    definitions = source.get('$defs', {})
    def project(value):
        if '$ref' in value:
            return project(definitions[value['$ref'].split('/')[-1]])
        alternatives = value.get('anyOf', [])
        non_null = [v for v in alternatives if v.get('type') != 'null']
        if alternatives and len(non_null) == 1 and len(non_null) < len(alternatives):
            return {**project(non_null[0]), 'nullable': True}
        result = {key: value[key] for key in ('type', 'description', 'enum', 'required') if key in value}
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

    def __init__(self, key, model, timeout=90, max_tokens=8000, max_retries=2):
        self.key, self.model = key.strip(), model.strip()
        self.timeout, self.max_tokens, self.max_retries = timeout, max_tokens, max_retries
        self.attempt_count = 0

    def new_request(self):
        from copy import copy
        result = copy(self)
        result.attempt_count = 0
        return result

    def missing_settings(self):
        return [name for name, value in [('FALLBACK_LLM_API_KEY', self.key), ('FALLBACK_LLM_MODEL', self.model)] if not value]

    async def _request(self, operation, payload, model_class, correction=''):
        if not self.configured:
            raise ProviderError('Заполните API-ключ Google Cloud и модель Vertex в .env.')
        initial_correction = correction
        for _ in range(self.max_retries + 1):
            try:
                return await self._request_once(operation, payload, model_class, correction)
            except (ValidationError, ValueError) as exc:
                correction = corrective_message(exc)
                if initial_correction:
                    correction = initial_correction + '\n' + correction
        raise ProviderError(f'Vertex вернул некорректный JSON после {self.max_retries} попыток исправления.', retryable=True, reason='invalid_response')

    async def _request_once(self, operation, payload, model_class, correction):
        schema = model_class.model_json_schema()
        user = json.dumps(payload, ensure_ascii=False)
        if correction:
            user += '\nИсправь ошибки предыдущего результата: ' + correction
        config = types.GenerateContentConfig(
            system_instruction=prompt_text(operation) + '\nJSON Schema:\n' + json.dumps(schema, ensure_ascii=False),
            response_mime_type='application/json', response_schema=vertex_schema(model_class),
            max_output_tokens=self.max_tokens,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        if self.model.startswith(('gemini-2.5-flash', 'publishers/google/models/gemini-2.5-flash')):
            # Keep the configured output budget for the structured process,
            # rather than consuming it on hidden thinking in Flash 2.5.
            config.thinking_config = types.ThinkingConfig(thinking_budget=0)
        options = types.HttpOptions(api_version='v1', timeout=self.timeout * 1000,
                                    retry_options=types.HttpRetryOptions(attempts=1))
        try:
            with genai.Client(vertexai=True, api_key=self.key, http_options=options) as client:
                async with client.aio as asynchronous:
                    self.attempt_count += 1
                    response = await asyncio.wait_for(asynchronous.models.generate_content(
                        model=self.model,
                        contents=[types.Content(role='user', parts=[types.Part.from_text(text=user)])],
                        config=config), timeout=self.timeout)
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
            if code in (401, 403):
                raise ProviderError('Vertex отклонил доступ. Проверьте Google Cloud API key и права Vertex Express.', retryable=code == 401, reason=f'http_{code}') from exc
            if code == 429:
                from .cooldown import retry_delay
                raise ProviderError('Лимит запросов Vertex исчерпан.', retryable=True, reason='http_429', retry_after=retry_delay(exc.response.headers)) from exc
            if code == 404 or code == 408 or code >= 500:
                raise ProviderError('Модель или сервис Vertex временно недоступны.', retryable=True, reason=f'http_{code}') from exc
            raise ProviderError(f'Vertex вернул HTTP {code}. Проверьте модель и настройки Google Cloud.') from exc

    async def parse_process(self, text, correction=''):
        return await self._request('extraction', {'description': text}, ProcessDefinition, correction)

    async def analyze_ambiguities(self, text, context=None):
        return await self._request('ambiguity', {'description': text, 'context': context or {}}, AmbiguityAnalysis)

    async def clarify_process(self, process, answers, correction=''):
        result = await self._request('clarification', {'original_text': process.description, 'process': process.model_dump(), 'ambiguities': [a.model_dump() for a in process.ambiguities], 'answers': answers}, ProcessDefinition, correction)
        return ClarificationResult(process=result)

    async def modify_process(self, process, command, correction=''):
        result = await self._request('modification', {'process': process.model_dump(), 'command': command}, ProcessDefinition, correction)
        return ModificationResult(process=result)

    async def audit_process(self, process):
        return await self._request('audit', {'process': process.model_dump()}, AuditResult)
