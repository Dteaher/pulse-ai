"""Small operation policy; provider-specific wire options remain in adapters."""
from contextvars import ContextVar

OPERATION_FIELDS = {
    'extraction': ('parse', 'parse'),
    'preparation': ('parse', 'parse'),
    'ambiguity': ('clarify', 'clarify'),
    'clarification': ('parse', 'clarify'),
    'modification': ('modify', 'modify'),
    'audit': ('doctor', 'doctor'),
    'business_coverage': ('doctor', 'business_coverage'),
    'corrective': ('corrective', 'corrective'),
}


def operation_name(operation, corrective=False):
    if corrective or operation == 'corrective_patch':
        return 'corrective'
    return {
        'preparation_v2': 'preparation',
        'modification_patch': 'modification',
        'preparation_patch': 'modification',
    }.get(operation, operation)


def operation_reasoning(provider):
    name = operation_name(getattr(provider, '_operation', ''), getattr(provider, '_corrective', False))
    return getattr(provider, 'reasoning_by_operation', {}).get(name) or provider.reasoning_effort


call_context = ContextVar('pulse_llm_call', default={})


def compact_process(process):
    # Keep business evidence and IDs; omit only default-valued fields. There is
    # no XML/DI/history in this representation, and the canonical DTO is unchanged.
    return process.model_dump(exclude_defaults=True, exclude_none=True)


def operation_budget(provider, operation, payload, corrective=False):
    if operation == 'structural_repair':
        return min(provider.max_tokens, getattr(provider, 'repair_max_tokens', 1600))
    budgets = getattr(provider, 'operation_budgets', {})
    name = operation_name(operation, corrective)
    configured = budgets.get(name)
    if not configured:
        return provider.max_tokens
    if operation in ('audit', 'ambiguity', 'business_coverage'):
        return min(provider.max_tokens, configured)
    # Large graphs need a larger complete result, never a speed-induced truncation.
    process = payload.get('process') or payload.get('context', {}).get('base_process') or {}
    graph_floor = 400 + len(process.get('nodes', [])) * 150 + len(process.get('flows', [])) * 45
    # An explicit operation budget is independent of the global fallback.
    # Otherwise parse=12000 would silently remain 8000 on the wire.
    return min(max(provider.max_tokens, configured), max(configured, graph_floor))
