from uuid import uuid4
from .base import LLMProvider, ProviderError
from ...models import Node, Flow, AuditResult, ClarificationResult, ModificationResult, AmbiguityAnalysis
from ...examples import TEXTS, demo_process, ambiguous_process


class MockLLMProvider(LLMProvider):
    """Explicit fixture mode. Never pretends to understand arbitrary text."""
    name = 'mock'
    model = 'fixtures'
    business_audit_available = False
    async def analyze_ambiguities(self, text, context=None):
        # Fixture questions are deliberately stored in the fixture process.
        return AmbiguityAnalysis()

    async def parse_process(self, text, correction=''):
        normalized = text.strip()
        if normalized not in TEXTS:
            raise ProviderError('Тестовый режим поддерживает только три встроенных примера. Для произвольного текста настройте LLM_PROVIDER и API-ключ в .env.')
        index = TEXTS.index(normalized)
        return ambiguous_process() if index == 2 else demo_process(index)

    async def clarify_process(self, process, answers, correction=''):
        p = process.model_copy(deep=True)
        known = {a.id: a for a in p.ambiguities}
        for key, answer in answers.items():
            if key not in known or answer not in known[key].suggested_answers:
                raise ProviderError('В тестовом режиме выберите предложенный ответ. Свободные уточнения доступны с реальной моделью.')
        if answers.get('parallel') == 'Последовательно':
            p.nodes = [n for n in p.nodes if n.id not in ('Fork', 'Join')]
            p.flows = [f for f in p.flows if f.source not in ('Fork', 'Join', 'LegalCheck', 'SecurityCheck') and f.target not in ('Fork', 'Join')]
            p.flows.extend([Flow(id='SequentialA', source='Complete', target='LegalCheck', name='Да', condition='Да'),
                            Flow(id='SequentialB', source='LegalCheck', target='SecurityCheck'),
                            Flow(id='SequentialC', source='SecurityCheck', target='Decide')])
        if answers.get('refusal') == 'Вернуть документы на доработку':
            p.nodes = [n for n in p.nodes if n.id not in ('Reject', 'EndNo')]
            p.flows = [f for f in p.flows if f.source != 'Reject' and f.target != 'Reject']
            p.flows.append(Flow(id='RefusalReturn', source='Approved', target='Revise', name='Нет', condition='Нет'))
        p.ambiguities = [a for a in p.ambiguities if a.id not in answers]
        return ClarificationResult(process=p)

    async def modify_process(self, process, command, correction=''):
        if command.strip().lower().rstrip('.') != 'после проверки документов добавь согласование руководителем':
            raise ProviderError('Тестовая команда: «После проверки документов добавь согласование руководителем». Другие изменения требуют реальной модели.')
        p = process.model_copy(deep=True)
        anchor = next((n for n in p.nodes if n.name == 'Проверить документы'), None)
        if anchor is None:
            raise ProviderError('В этом примере нет задачи «Проверить документы». Загрузите пример подключения.')
        head = next((x for x in p.participants if x.name == 'Руководитель'), None)
        if head is None:
            raise ProviderError('В процессе нет участника «Руководитель».')
        node_id = 'Approval_' + uuid4().hex[:8]
        p.nodes.append(Node(id=node_id, type='user_task', name='Согласовать заявку', participant_id=head.id,
                            source_text=command, inferred=True, confidence='confirmation_required'))
        for f in p.flows:
            if f.source == anchor.id:
                f.source = node_id
        p.flows.append(Flow(id='Flow_' + uuid4().hex[:8], source=anchor.id, target=node_id))
        return ModificationResult(process=p)

    async def audit_process(self, process):
        return AuditResult(issues=[])
