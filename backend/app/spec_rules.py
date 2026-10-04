"""Evidence for standard errors, distinct from PULSE's modeling policy.

References are to printed pages/sections of OMG BPMN 2.0.2 (formal/13-12-09).
Unlisted rules remain product/model quality rules, never invented standard rules.
"""
SPEC_RULES = {
    'DUPLICATE_ID': '8.3.1; 15.3.2',
    'BROKEN_REFERENCE': '15.3.2',
    'CROSS_POOL_SEQUENCE_FLOW': '9.3; 8.4.13',
    'SAME_POOL_MESSAGE_FLOW': '9.4',
    'INVALID_MESSAGE_ENDPOINT': '9.4.1; 10.5.2–10.5.4',
    'INVALID_EVENT_GATEWAY_SUCCESSOR': '10.6.6',
    'SHARED_EVENT_GATEWAY_SUCCESSOR': '10.6.6',
    'MIXED_EVENT_GATEWAY_SUCCESSORS': '10.6.6',
    'EVENT_GATEWAY_HAS_CONDITION': '10.6.6; 8.4.13',
    'INVALID_EVENT_GATEWAY_TOPOLOGY': '10.6.6 (неинстанцирующий subset)',
    'START_HAS_INCOMING': '10.5.2',
    'END_HAS_OUTGOING': '10.5.3',
    'PARALLEL_HAS_CONDITION': '8.4.13; 10.6.4',
    'CONDITION_OUTSIDE_GATEWAY': '8.4.13; 10.5.2; 10.5.4',
    'CONDITIONAL_ACTIVITY_SINGLE_FLOW': '8.4.13',
    'INVALID_DEFAULT_SOURCE': '8.4.13',
    'MULTIPLE_DEFAULT_FLOWS': '10.3.1; 10.6.2–10.6.3',
    'START_END_PAIR_REQUIRED': '10.5.2–10.5.3',
    'INVALID_NONE_CATCH': '10.5.4, Table 10.89',
}


def evidence(code):
    section = SPEC_RULES.get(code)
    return {'source': 'BPMN_SPEC', 'spec_section': section} if section else {}
