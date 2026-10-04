import logging
import json
from fastapi import FastAPI, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from lxml import etree
from pydantic import ValidationError
from .config import Settings
from .models import GenerateRequest, ClarifyRequest, ModifyRequest, ProcessRequest, ImportRequest, Issue
from .services.llm.base import LLMProvider, ProviderError
from .services.llm.factory import get_llm_provider
from .pipeline import generate_valid, audit_rules
from .validator import validate_process
from .bpmn import build_bpmn, import_process
from .xml_validation import validate_document, validate_xsd
from .examples import TEXTS
from .changes import preserve_ids, changes_between
from .preflight import prepare, questions_result, extraction_context, attach_assumptions, accepted_assumption, focused_correction
from .diagnostics import PipelineFailure, classify, build_failure
from time import perf_counter
from uuid import uuid4
from .telemetry import request_id, operation, RequestLogContext, profile, PerformanceProfile
from .performance_cache import ResultCache, implementation_version
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


async def _resolved(process):
    return process


def create_app(settings=None, provider: LLMProvider | None = None):
    s = settings or Settings()
    validation_logger.setLevel(logging.DEBUG if s.app_env == 'development' else logging.INFO)
    @asynccontextmanager
    async def lifespan(app):
        yield
        await app.state.provider.close()
        for retired in app.state.retired_providers:
            await retired.close()
    app = FastAPI(title='PULSE', version='0.1.0', lifespan=lifespan)
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
        return {'status': 'ok', 'provider': selected.name, 'configured': configured, 'missing_settings': missing}

    @app.get('/api/llm/info')
    def llm_info(selected: LLMProvider = Depends(current_provider)):
        return selected.connection_info

    @app.get('/api/examples')
    def examples():
        return [{'name': name, 'text': text} for name, text in zip(['Обработка заявки', 'Подключение к электросети', 'Описание с неоднозначностями'], TEXTS)]

    @app.post('/api/process/generate')
    async def generate_endpoint(body: GenerateRequest, request: Request, connection=Depends(current_connection)):
        return await cached(request, 'generate', body, connection, lambda: generate(body, connection))

    async def generate(body, connection):
        active, selected = connection
        started = perf_counter()
        state, timings, prepared = await prepare(selected, body.text)
        if state.analysis.ambiguities:
            return questions_result(state, timings, selected, limit=s.max_clarification_rounds)
        async def call(correction):
            if prepared is not None and not correction:
                return attach_assumptions(prepared, state)
            if correction:
                return attach_assumptions(await selected.parse_process(body.text, focused_correction(correction, state)), state)
            return attach_assumptions(await selected.parse_process(body.text, extraction_context(state) + '\n' + correction), state)
        result = await generate_valid(call, source_text=body.text, coverage_answers=state.answers, provider=selected, development=active.app_env == 'development', max_retries=min(active.llm_max_retries, active.llm_graph_max_retries))
        result['timings'].update(timings)
        result['timings']['total_ms'] = round((perf_counter() - started) * 1000, 2)
        return {**result, 'metadata': selected.metadata, 'clarification_round': 0, 'clarification_limit_reached': False, 'accepted_assumptions': []}

    @app.post('/api/process/clarify')
    async def clarify_endpoint(body: ClarifyRequest, request: Request, connection=Depends(current_connection)):
        return await cached(request, 'clarify', body, connection, lambda: clarify(body, connection))

    async def clarify(body, connection):
        started = perf_counter()
        active, selected = connection
        if body.preflight is not None:
            state = body.preflight
            questions = {q.id: q for q in state.analysis.ambiguities}
            accepted = set(body.accepted_ambiguity_ids)
            if body.continue_with_draft:
                accepted = {q.id for q in questions.values() if q.severity == 'warning'}
            if not accepted.issubset(questions) or any(questions[k].severity == 'critical' for k in accepted):
                raise ValueError('Критичные вопросы нельзя принять как допущение.')
            if set(body.answers) != set(questions) - accepted or any(not a.strip() or len(a) > 2000 for a in body.answers.values()):
                raise ValueError('Ответьте на предложенные вопросы.')
            if body.clarification_round >= active.max_clarification_rounds:
                raise ValueError('Достигнут лимит уточнений. Уточните исходное описание.')
            if body.operation == 'modify' and (body.base_process is None or not body.instruction.strip()):
                raise ValueError('Для изменения нужна исходная версия и команда.')
            answers = {**state.answers, **{k: {'question': questions[k].question, 'answer': v}
                                          for k, v in body.answers.items()}}
            # Stored answers are textual, including the original question for later rounds.
            answers = {k: json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v for k, v in answers.items()}
            assumptions = state.analysis.assumptions + [accepted_assumption(questions[k]) for k in accepted]
            context = {'operation': body.operation, 'instruction': body.instruction,
                       'base_process': body.base_process.model_dump() if body.base_process else None,
                       'accepted_assumptions': [a.model_dump() for a in assumptions]}
            state, timings, prepared = await prepare(selected, state.original_text, context=context, answers=answers, assumptions=assumptions)
            round_number = body.clarification_round + 1
            state.analysis.ambiguities = [q for q in state.analysis.ambiguities if q.id not in accepted]
            if state.analysis.ambiguities:
                return questions_result(state, timings, selected, round_number=round_number, limit=active.max_clarification_rounds)
            async def resolved_call(correction):
                correction = focused_correction(correction, state) if correction else ''
                context_text = extraction_context(state) + '\n' + correction
                if body.operation == 'modify':
                    candidate = prepared if prepared is not None and not correction else (await selected.modify_process(body.base_process, body.instruction, correction or context_text)).process
                    candidate = preserve_ids(body.base_process, candidate)
                    from .quality import check_membership
                    check_membership(body.base_process, candidate, body.instruction)
                else:
                    candidate = prepared if prepared is not None and not correction else await selected.parse_process(state.original_text, correction or context_text)
                return attach_assumptions(candidate, state)
            result = await generate_valid(resolved_call, source_text=state.original_text if body.operation != 'modify' else '', coverage_answers=state.answers, semantic_check=body.operation == 'modify', provider=selected, max_retries=min(active.llm_max_retries, active.llm_graph_max_retries))
            result['timings'].update(timings)
            result['timings']['total_ms'] = round((perf_counter()-started)*1000, 2)
            return {**result, 'metadata': selected.metadata, 'clarification_round': round_number,
                    'clarification_limit_reached': False, 'accepted_assumptions': body.accepted_assumptions,
                    'changes': changes_between(body.base_process, result['process']).changes if body.operation == 'modify' and result['xml'] else []}
        if body.process is None:
            raise ValueError('Нет процесса или контекста уточнения.')
        expected = {a.id for a in body.process.ambiguities}
        limit = active.max_clarification_rounds
        base = body.base_process or body.process
        if body.operation == 'modify' and (body.base_process is None or not body.instruction.strip()):
            raise ValueError('Для уточнения изменения нужна исходная версия и команда.')
        questions = {a.id: a for a in body.process.ambiguities}
        accepted = set(body.accepted_ambiguity_ids)
        if body.continue_with_draft:
            if any(a.severity == 'critical' for a in questions.values()):
                raise ValueError('Критичные вопросы нельзя принять как допущение. Уточните описание.')
            accepted = set(questions)
        if (len(accepted) != len(body.accepted_ambiguity_ids) and not body.continue_with_draft) or not accepted.issubset(expected) or any(questions[key].severity != 'warning' for key in accepted):
            raise ValueError('Принимать как допущения можно только предложенные некритичные вопросы.')
        assumptions = body.accepted_assumptions + [a for a in body.process.ambiguities if a.id in accepted]
        assumptions = list({(a.id, a.question): a for a in assumptions}.values())
        if len(assumptions) > 30:
            raise ValueError('Достигнут лимит сохранённых допущений. Уточните исходное описание процесса.')
        draft = body.process.model_copy(deep=True)
        draft.ambiguities = [a for a in draft.ambiguities if a.id not in accepted]
        draft.assumptions = list({a.id: a for a in draft.assumptions + [accepted_assumption(q) for q in assumptions]}.values())
        if not draft.ambiguities and (not body.answers or body.continue_with_draft):
            result = await generate_valid(lambda _: _resolved(draft), semantic_check=body.operation == 'modify')
            return {**result, 'changes': changes_between(base, draft).changes if body.operation == 'modify' else [], 'clarification_round': body.clarification_round, 'clarification_limit_reached': False, 'accepted_assumptions': assumptions, 'metadata': None}
        if body.clarification_round >= limit:
            raise ValueError('Достигнут лимит уточнений. Продолжите с текущим черновиком или измените описание.')
        if not body.answers or set(body.answers) != expected - accepted or any(not a.strip() or len(a) > 2000 for a in body.answers.values()):
            raise ValueError('Ответьте на предложенные вопросы.')
        context = json.dumps({'original_text': body.original_text or body.process.description, 'operation': body.operation,
                              'instruction': body.instruction, 'accepted_assumptions': [a.model_dump() for a in assumptions], 'base_process': base.model_dump() if body.operation == 'modify' else None}, ensure_ascii=False)
        async def call(correction):
            candidate = (await selected.clarify_process(draft, body.answers, 'Контекст уточнения:\n' + context + '\n' + correction)).process
            accepted_signatures = {(a.id, a.question, a.assumption) for a in assumptions}
            candidate.ambiguities = [a for a in candidate.ambiguities if a.severity == 'critical' or (a.id, a.question, a.assumption) not in accepted_signatures]
            candidate = preserve_ids(base, candidate)
            candidate.assumptions = list({a.id: a for a in draft.assumptions + candidate.assumptions}.values())
            if body.operation == 'modify':
                from .quality import check_membership
                check_membership(base, candidate, body.instruction)
            return candidate
        try:
            result = await generate_valid(call, source_text=(body.original_text or body.process.description) if body.operation != 'modify' else '', coverage_answers=body.answers, semantic_check=body.operation == 'modify', provider=selected, development=active.app_env == 'development', max_retries=min(active.llm_max_retries, active.llm_graph_max_retries))
        except ProviderError as exc:
            if body.operation == 'modify':
                failure = ProviderError('Изменение не удалось применить. Текущая версия процесса сохранена. ' + str(exc))
                failure.diagnostics = getattr(exc, 'diagnostics', [])
                raise failure from exc
            raise
        round_number = body.clarification_round + 1
        return {**result, 'metadata': selected.metadata, 'changes': changes_between(base, result['process']).changes if body.operation == 'modify' and result['xml'] else [], 'accepted_assumptions': assumptions,
                'clarification_round': round_number, 'clarification_limit_reached': round_number >= limit and bool(result['ambiguities'])}

    @app.post('/api/process/modify')
    async def modify_endpoint(body: ModifyRequest, request: Request, connection=Depends(current_connection)):
        return await cached(request, 'modify', body, connection, lambda: modify(body, connection))

    async def modify(body, connection):
        active, selected = connection
        started = perf_counter()
        errors = [i for i in validate_process(body.process) if i.severity == 'error']
        if errors:
            raise PipelineFailure('Исходная схема содержит ошибки в связях. Исправьте её перед изменением.', errors)
        if active.deterministic_modify_enabled:
            from .commands import rename_command
            candidate = rename_command(body.process, body.command)
            if candidate is not None:
                result = await generate_valid(lambda _: _resolved(candidate), semantic_check=True, max_retries=0)
                return {**result, 'changes': changes_between(body.process, result['process']).changes,
                        'metadata': None, 'accepted_assumptions': body.accepted_assumptions}
        state, timings, prepared = await prepare(selected, body.command, context={'operation': 'modify',
            'base_process': body.process.model_dump()}, assumptions=body.process.assumptions)
        if state.analysis.ambiguities:
            return questions_result(state, timings, selected, limit=s.max_clarification_rounds)
        accepted = [a for a in body.accepted_assumptions if a.id not in state.analysis.superseded_assumption_ids]
        async def call(correction):
            correction = focused_correction(correction, state) if correction else ''
            context = extraction_context(state) + '\n' + ('Принятые допущения: ' + json.dumps([a.model_dump() for a in accepted], ensure_ascii=False) + '\n' if accepted else '')
            candidate = prepared if prepared is not None and not correction else (await selected.modify_process(body.process, body.command, correction or context)).process
            candidate = attach_assumptions(preserve_ids(body.process, candidate), state)
            from .quality import check_membership
            check_membership(body.process, candidate, body.command)
            return candidate
        try:
            result = await generate_valid(call, semantic_check=True, provider=selected, development=active.app_env == 'development', max_retries=min(active.llm_max_retries, active.llm_graph_max_retries))
        except ProviderError as exc:
            failure = ProviderError('Изменение не удалось применить. Текущая версия процесса сохранена. ' + str(exc))
            failure.diagnostics = getattr(exc, 'diagnostics', [])
            raise failure from exc
        result['timings'].update(timings)
        result['timings']['total_ms'] = round((perf_counter() - started) * 1000, 2)
        return {**result, 'changes': changes_between(body.process, result['process']).changes if result['xml'] else [], 'metadata': selected.metadata, 'accepted_assumptions': accepted}

    @app.post('/api/process/bpmn')
    def bpmn(body: ProcessRequest):
        if any(a.severity == 'critical' for a in body.process.ambiguities):
            raise ValueError('Сначала ответьте на критичные уточнения.')
        issues = validate_process(body.process)
        if any(i.severity == 'error' for i in issues):
            raise PipelineFailure('В схеме есть некорректные элементы или связи.', issues)
        try:
            return {'xml': build_bpmn(body.process)}
        except (ValueError, etree.LxmlError):
            logger.exception('bpmn_export_failed')
            raise build_failure() from None

    @app.post('/api/process/import')
    def import_bpmn(body: ImportRequest):
        return {'process': import_process(body.xml, body.previous)}

    @app.post('/api/process/audit')
    async def audit_endpoint(body: ImportRequest, request: Request, connection=Depends(current_connection)):
        return await cached(request, 'audit', body, connection, lambda: audit(body, connection[1]))

    async def audit(body, selected):
        xsd_issues = validate_xsd(body.xml)
        xml_ok = not xsd_issues
        document_issues = xsd_issues or validate_document(body.xml, check_xsd=False)
        try:
            p = import_process(body.xml, body.previous)
        except (ValueError, ValidationError, etree.LxmlError) as exc:
            document_issues += getattr(exc, 'diagnostics', [])
            if document_issues:
                return {'technical': {'BPMN XML / XSD': xml_ok, 'Граф / связи / события / шлюзы': False, 'Участники': False},
                        'issues': document_issues, 'llm_audit': False,
                        'notice': 'Сначала исправьте XML и ссылки элементов. Бизнес-аудит не выполнялся.', 'metadata': None}
            raise
        graph_issues = validate_process(p)
        if not any(i.severity == 'error' for i in graph_issues):
            from .semantics import validate_semantics
            graph_issues += validate_semantics(p)
        issues = document_issues + graph_issues + audit_rules(p)
        business_available = False
        notice = ''
        try:
            if any(i.severity == 'error' for i in graph_issues + document_issues):
                raise ProviderError('Сначала исправьте технические ошибки схемы.')
            report = await selected.audit_process(p)
            valid_ids = {n.id for n in p.nodes}
            for issue in report.issues:
                issue.node_ids = [i for i in issue.node_ids if i in valid_ids]
                issue.origin = 'llm'
                issue.source = 'BUSINESS_LOGIC'
                issue.type = 'CLARIFICATION_REQUIRED' if issue.severity == 'clarification' else 'BUSINESS_WARNING'
                if issue.severity == 'error': issue.severity = 'warning'
                issue.category = 'business_logic'
                issue.spec_section = None  # Model opinions are not normative spec evidence.
            issues.extend(report.issues)
            business_available = selected.business_audit_available
        except (ProviderError, ValueError, ValidationError):
            notice = 'Сначала исправьте технические ошибки схемы; бизнес-аудит не выполнялся.' if any(i.severity == 'error' for i in graph_issues + document_issues) else 'Аудит модели недоступен. Показаны результаты программных правил.'
        return {'technical': {'BPMN XML / XSD': xml_ok, 'Граф / связи / события / шлюзы': not any(i.severity == 'error' for i in graph_issues + document_issues),
                              'Участники': not any('участник' in i.message.lower() for i in graph_issues)},
                'issues': [classify(i) for i in issues], 'llm_audit': business_available, 'notice': notice,
                'metadata': selected.metadata if business_available else None}

    return app


app = create_app()
