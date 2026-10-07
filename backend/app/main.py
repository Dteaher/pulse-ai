import logging
import json
from fastapi import FastAPI, Depends
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from lxml import etree
from pydantic import ValidationError
from .config import Settings
from .models import Issue
from .services.llm.base import LLMProvider, ProviderError
from .services.llm.factory import get_llm_provider
from .examples import TEXTS
from .diagnostics import PipelineFailure
from time import perf_counter
from uuid import uuid4
from .telemetry import request_id, operation, RequestLogContext, profile, PerformanceProfile
from .performance_cache import ResultCache, implementation_version
from .security import RequestGuard
from contextlib import asynccontextmanager
from collections import OrderedDict
import hashlib
import re

logger = logging.getLogger('pulse')
validation_logger = logging.getLogger('pulse.validation')
validation_logger.setLevel(logging.INFO)
if not validation_logger.handlers:
    handler = logging.StreamHandler()
    handler.addFilter(RequestLogContext())
    handler.setFormatter(logging.Formatter('%(message)s request_id=%(request_id)s operation=%(operation)s'))
    validation_logger.addHandler(handler)
    llm_logger = logging.getLogger('pulse.llm')
    llm_logger.setLevel(logging.INFO)
    if not llm_logger.handlers:
        llm_logger.addHandler(handler)


