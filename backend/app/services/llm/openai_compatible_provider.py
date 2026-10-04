import json
import asyncio
from functools import lru_cache
from time import perf_counter
import httpx
from pydantic import ValidationError
from .base import LLMProvider, ProviderError, LLMMessage
from .common import validated_json, corrective_message, prompt_text, performance_prompt, explicit_unknown
from .performance import compact_process, operation_budget, call_context
from ...telemetry import record_call
from .structured_output import response_content
from ...models import ProcessDefinition, AuditResult, ClarificationResult, ModificationResult, AmbiguityAnalysis, ProcessPreparation, ProcessPatch, ModificationPreparation

@lru_cache(maxsize=16)
def strict_schema(model):
    schema = model.model_json_schema()
    def convert(value):
        if isinstance(value, dict):
            # Wire variants have distinct literal statuses. Native APIs accept
            # nested anyOf; local Pydantic retains the discriminator validation.
            value.pop('discriminator', None)
            if 'oneOf' in value:
                value['anyOf'] = value.pop('oneOf')
            value.pop('default', None)
            if '$ref' in value:
                value.pop('title', None)  # Native strict API forbids siblings on refs.
            if value.get('type') == 'object':
                value['additionalProperties'] = False
                value['required'] = list(value.get('properties', {}))
            for key, nested in value.items():
                if key in ('properties', '$defs'):
                    # These are name-to-schema maps. A business field named
                    # "default" is not the JSON Schema default keyword.
                    for child in nested.values():
                        convert(child)
                else:
                    convert(nested)
        elif isinstance(value, list):
            for nested in value:
                convert(nested)
    convert(schema)
    return schema


