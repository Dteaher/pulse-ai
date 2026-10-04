import json
import logging
from time import perf_counter
from pydantic import ValidationError
from .services.llm.base import ProviderError
from .models import Issue
from .diagnostics import classify, build_failure
from .xml_validation import validate_document, validate_xsd
from lxml import etree
from .validator import validate_process
from .bpmn import build_bpmn
from .semantics import validate_semantics
from .quality import validate_labels
from .notation import polish_process
from .telemetry import profile, add_stage
from .repair import repair_context, apply_repair, assert_preservation
from .diagnostics import PipelineFailure

logger = logging.getLogger('pulse.validation')


async def generate_valid(call, *, semantic_check=False, provider=None, development=False, correction='', max_retries=1, source_text='', coverage_answers=None):
    started = perf_counter()
    last_reason = ''
    timings = {key: 0.0 for key in ('llm_parse_ms', 'normalization_ms', 'semantic_validation_ms', 'bpmn_build_ms', 'xsd_validation_ms', 'post_validation_ms')}
    for attempt in range(max_retries + 1):
        errors = []
        try:
            parse_started = perf_counter()
            process = await call(correction)
            timings['llm_parse_ms'] += round((perf_counter() - parse_started) * 1000, 2)
            # A business question is not an invalid graph. Never repair it by guessing.
            if any(a.severity == 'critical' for a in process.ambiguities):
                return {'process': process, 'ambiguities': process.ambiguities, 'xml': None,
                        'attempts': attempt + 1, 'semantic_warnings': [], 'timings': timings}
            normalized = perf_counter()
            process = polish_process(process)
            timings['normalization_ms'] += round((perf_counter() - normalized) * 1000, 2)
            add_stage('normalization_ms', normalized)
            priority = ['missing_branch', 'unclear_condition', 'unclear_parallelism', 'unclear_sequence', 'unclear_participant', 'unclear_result', 'unclear_end', 'other']
            process.ambiguities = sorted(process.ambiguities, key=lambda a: (a.severity != 'critical', priority.index(a.type)))
            validation_started = perf_counter()
            issues = validate_process(process)
            add_stage('process_validation_ms', validation_started)
            if process.pools:
                issues += validate_labels(process)
            errors = [i for i in issues if i.severity == 'error']
            semantic_issues = validate_semantics(process) if not errors else []
            issues += semantic_issues
            issues = [classify(i) for i in issues]
            errors = [i for i in issues if i.severity == 'error']
            add_stage('semantic_validation_ms', validation_started)
            current = profile.get()
            if current:
                current.validation_error_count += len(errors)
                current.validation_codes.extend(i.code for i in errors)
            timings['semantic_validation_ms'] += round((perf_counter() - validation_started) * 1000, 2)
            if not errors and source_text and provider is not None and provider.business_coverage_enabled:
                from .business_coverage import coverage_issues
                coverage_started = perf_counter()
                report = await provider.check_business_coverage(process, source_text, coverage_answers)
                coverage_errors = coverage_issues(report, process, source_text, coverage_answers)
                issues += coverage_errors
                errors += coverage_errors
                add_stage('business_coverage_ms', coverage_started)
                if current:
                    current.validation_error_count += len(coverage_errors)
                    current.validation_codes.extend(i.code for i in coverage_errors)
            if development:
                logger.info('process_validation duration_ms=%.1f', (perf_counter() - validation_started) * 1000)
                metadata = provider.metadata if provider else None
                logger.info('validation attempt=%s corrective_retry=%s provider=%s model=%s fallback=%s errors=%s',
                            attempt + 1, attempt, metadata.provider_used if metadata else 'unknown',
                            metadata.model_used if metadata else 'unknown', metadata.fallback_used if metadata else False,
                            json.dumps([i.model_dump(exclude_none=True) for i in errors], ensure_ascii=False))
            if errors:
                # A patch is optional and transactional: a candidate traverses
                # every standard check and the complete preservation audit.
                context = repair_context(process, errors) if provider is not None and provider.patch_corrective_enabled and provider.supports_structural_repair and attempt < max_retries else None
                if context is not None:
                    if current:
                        current.patch_corrective_count += 1
                    try:
                        patch_started = perf_counter()
                        patch = await provider.repair_process(context)
                        timings['llm_parse_ms'] += round((perf_counter() - patch_started) * 1000, 2)
                        if patch is not None:
                            candidate = apply_repair(process, errors, patch)
                            async def repaired(_):
                                return candidate
                            checked_result = await generate_valid(repaired, semantic_check=semantic_check, provider=provider, max_retries=0, source_text=source_text, coverage_answers=coverage_answers)
                            assert_preservation(process, checked_result['process'], patch)
                            if current:
                                current.patch_success_count += 1
                            for key, duration in timings.items():
                                checked_result['timings'][key] = round(checked_result['timings'].get(key, 0) + duration, 2)
                            checked_result['attempts'] = attempt + 1
                            checked_result['timings']['total_ms'] = round((perf_counter() - started) * 1000, 2)
                            return checked_result
                        if current:
                            current.patch_failure_count += 1
                    except (ValueError, ValidationError, PipelineFailure) as exc:
                        if current:
                            current.patch_rejected_quality_count += 1
                        logger.info('structural_patch_rejected reason=%s', type(exc).__name__)
                    except ProviderError as exc:
                        if current:
                            if exc.reason in ('graph_validation', 'semantic_validation'):
                                current.patch_rejected_quality_count += 1
                            else:
                                current.patch_failure_count += 1
                        logger.info('structural_patch_failed reason=provider_or_validation')
                if current and attempt < max_retries:
                    current.graph_corrective_count += 1
                last_reason = 'Причина: ' + ('некорректное ветвление или исходы процесса.' if semantic_issues or any('шлюз' in i.message.casefold() or 'ветк' in i.message.casefold() for i in errors) else 'некорректные связи или события процесса.')
                if any(i.code == 'SAME_POOL_MESSAGE_FLOW' for i in errors):
                    last_reason = 'Причина: сообщения ошибочно соединяют дорожки одной организации; нужны обычные переходы.'
                elif any(i.category == 'business_coverage' for i in errors):
                    last_reason = 'Причина: схема не полностью сохраняет действия или бизнес-правила исходного описания.'
                elif any(i.code == 'CROSS_POOL_SEQUENCE_FLOW' for i in errors):
                    last_reason = 'Причина: обычный переход пересекает границу участников; нужна связь сообщением.'
                elif any(i.code in ('ORPHAN_NODE', 'UNREACHABLE_NODE', 'DEAD_END', 'NO_PATH_TO_END') for i in errors):
                    last_reason = 'Причина: в одном из процессов есть действия без связного пути от начала к завершению.'
                feedback_fields = {'code', 'message', 'node_id', 'flow_id', 'gateway_id', 'process_id', 'node_ids', 'source_pool', 'target_pool', 'suggested_fix'}
                correction = json.dumps({'policy': 'Исправь только указанные ошибки и подтверждённые пропуски из исходного описания. Сохрани остальные действия, бизнес-правила, участников и ответы. Если требуется неизвестный бизнес-факт, верни critical ambiguity вместо угадывания.', 'errors': [i.model_dump(include=feedback_fields, exclude_defaults=True, exclude_none=True) for i in errors], 'previous_result': process.model_dump(exclude_defaults=True, exclude_none=True), 'business_context': {'source_text': source_text, 'answers': coverage_answers or {}}}, ensure_ascii=False, separators=(',', ':'))
                continue
            build_started = perf_counter()
            try:
                xml = build_bpmn(process)
            except (ValueError, etree.LxmlError):
                logger.exception('bpmn_build_failed attempt=%s', attempt + 1)
                raise build_failure() from None
            timings['bpmn_build_ms'] += round((perf_counter() - build_started) * 1000, 2)
            add_stage('bpmn_build_ms', build_started)
            checked = perf_counter()
            post_issues = validate_xsd(xml)
            timings['xsd_validation_ms'] += round((perf_counter() - checked) * 1000, 2)
            checked = perf_counter()
            if not post_issues:
                post_issues = validate_document(xml, complete_di=True, check_xsd=False)
            timings['post_validation_ms'] += round((perf_counter() - checked) * 1000, 2)
            if any(i.severity == 'error' for i in post_issues):
                raise PipelineFailure('Проверка BPMN не пройдена. Текущая версия сохранена.', post_issues)
            issues += [classify(i) for i in post_issues]
            if development:
                logger.info('bpmn_build duration_ms=%.1f', (perf_counter() - build_started) * 1000)
            if development:
                logger.info('pipeline_timings %s', json.dumps(timings))
            return {'process': process, 'ambiguities': process.ambiguities, 'xml': xml, 'attempts': attempt + 1,
                    'semantic_warnings': [i for i in issues if i.severity != 'error'],
                    'diagnostics': issues, 'timings': {'ambiguity_analysis_ms': 0, **timings, 'total_ms': round((perf_counter()-started)*1000, 2)}}
        except (ValidationError, ValueError) as exc:
            if isinstance(exc, ValidationError):
                details = [{'code': 'SCHEMA_VALIDATION', 'location': list(e['loc']), 'type': e['type']} for e in exc.errors(include_input=False)]
            else:
                details = [{'code': 'INVALID_PROCESS_GRAPH', 'message': str(exc)[:1500]}]
            correction = json.dumps({'errors': details}, ensure_ascii=False)
            last_reason = 'Причина: ответ модели не соответствует структуре процесса.'
            if development:
                logger.info('validation attempt=%s corrective_retry=%s errors=%s', attempt + 1, attempt, correction)
    if provider is not None and provider.use_validation_fallback():
        result = await generate_valid(call, semantic_check=semantic_check, provider=provider, development=development, correction=correction, max_retries=max_retries, source_text=source_text, coverage_answers=coverage_answers)
        result['attempts'] += max_retries + 1
        for key, duration in timings.items():
            result['timings'][key] = round(result['timings'].get(key, 0) + duration, 2)
        result['timings']['total_ms'] = round((perf_counter()-started)*1000, 2)
        return result
    failure = ProviderError('Ответ модели не прошёл проверку после ограниченного числа исправлений. ' + last_reason + ' Уточните описание и повторите запрос.', reason='semantic_validation' if semantic_check else 'graph_validation')
    failure.diagnostics = [classify(i) for i in errors] if errors else [Issue(code='MODEL_SCHEMA_INVALID', severity='error', type='MODEL_ERROR', message='Ответ модели не соответствует внутренней схеме процесса.')]
    raise failure