def create_app(settings=None, provider: LLMProvider | None = None):
    s = settings or Settings()
    if s.app_env == 'production' and not s.pulse_access_token:
        raise ValueError('Production requires PULSE_ACCESS_TOKEN. Configure it in the server environment.')
    if s.app_env == 'production' and (len(s.pulse_access_token) < 32 or not s.api_limits_enabled):
        raise ValueError('Production requires a workspace code of at least 32 characters and API_LIMITS_ENABLED=true.')
    validation_logger.setLevel(logging.DEBUG if s.app_env == 'development' else logging.INFO)
    @asynccontextmanager
    async def lifespan(app):
        yield
        await app.state.provider.close()
        for retired in app.state.retired_providers:
            await retired.close()
    app = FastAPI(title='PULSE — AI Business Process Engineer', version='0.2.0', lifespan=lifespan)
    app.add_middleware(RequestGuard, settings=s)
    app.state.provider = provider or get_llm_provider(s)
    app.state.settings_key = hashlib.sha256(s.model_dump_json().encode()).hexdigest()
    app.state.retired_providers = []
    app.state.provider_users = {}
    app.state.performance_profiles = OrderedDict()
    result_cache = ResultCache(s.performance_cache_entries, s.performance_cache_ttl)

    async def current_connection():
        # Snapshot once per request: retries must use the same provider/model.
        # Explicit settings/provider injections stay fixed for tests and embedding.
        if settings is not None or provider is not None:
            active = s
        else:
            try:
                active = Settings()
            except ValidationError as exc:
                # Settings errors can contain secret input; never return their details.
                raise ProviderError('Некорректные настройки .env. Проверьте типы и допустимые значения параметров LLM.') from exc
            snapshot = hashlib.sha256(active.model_dump_json().encode()).hexdigest()
            if snapshot != app.state.settings_key:
                # An env reload must not close the transport of an in-flight request.
                replacement = get_llm_provider(active)
                app.state.retired_providers.append(app.state.provider)
                app.state.provider = replacement
                app.state.settings_key = snapshot
                result_cache.clear()
        owner = app.state.provider
        key = id(owner)
        app.state.provider_users[key] = app.state.provider_users.get(key, 0) + 1
        try:
            yield active, owner.new_request()
        finally:
            app.state.provider_users[key] -= 1
            for retired in list(app.state.retired_providers):
                if not app.state.provider_users.get(id(retired), 0):
                    app.state.retired_providers.remove(retired)
                    app.state.provider_users.pop(id(retired), None)
                    await retired.close()

    def current_provider(connection=Depends(current_connection)):
        return connection[1]

    @app.middleware('http')
    async def request_context(request, call_next):
        request.state.request_id = uuid4().hex
        started = perf_counter()
        request_token = request_id.set(request.state.request_id)
        operation_token = operation.set(request.url.path)
        measured = PerformanceProfile()
        performance_token = profile.set(measured)
        try:
            response = await call_next(request)
            validation_logger.info('http_result status=%s total_ms=%.1f', response.status_code, (perf_counter()-started)*1000)
        finally:
            report = measured.report()
            app.state.performance_profiles[request.state.request_id] = report
            while len(app.state.performance_profiles) > 64:
                app.state.performance_profiles.popitem(last=False)
            validation_logger.info('request_performance %s', json.dumps(report))
            profile.reset(performance_token)
            request_id.reset(request_token)
            operation.reset(operation_token)
        response.headers['X-Request-ID'] = request.state.request_id
        return response

    async def cached(request, operation_name, body, connection, compute):
        active, selected = connection
        scope = request.headers.get('X-Pulse-Session', '')
        if not re.fullmatch(r'[a-zA-Z0-9_-]{16,128}', scope):
            scope = ''  # No shared anonymous cache; API clients may opt out.
        if not active.llm_performance_enabled:
            scope = ''
        fingerprint = hashlib.sha256(active.model_dump_json().encode()).hexdigest()
        began = perf_counter()
        result = await result_cache.get_or_compute(scope, operation_name, body.model_dump(mode='json'),
            implementation_version() + fingerprint, compute)
        measured = profile.get()
        if measured:
            if operation_name in ('modify', 'audit', 'clarify'):
                measured.add({'audit': 'doctor_ms', 'modify': 'modify_ms', 'clarify': 'clarify_generation_ms'}[operation_name], (perf_counter()-began)*1000)
            if result.get('process'):
                measured.stages['process_definition_size'] = len(result['process'].model_dump_json())
            for key in (() if measured.cache_hit or measured.deduplicated else ('ambiguity_analysis_ms', 'combined_preparation_ms')):
                if key in result.get('timings', {}):
                    measured.add(key, result['timings'][key])
            if measured.cache_hit or measured.deduplicated:
                result['cache'] = {'hit': measured.cache_hit, 'deduplicated': measured.deduplicated}
                if result.get('timings'):
                    result['timings'] = {key: 0 for key in result['timings']}
                if result.get('metadata'):
                    result['metadata'] = result['metadata'].model_copy(update={'attempts': 0})
        encode_started = perf_counter()
        encoded = jsonable_encoder(result)
        if measured:
            measured.add('serialization_ms', (perf_counter()-encode_started)*1000)
        encode_started = perf_counter()
        response = JSONResponse(content=encoded)
        if measured:
            measured.add('serialization_ms', (perf_counter()-encode_started)*1000)
        return response

    @app.exception_handler(PipelineFailure)
    async def pipeline_error(request, exc):
        return JSONResponse(status_code=422, content={'detail': str(exc),
            'human_message': str(exc), 'diagnostics': [i.model_dump(exclude_none=True) for i in exc.diagnostics],
            'request_id': request.state.request_id})

    @app.exception_handler(ProviderError)
    async def provider_error(request, exc):
        diagnostics = getattr(exc, 'diagnostics', []) or [Issue(
            code='OUTPUT_TRUNCATED' if exc.reason=='output_truncated' else 'LLM_' + exc.reason.upper(), severity='error',
            type='MODEL_ERROR' if exc.reason == 'invalid_response' else 'INTEROPERABILITY_ERROR',
            category='llm_response' if exc.reason == 'invalid_response' else 'llm_transport',
            message=str(exc), suggested_fix='Проверьте доступность модели и повторите запрос.')]
        return JSONResponse(status_code=502, content={'detail': str(exc), 'human_message': str(exc),
            'diagnostics': [i.model_dump(exclude_none=True) for i in diagnostics],
            'request_id': request.state.request_id})

    @app.exception_handler(ValueError)
    @app.exception_handler(etree.LxmlError)
    async def invalid_process(request, exc):
        safe = str(exc) if str(exc).startswith(('Критичные вопросы', 'Достигнут лимит', 'Ответьте на предложенные', 'Принимать как допущения', 'Для уточнения изменения', 'Для изменения нужна', 'Нет процесса', 'Сначала ответьте')) else 'Не удалось обработать процесс. Проверьте ответы, структуру и связи элементов.'
        logger.warning('invalid_process request_id=%s exception=%s', request.state.request_id, type(exc).__name__)
        if request.url.path.endswith('/import'):
            safe = 'Не удалось импортировать BPMN в AI-слой. Проверьте XML и поддерживаемые элементы; исходный файл сохранён.'
        diagnostic = Issue(code='INVALID_PROCESS_INPUT', severity='error',
            type='INTEROPERABILITY_ERROR' if request.url.path.endswith('/import') else 'MODEL_ERROR',
            message=safe)
        return JSONResponse(status_code=422, content={'detail': safe, 'human_message': safe,
            'diagnostics': [i.model_dump(exclude_none=True) for i in getattr(exc, 'diagnostics', [diagnostic])]})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        message = 'Проверьте входные данные: описание и структуру процесса.'
        issue = Issue(code='REQUEST_SCHEMA_INVALID', severity='error', type='MODEL_ERROR', category='request_schema', message=message)
        return JSONResponse(status_code=422, content={'detail': message, 'human_message': message,
            'diagnostics': [issue.model_dump(exclude_none=True)]})

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        logger.error('Unhandled %s', type(exc).__name__)
        message = 'Не удалось обработать запрос. Повторите попытку.'
        issue = Issue(code='INTERNAL_PIPELINE_ERROR', severity='error', type='INTEROPERABILITY_ERROR', category='application', message=message)
        return JSONResponse(status_code=500, content={'detail': message, 'human_message': message,
            'diagnostics': [issue.model_dump(exclude_none=True)]})

    @app.get('/api/health')
    def health(connection=Depends(current_connection)):
        active, selected = connection
        configured = selected.configured
        missing = selected.missing_settings() if hasattr(selected, 'missing_settings') else []
        return {'status': 'ok', 'provider': selected.name, 'configured': configured, 'missing_settings': missing,
                'access_required': bool(s.pulse_access_token)}

    @app.get('/api/access/check')
    def access_check():
        return {'status': 'ok'}

    @app.get('/api/llm/info')
    def llm_info(selected: LLMProvider = Depends(current_provider)):
        return selected.connection_info

    @app.get('/api/examples')
    def examples():
        return [{'name': name, 'text': text} for name, text in zip(['Обработка заявки', 'Подключение к электросети', 'Описание с неоднозначностями'], TEXTS)]

    from .process_routes import register_process_routes
    register_process_routes(app, current_connection, cached)
    return app


app = create_app()
