"""Offline size research. Token estimates are never billed provider usage."""
import json
import sys
from collections import Counter
from pathlib import Path
import tiktoken

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.models import ProcessDefinition
from tools.evidence_wire import encode, decode
from app.repair import quality_fingerprint


def run():
    tokenizer = tiktoken.get_encoding('o200k_base')
    def count(data):
        return len(tokenizer.encode(json.dumps(data, ensure_ascii=False, separators=(',', ':')), disallowed_special=()))
    data = json.loads((ROOT / 'examples/pipeline-resilience/exact-complex-polished-response.json').read_text(encoding='utf-8'))['process']
    p = ProcessDefinition.model_validate(data)
    canonical = p.model_dump(mode='json')
    sources = Counter(n.source_text for n in p.nodes if n.source_text)
    labels = Counter(item.name for item in [*p.nodes, *p.flows, *p.message_flows] if item.name)
    wire = encode(p)
    no_evidence = {**canonical, 'nodes': [{k: v for k, v in n.items() if k != 'source_text'} for n in canonical['nodes']]}
    record = {'method': 'o200k_base estimates of serialized canonical JSON; not exact MultiAI/Vertex tokenizer or billed output. Top-level components include field overhead; evidence/labels overlap nodes/flows and are not additive.',
              'canonical_estimated_tokens': count(canonical),
              'components_estimated_tokens': {key: count({key: value}) for key, value in canonical.items()},
              'evidence_marginal_estimated_tokens': count(canonical) - count(no_evidence),
              'evidence_standalone_estimated_tokens': count([n.source_text for n in p.nodes]),
              'duplicate_nonempty_evidence': {text: times for text, times in sources.items() if times > 1},
              'duplicate_label_count': sum(times - 1 for times in labels.values() if times > 1),
              'duplicated_labels_estimated_tokens': count([label for label, times in labels.items() for _ in range(times - 1)]),
              'wire_estimated_tokens': count(wire), 'wire_reference_entries': len(wire['evidence']),
              'estimated_token_saving': count(canonical) - count(wire),
              'exact_canonical_roundtrip': quality_fingerprint(decode(wire)) == quality_fingerprint(p),
              'wire_production_enabled': False,
              'actor_names_already_deduplicated': True,
              'actor_representation': 'participant_id in nodes; participant names defined once',
              'original_text_repetition': 'description is stored once; source_text carries local evidence',
              'historical_request_output_tokens': 9028,
              'historical_explanation': '9028 is the sum of analysis, first extraction and full corrective output, not one ProcessDefinition'}
    (ROOT / 'examples/performance-v2/output-breakdown.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(record['canonical_estimated_tokens'], record['wire_estimated_tokens'], record['estimated_token_saving'])


if __name__ == '__main__':
    run()