class OpenAICompatibleProvider(LLMProvider):
    name = 'openai_compatible'
    supports_preparation = True
    supports_structural_repair = True

    def __init__(self, key, base_url, model, structured=None, timeout=90, max_tokens=16000, max_retries=2):
        self.key, self.base_url, self.model = key.strip(), base_url.strip().rstrip('/'), model.strip()
        self.structured, self.timeout = structured, timeout
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.attempt_count = 0
        self.performance_enabled = False
        self.operation_budgets = {}
        self.combined_enabled = False
        self.debug_raw_response = False
        self.output_retry_enabled = False
        self.omit_token_limit = False
        self.parse_retry_max_tokens = 16000
        self.prompt_hints = ()
        self._clients = {}

    def new_request(self):
        from copy import copy
        result = copy(self)
        result.attempt_count = 0
        return result

    def client(self):
        loop = asyncio.get_running_loop()
        if loop not in self._clients or self._clients[loop].is_closed:
            self._clients[loop] = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=min(10, self.timeout), pool=min(10, self.timeout)))
        return self._clients[loop]

    async def close(self):
        for loop, client in list(self._clients.items()):
            if loop is asyncio.get_running_loop() and not client.is_closed:
                await client.aclose()
            if loop.is_closed() or loop is asyncio.get_running_loop():
                self._clients.pop(loop, None)

    @property
    def configured(self):
        return not self.missing_settings()

    def missing_settings(self):
        return [name for name, value in [('LLM_API_KEY', self.key), ('LLM_MODEL', self.model), ('LLM_BASE_URL', self.base_url)] if not value.strip()]

    def headers(self):
        return {'Authorization': 'Bearer ' + self.key}

    def token_budget(self):
        return {'max_tokens': self.max_tokens}

    def request_options(self):
        return {}

    async def _request(self, prompt, payload, model_class, correction=''):
        patch_base = None
        preparation_base = ProcessDefinition.model_validate(payload['context']['base_process']) if model_class is ModificationPreparation else None
        if getattr(self, 'patch_enabled', False) and model_class is ProcessDefinition:
            if prompt == 'modification' and not correction.strip().startswith('{'):
                patch_base = ProcessDefinition.model_validate(payload['process'])
                prompt, model_class = 'modification_patch', ProcessPatch
            elif correction.strip().startswith('{'):
                try:
                    fix = json.loads(correction)
                    if 'errors' in fix and 'previous_result' in fix:
                        patch_base = ProcessDefinition.model_validate(fix['previous_result'])
                        payload = {'process': compact_process(patch_base), 'errors': fix['errors'], 'business_context': fix.get('business_context', {})}
                        prompt, model_class, correction = 'corrective_patch', ProcessPatch, ''
                except (ValueError, KeyError):
                    pass
        # JSON syntax and Pydantic validation belong to the adapter, for all operations.
        graph_correction = correction
        request_started = perf_counter()
        retries = min(self.max_retries, 1) if self.performance_enabled else self.max_retries
        for attempt in range(retries + 1):
            token = call_context.set({**call_context.get(), 'json_corrective': attempt > 0})
            try:
                try:
                    result = await self._request_once(prompt, payload, model_class, correction)
                except ProviderError as exc:
                    if not (exc.reason == 'output_truncated' and self.output_retry_enabled and not self.omit_token_limit and
                            prompt == 'extraction' and not correction and attempt == 0):
                        raise
                    current_budget = operation_budget(self,prompt,payload)
                    larger_budget = min(self.parse_retry_max_tokens, current_budget + current_budget//2)
                    remaining = self.timeout-(perf_counter()-request_started)
                    if larger_budget <= current_budget or remaining <= 0:
                        raise
                    budget_token = call_context.set({**call_context.get(),'output_budget_override':larger_budget,
                                                     'output_budget_retry':True})
                    try:
                        # Same initial prompt, not a JSON corrective; never forwards
                        # corrupted content. At most one retry within the deadline.
                        result = await asyncio.wait_for(self._request_once(prompt,payload,model_class,''),remaining)
                    except asyncio.TimeoutError:
                        raise ProviderError('Бюджет повторной генерации исчерпан.',reason='timeout') from None
                    finally:
                        call_context.reset(budget_token)
                if preparation_base is not None:
                    return validated_json(result.apply(preparation_base).model_dump_json(), ProcessPreparation)
                return validated_json(result.apply(patch_base, allow_business_removal=prompt != 'corrective_patch').model_dump_json(), ProcessDefinition) if patch_base is not None else result
            except (ValidationError, ValueError) as exc:
                correction = ('Исправь ProcessPatch: ' + str(exc)[:500]) if (patch_base is not None or preparation_base is not None) and not isinstance(exc, ValidationError) else corrective_message(exc)
                if graph_correction:
                    correction = graph_correction + '\n' + correction
            finally:
                call_context.reset(token)
        raise ProviderError(f'Модель вернула некорректный JSON после {retries} попыток исправления.', retryable=True, reason='invalid_response')

    async def _request_once(self, prompt, payload, model_class, correction=''):
        if not self.configured:
            raise ProviderError('Заполните ' + ', '.join(self.missing_settings()) + ' в .env в корне проекта. Для встроенных примеров доступен LLM_PROVIDER=mock.')
        schema = strict_schema(model_class)
        graph_fix = None
        if self.performance_enabled and correction.strip().startswith('{'):
            try:
                graph_fix = json.loads(correction)
            except json.JSONDecodeError:
                pass
        if graph_fix and 'previous_result' in graph_fix and 'errors' in graph_fix:
            prompt = 'corrective'
            payload = {'process': graph_fix['previous_result'], 'errors': graph_fix['errors'],
                       'policy': graph_fix.get('policy', ''), 'business_context': graph_fix.get('business_context', {})}
            correction = ''
        system = performance_prompt(prompt) if self.performance_enabled else prompt_text(prompt)
        if self.prompt_hints:
            system += '\n' + '\n'.join(self.prompt_hints)
        schema_prompt = '\nJSON Schema:\n' + json.dumps(schema, ensure_ascii=False, separators=(',', ':'))
        # Native structured output already supplies the schema. Repeating it in
        # the prompt wastes context and the provider's token budget.
        messages = [LLMMessage('system', system + (schema_prompt if self.structured is False else '')),
                    LLMMessage('user', json.dumps(payload, ensure_ascii=False, separators=(',', ':')))]
        if correction:
            messages.append(LLMMessage('user', 'Исправь ошибки предыдущего результата: ' + correction))
        body = {'model': self.model, 'messages': [{'role': m.role, 'content': m.content} for m in messages]}
        body['response_format'] = {'type': 'json_schema', 'json_schema': {'name': model_class.__name__, 'strict': True, 'schema': schema}} if self.structured is not False else {'type': 'json_object'}
        body.update(self.token_budget())
        budget = operation_budget(self, prompt, payload, bool(graph_fix))
        if prompt == 'extraction' and not correction and call_context.get().get('output_budget_override'):
            budget = call_context.get()['output_budget_override']
        for name in ('max_tokens', 'max_completion_tokens'):
            if name in body:
                body[name] = budget
        self._operation = 'modification' if prompt in ('modification_patch', 'preparation_patch') or prompt == 'preparation' and payload.get('context', {}).get('operation') == 'modify' else prompt
        self._corrective = bool(graph_fix) or prompt in ('corrective_patch', 'structural_repair') or call_context.get().get('json_corrective', False)
        body.update(self.request_options())
        if self.omit_token_limit:
            body.pop('max_tokens', None)
            body.pop('max_completion_tokens', None)
        started = perf_counter()
        before_attempts = self.attempt_count
        entry = {'provider': self.name, 'model': self.model, 'operation': prompt,
                 'reasoning_effort': body.get('reasoning_effort'),
                 'system_prompt_chars': len(messages[0].content),
                 'user_payload_chars': sum(len(m.content) for m in messages[1:]),
                 'prompt_size_chars': sum(len(m.content) for m in messages),
                 'schema_chars': len(json.dumps(schema, separators=(',', ':'))),
                 'max_tokens': None if self.omit_token_limit else budget, 'corrective': self._corrective or call_context.get().get('corrective', False),
                 'json_corrective': call_context.get().get('json_corrective', False),
                 'output_budget_retry': call_context.get().get('output_budget_retry', False),
                 'fallback': call_context.get().get('fallback', False),
                 'input_tokens': None, 'output_tokens': None}
        try:
            client = self.client()
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
                if not body['messages'][0]['content'].endswith(schema_prompt):
                    body['messages'][0]['content'] += schema_prompt
                self.attempt_count += 1
                response = await client.post(self.base_url + '/chat/completions', headers=self.headers(), json=body)
            if self.debug_raw_response:
                from .raw_debug import save_raw
                try:
                    try:
                        debug_body = response.json()
                    except ValueError:
                        debug_body = {'non_json_response': response.text}
                    save_raw(provider=self.name, model=self.model, status=response.status_code,
                             duration_ms=round((perf_counter()-started)*1000,2), body=body,
                             response=debug_body, model_class=model_class, secrets=(self.key,))
                except (OSError, ValueError):
                    pass  # Debug I/O must never change request behavior.
            response.raise_for_status()
            entry['http_status'] = response.status_code
            data = response.json()
            if not isinstance(data,dict):
                response_content(data)  # Typed format failure; no AttributeError leak.
            usage = data.get('usage') or {}
            usage = usage if isinstance(usage,dict) else {}
            entry['response_model'] = data.get('model')
            entry['input_tokens'] = usage.get('prompt_tokens', usage.get('input_tokens'))
            entry['output_tokens'] = usage.get('completion_tokens', usage.get('output_tokens'))
            details = usage.get('completion_tokens_details') or {}
            entry['reasoning_tokens'] = details.get('reasoning_tokens') if isinstance(details,dict) else None
            # Validate shape before indexing; reasoning is never used as output.
            choices = data.get('choices') if isinstance(data, dict) else None
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                msg = choices[0].get('message')
                entry['finish_reason'] = choices[0].get('finish_reason')
                if choices[0].get('finish_reason') in ('length','max_tokens'):
                    entry['failure_class'] = 'OUTPUT_TRUNCATED'
                    raise ProviderError('Ответ модели обрезан. Проверьте operation-specific output budget (LLM_PARSE_MAX_TOKENS; глобальный резерв LLM_MAX_TOKENS).',reason='output_truncated')
                if isinstance(msg, dict) and msg.get('refusal'):
                    raise ProviderError('Модель отказалась обрабатывать запрос. Уточните описание процесса.')
            content = response_content(data)
            choice = data['choices'][0]
            if choice.get('finish_reason') == 'length':
                raise ProviderError('Ответ модели обрезан. Увеличьте LLM_MAX_TOKENS или сократите описание процесса.')
            message = choice['message']
            if message.get('refusal'):
                raise ProviderError('Модель отказалась обрабатывать запрос. Уточните описание процесса.')
            return validated_json(content, model_class)
        except httpx.TimeoutException as exc:
            raise ProviderError('Модель не ответила вовремя. Повторите запрос или проверьте настройки провайдера.', retryable=True, reason='timeout') from exc
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            entry['http_status'] = code
            if code in (401, 403):
                raise ProviderError('Провайдер отклонил доступ. Проверьте backend API-ключ и права на модель.', retryable=code == 401, reason=f'http_{code}') from exc
            if code == 429:
                from .cooldown import retry_delay
                delay = retry_delay(exc.response.headers)
                raise ProviderError(f'Лимит модели исчерпан. Повторите через {int(delay) + 1} с.', retryable=True, reason='http_429', retry_after=delay) from exc
            error = exc.response.text.lower()
            if code in (400, 404) and 'unavailable for free' in error:
                raise ProviderError('Выбранная модель больше недоступна бесплатно у провайдера. Выберите другую бесплатную модель или явно настройте платную версию.') from exc
            temporary = any(marker in error for marker in ('model_unavailable', 'model unavailable', 'no endpoints found', 'no available provider', 'temporarily unavailable', 'overloaded', 'service_unavailable'))
            if code in (400, 404, 408, 409, 422) and temporary:
                raise ProviderError('Модель временно недоступна у провайдера. Повторите запрос позже.', retryable=True, reason='provider_unavailable') from exc
            if code == 408:
                raise ProviderError('Провайдер не ответил вовремя.', retryable=True, reason='timeout') from exc
            if code == 404:
                raise ProviderError('Модель недоступна у провайдера.', retryable=True, reason='model_unavailable') from exc
            raise ProviderError(f'Провайдер вернул HTTP {code}. Проверьте модель, endpoint и поддержку Structured Outputs.', retryable=code >= 500, reason=f'http_{code}') from exc
        except httpx.RequestError as exc:
            raise ProviderError('Не удалось соединиться с провайдером модели.', retryable=True, reason='network_error') from exc
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError('Ответ провайдера не содержит корректный JSON.') from exc
        finally:
            entry['http_attempts'] = self.attempt_count - before_attempts
            entry['duration_ms'] = round((perf_counter()-started)*1000, 2)
            record_call(entry)
        # Pydantic ValidationError is corrected inside _request before returning.

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
