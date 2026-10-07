import logging
import json
from fastapi import Depends, Request
from lxml import etree
from pydantic import ValidationError
from .models import GenerateRequest, ClarifyRequest, ModifyRequest, ProcessRequest, ImportRequest
from .services.llm.base import ProviderError
from .pipeline import generate_valid, audit_rules
from .validator import validate_process
from .bpmn import build_bpmn, import_process
from .xml_validation import validate_document, validate_xsd
from .changes import preserve_ids, changes_between
from .preflight import prepare, questions_result, extraction_context, attach_assumptions, accepted_assumption, focused_correction
from .diagnostics import PipelineFailure, classify, build_failure
from time import perf_counter

logger = logging.getLogger('pulse')

async def _resolved(process):
    return process

def register_process_routes(app, current_connection, cached):
    @app.post('/api/process/generate')
    async def generate_endpoint(body: GenerateRequest, request: Request, connection=Depends(current_connection)):
        return await cached(request, 'generate', body, connection, lambda: generate(body, connection))

    async def generate(body, connection):
        active, selected = connection
        started = perf_counter()
        state, timings, prepared = await prepare(selected, body.text)
        if state.analysis.ambiguities:
            return questions_result(state, timings, selected, limit=active.max_clarification_rounds)
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
            return questions_result(state, timings, selected, limit=active.max_clarification_rounds)
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

