import json
import httpx
from pydantic import ValidationError
from .base import LLMProvider, ProviderError, LLMMessage
from .common import validated_json, corrective_message, prompt_text
from ...models import ProcessDefinition, AuditResult, ClarificationResult, ModificationResult

def strict_schema(model):
    schema = model.model_json_schema()
    def convert(value):
        if isinstance(value, dict):
            value.pop('default', None)
            if value.get('type') == 'object':
                value['additionalProperties'] = False
                value['required'] = list(value.get('properties', {}))
            for nested in value.values():
                convert(nested)
        elif isinstance(value, list):
            for nested in value:
                convert(nested)
    convert(schema)
    return schema


class OpenAICompatibleProvider(LLMProvider):
    name = 'openai_compatible'

    def __init__(self, key, base_url, model, structured=None, timeout=90, max_tokens=16000, max_retries=2):
        self.key, self.base_url, self.model = key.strip(), base_url.strip().rstrip('/'), model.strip()
        self.structured, self.timeout = structured, timeout
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.attempt_count = 0

    def new_request(self):
        from copy import copy
        result = copy(self)
        result.attempt_count = 0
        return result

    @property
    def configured(self):
        return not self.missing_settings()

    def missing_settings(self):
        return [name for name, value in [('LLM_API_KEY', self.key), ('LLM_MODEL', self.model), ('LLM_BASE_URL', self.base_url)] if not value.strip()]

    def headers(self):
        return {'Authorization': 'Bearer ' + self.key}

    def token_budget(self):
        return {'max_tokens': self.max_tokens}

    async def _request(self, prompt, payload, model_class, correction=''):
        # JSON syntax and Pydantic validation belong to the adapter, for all operations.
        graph_correction = correction
        for attempt in range(self.max_retries + 1):
            try:
                return await self._request_once(prompt, payload, model_class, correction)
            except (ValidationError, ValueError) as exc:
                correction = corrective_message(exc)
                if graph_correction:
                    correction = graph_correction + '\n' + correction
        raise ProviderError(f'Модель вернула некорректный JSON после {self.max_retries} попыток исправления.', retryable=True)

    async def _request_once(self, prompt, payload, model_class, correction=''):
        if not self.configured:
            raise ProviderError('Заполните ' + ', '.join(self.missing_settings()) + ' в .env в корне проекта. Для встроенных примеров доступен LLM_PROVIDER=mock.')
        schema = strict_schema(model_class)
        system = prompt_text(prompt)
        messages = [LLMMessage('system', system + '\nJSON Schema:\n' + json.dumps(schema, ensure_ascii=False)),
                    LLMMessage('user', json.dumps(payload, ensure_ascii=False))]
        if correction:
            messages.append(LLMMessage('user', 'Исправь ошибки предыдущего результата: ' + correction))
        body = {'model': self.model, 'messages': [{'role': m.role, 'content': m.content} for m in messages]}
        body['response_format'] = {'type': 'json_schema', 'json_schema': {'name': model_class.__name__, 'strict': True, 'schema': schema}} if self.structured is not False else {'type': 'json_object'}
        body.update(self.token_budget())
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                self.attempt_count += 1
                response = await client.post(self.base_url + '/chat/completions', headers=self.headers(), json=body)
                # Auto mode only downgrades for an explicit unsupported format error.
                # Auth, quota and unrelated 400 errors never trigger this behavior.
                for mode in ('json_object', None):
                    error = response.text.lower()
                    unsupported = response.status_code in (400, 422) and any(term in error for term in ('response_format', 'json_schema', 'json_object')) and any(term in error for term in ('unsupported', 'not support', 'not available', 'not permitted'))
                    if self.structured is not None or not unsupported:
                        break
                    if mode:
                        body['response_format'] = {'type': mode}
                    else:
                        body.pop('response_format', None)
                    self.attempt_count += 1
                    response = await client.post(self.base_url + '/chat/completions', headers=self.headers(), json=body)
            response.raise_for_status()
            choice = response.json()['choices'][0]
            if choice.get('finish_reason') == 'length':
                raise ProviderError('Ответ модели обрезан. Увеличьте LLM_MAX_TOKENS или сократите описание процесса.')
            message = choice['message']
            if message.get('refusal'):
                raise ProviderError('Модель отказалась обрабатывать запрос. Уточните описание процесса.')
            return validated_json(message['content'], model_class)
        except httpx.TimeoutException as exc:
            raise ProviderError('Модель не ответила вовремя. Повторите запрос или проверьте настройки провайдера.', retryable=True) from exc
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code in (401, 403):
                raise ProviderError('Провайдер отклонил доступ. Проверьте backend API-ключ и права на модель.', retryable=code == 401) from exc
            if code == 429:
                raise ProviderError('Лимит запросов к модели исчерпан. Повторите позже.', retryable=True) from exc
            error = exc.response.text.lower()
            if code in (400, 404) and 'unavailable for free' in error:
                raise ProviderError('Выбранная модель больше недоступна бесплатно у провайдера. Выберите другую бесплатную модель или явно настройте платную версию.') from exc
            temporary = any(marker in error for marker in ('model_unavailable', 'model unavailable', 'no endpoints found', 'no available provider', 'temporarily unavailable', 'overloaded', 'service_unavailable'))
            if code in (400, 404, 408, 409, 422) and temporary:
                raise ProviderError('Модель временно недоступна у провайдера. Повторите запрос позже.', retryable=True) from exc
            if code == 408:
                raise ProviderError('Провайдер не ответил вовремя.', retryable=True) from exc
            if code == 404:
                raise ProviderError('Модель недоступна у провайдера.', retryable=True) from exc
            raise ProviderError(f'Провайдер вернул HTTP {code}. Проверьте модель, endpoint и поддержку Structured Outputs.', retryable=code >= 500) from exc
        except httpx.RequestError as exc:
            raise ProviderError('Не удалось соединиться с провайдером модели.', retryable=True) from exc
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError('Ответ провайдера не содержит корректный JSON.') from exc
        # Pydantic ValidationError is corrected inside _request before returning.

    async def parse_process(self, text, correction=''):
        return await self._request('extraction', {'description': text}, ProcessDefinition, correction)

    async def clarify_process(self, process, answers, correction=''):
        result = await self._request('clarification', {'process': process.model_dump(), 'answers': answers}, ProcessDefinition, correction)
        return ClarificationResult(process=result)

    async def modify_process(self, process, command, correction=''):
        result = await self._request('modification', {'process': process.model_dump(), 'command': command}, ProcessDefinition, correction)
        return ModificationResult(process=result)

    async def audit_process(self, process):
        return await self._request('audit', {'process': process.model_dump()}, AuditResult)