def audit_rules(p):
    issues = []
    for n in p.nodes:
        if n.type == 'end_event' and n.name.lower() in ('конец', 'end', 'завершение'):
            issues.append(Issue(severity='warning', message='Назовите результат конечной ветки, чтобы аналитик видел её исход.', node_ids=[n.id]))
    # Find cyclic SCCs without token simulation. A legal loop with an exit is
    # allowed; its missing retry bound is a business risk, never a graph error.
    graph = {n.id: [] for n in p.nodes}
    for f in p.flows:
        if f.source in graph and f.target in graph:
            graph[f.source].append(f.target)
    visited, stack, active, positions, lows = set(), [], set(), {}, {}
    def visit(key):
        positions[key] = lows[key] = len(positions)
        stack.append(key); active.add(key); visited.add(key)
        for target in graph[key]:
            if target not in visited:
                visit(target); lows[key] = min(lows[key], lows[target])
            elif target in active:
                lows[key] = min(lows[key], positions[target])
        if lows[key] == positions[key]:
            group = []
            while True:
                item = stack.pop(); active.remove(item); group.append(item)
                if item == key: break
            if len(group) > 1 or key in graph[key]:
                issues.append(Issue(code='UNBOUNDED_REWORK_LOOP', severity='warning',
                    source='BUSINESS_LOGIC', type='BUSINESS_WARNING', node_ids=group,
                    message='В схеме есть цикл повторной обработки. Предел повторов не представлен в текущем subset.',
                    suggested_fix='Уточните допустимое число повторов или срок обработки.'))
    for key in graph:
        if key not in visited: visit(key)
    if any(n.type == 'parallel_gateway' for n in p.nodes):
        issues.append(Issue(severity='info', message='Проверьте, что каждая параллельная ветка гарантированно достигает объединения. Графовая проверка не доказывает отсутствие токеновых тупиков.', node_ids=[n.id for n in p.nodes if n.type == 'parallel_gateway']))
    if not any('отказ' in n.name.lower() or 'отклон' in n.name.lower() for n in p.nodes):
        issues.append(Issue(severity='clarification', source='BUSINESS_LOGIC', message='Обработка отказа не обозначена явно. Уточните, требуется ли она для этого процесса.'))
    issues.extend(Issue(severity='clarification', source='BUSINESS_LOGIC', message=a.question, node_ids=a.related_node_ids) for a in p.ambiguities)
    issues.append(Issue(severity='info', message='Сроки и обработка исключений не проверяются без соответствующих исходных данных.'))
    return issues
