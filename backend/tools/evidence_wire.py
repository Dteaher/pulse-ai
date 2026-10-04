"""Lossless evidence-reference research only; not a production LLM DTO."""
from collections import Counter
from copy import deepcopy
from app.models import ProcessDefinition


def encode(process):
    data = process.model_dump(mode='json')
    counts = Counter(n['source_text'] for n in data['nodes'] if len(n['source_text']) >= 40)
    table = [text for text, count in counts.items() if count > 1]
    refs = {text: index for index, text in enumerate(table)}
    for node in data['nodes']:
        if node['source_text'] in refs:
            node['source_text'] = {'ref': refs[node['source_text']]}
    return {'process': data, 'evidence': table}


def decode(wire):
    data = deepcopy(wire['process'])
    for node in data['nodes']:
        source = node['source_text']
        if isinstance(source, dict):
            if set(source) != {'ref'} or type(source['ref']) is not int or not 0 <= source['ref'] < len(wire['evidence']):
                raise ValueError('Invalid evidence reference')
            node['source_text'] = wire['evidence'][source['ref']]
    return ProcessDefinition.model_validate(data)
