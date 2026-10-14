"""Request context for safe logs across async provider and pipeline calls."""
import logging
import json
from dataclasses import dataclass, field
from time import perf_counter
from datetime import datetime, timezone
from contextvars import ContextVar

request_id = ContextVar('pulse_request_id', default='outside_http')
operation = ContextVar('pulse_operation', default='internal')
profile = ContextVar('pulse_performance', default=None)


@dataclass
class PerformanceProfile:
    started: float = field(default_factory=perf_counter)
    calls: list = field(default_factory=list)
    stages: dict = field(default_factory=dict)
    stage_events: list = field(default_factory=list)
    validation_attempt: int | None = None
    validation_error_count: int = 0
    graph_corrective_count: int = 0
    patch_corrective_count: int = 0
    patch_success_count: int = 0
    patch_rejected_quality_count: int = 0
    patch_failure_count: int = 0
    validation_codes: list = field(default_factory=list)
    cache_hit: bool = False
    deduplicated: bool = False

    def add(self, stage, elapsed, *, started=None):
        ended = perf_counter()
        if started is not None:
            self.stage_events.append({'stage': stage, 'provider': 'backend', 'model': None, 'attempt': self.validation_attempt, 'start_offset_ms': round((started-self.started)*1000,3), 'end_offset_ms': round((ended-self.started)*1000,3), 'duration_ms': round(elapsed,3)})
        self.stages[stage] = round(self.stages.get(stage, 0) + elapsed, 2)

    def report(self):
        keys = ('ambiguity_analysis_ms', 'combined_preparation_ms', 'clarify_generation_ms', 'llm_primary_ms',
                'llm_corrective_ms', 'llm_fallback_ms', 'normalization_ms',
                'process_validation_ms', 'semantic_validation_ms', 'business_coverage_ms', 'bpmn_build_ms',
                'reference_validation_ms', 'xsd_validation_ms', 'di_validation_ms',
                'doctor_ms', 'modify_ms', 'serialization_ms', 'layout_ms')
        stages = {key: self.stages.get(key, 0) for key in keys}
        for call in self.calls:
            key = 'llm_fallback_ms' if call.get('fallback') else 'llm_corrective_ms' if call.get('corrective') else 'llm_primary_ms'
            stages[key] += call['duration_ms']
        def tokens(field):
            values = [c[field] for c in self.calls if c.get(field) is not None]
            return sum(values) if values or not self.calls else None
        def operation_tokens(name):
            values = [c.get('output_tokens') for c in self.calls if c.get('operation') == name]
            return None if any(v is None for v in values) else sum(values)
        return {'request_total_ms': round((perf_counter()-self.started)*1000, 2),
                **stages, 'stage_events': self.stage_events, 'calls': self.calls, 'attempt_count': sum(c.get('http_attempts', 1) for c in self.calls),
                'corrective_retry_count': self.graph_corrective_count + sum(bool(c.get('json_corrective')) for c in self.calls),
                'validation_error_count': self.validation_error_count,
                'validation_codes': self.validation_codes,
                'full_corrective_count': self.graph_corrective_count,
                'patch_corrective_count': self.patch_corrective_count,
                'patch_success_count': self.patch_success_count,
                'patch_rejected_quality_count': self.patch_rejected_quality_count,
                'patch_failure_count': self.patch_failure_count,
                'patch_output_tokens': operation_tokens('structural_repair'),
                'full_corrective_output_tokens': operation_tokens('corrective'),
                'process_definition_size': self.stages.get('process_definition_size'),
                'fallback_used': any(c.get('fallback') for c in self.calls),
                'input_tokens': tokens('input_tokens'),
                'output_tokens': tokens('output_tokens'),
                'usage_complete': all(c.get('input_tokens') is not None and c.get('output_tokens') is not None for c in self.calls),
                'cache_hit': self.cache_hit, 'deduplicated': self.deduplicated}


def corrective_metrics(reports):
    """Aggregate cache misses; rates need a cohort, never a single request log."""
    misses = [r for r in reports if not r.get('cache_hit') and not r.get('deduplicated')]
    patches = sum(r.get('patch_corrective_count', 0) for r in misses)
    patch_tokens = [r.get('patch_output_tokens') for r in misses if r.get('patch_corrective_count', 0)]
    full_tokens = [r.get('full_corrective_output_tokens') for r in misses]
    def rate(numerator, denominator):
        return numerator / denominator if denominator else None
    return {'request_count': len(misses), 'patch_attempt_count': patches,
            'full_corrective_rate': rate(sum(r.get('full_corrective_count', 0) > 0 for r in misses), len(misses)),
            'patch_corrective_rate': rate(sum(r.get('patch_corrective_count', 0) > 0 for r in misses), len(misses)),
            'patch_success_rate': rate(sum(r.get('patch_success_count', 0) for r in misses), patches),
            'patch_rejected_quality_rate': rate(sum(r.get('patch_rejected_quality_count', 0) for r in misses), patches),
            'average_patch_output_tokens': None if any(v is None for v in patch_tokens) else rate(sum(patch_tokens), patches),
            'full_corrective_output_tokens': None if any(v is None for v in full_tokens) else sum(full_tokens)}


def add_stage(name, started):
    current = profile.get()
    if current:
        current.add(name, (perf_counter()-started)*1000, started=started)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


CALL_FIELDS = frozenset({
    'provider', 'model', 'operation', 'reasoning_effort', 'response_format',
    'system_prompt_chars', 'user_payload_chars', 'prompt_size_chars', 'schema_chars',
    'max_tokens', 'corrective', 'json_corrective', 'output_budget_retry', 'fallback',
    'input_tokens', 'output_tokens', 'reasoning_tokens', 'http_status', 'response_model',
    'cost_rub', 'finish_reason', 'http_attempts', 'duration_ms', 'failure_class',
    'timeout_phase', 'started_at', 'ended_at', 'attempt', 'ttft_ms', 'streaming',
    'retry_reason', 'format_attempts',
})


def record_call(entry, *, secrets=()):
    # Allow only metric fields, never request bodies, headers or customer text.
    safe = {key: value for key, value in entry.items() if key in CALL_FIELDS}
    for key, value in safe.items():
        if isinstance(value, str):
            for secret in secrets:
                if secret:
                    value = value.replace(secret, '[REDACTED]')
            safe[key] = value
    current = profile.get()
    if current:
        current.calls.append(safe)
    logging.getLogger('pulse.llm').info('llm_performance %s', json.dumps(safe))
    return safe


class RequestLogContext(logging.Filter):
    def filter(self, record):
        record.request_id = request_id.get()
        record.operation = operation.get()
        return True
