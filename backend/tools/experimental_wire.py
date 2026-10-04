"""Lossless, compact JSON names inside adapters; canonical DTOs stay unchanged.

All fields, evidence, enums and constraints are retained. JSON Schema titles
explain original names; aliases reduce repeated output keys in large graphs.
"""
from copy import deepcopy
from functools import lru_cache
from pydantic import create_model, ConfigDict
from app.models import (Node, Flow, Participant, Pool, MessageFlow, ProcessDefinition,
                       ProcessPatch, ProcessPreparation, ModificationPreparation)

ALIASES = {'id': 'i', 'type': 't', 'name': 'n', 'participant_id': 'p',
           'source_text': 's', 'confidence': 'c', 'inferred': 'f',
           'event_definition': 'e', 'decision_basis': 'b', 'source': 'from',
           'target': 'to', 'condition': 'if', 'is_default': 'default',
           'description': 'd', 'participants': 'roles', 'nodes': 'steps',
           'flows': 'paths', 'pools': 'groups', 'message_flows': 'messages'}


def make_wire(original, overrides=None):
    fields = {}
    for name, info in original.model_fields.items():
        field = deepcopy(info)
        field.alias = field.validation_alias = field.serialization_alias = ALIASES.get(name, name)
        field.title = name
        fields[name] = ((overrides or {}).get(name, info.annotation), field)
    return create_model('Wire' + original.__name__, __config__=ConfigDict(
        strict=True, extra='forbid', populate_by_name=True), **fields)


@lru_cache(maxsize=8)
def wire_model(original):
    parts = {'nodes': list[make_wire(Node)], 'flows': list[make_wire(Flow)],
             'participants': list[make_wire(Participant)], 'pools': list[make_wire(Pool)],
             'message_flows': list[make_wire(MessageFlow)]}
    if original in (ProcessDefinition, ProcessPatch):
        return make_wire(original, parts)
    if original is ProcessPreparation:
        return make_wire(original, {'process': wire_model(ProcessDefinition) | None})
    if original is ModificationPreparation:
        return make_wire(original, {'patch': wire_model(ProcessPatch) | None})
    return original


def wire_instruction():
    return ('\nТранспорт JSON компактный, но все бизнес-поля обязательны по схеме. '
            'Короткие ключи: ' + ', '.join(f'{old}={new}' for old, new in ALIASES.items()) +
            '. title каждого поля показывает исходное имя. Не сокращай значения, evidence или бизнес-граф.\n')
