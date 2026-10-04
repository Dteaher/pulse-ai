"""Evidence-backed business review; never derives expectations from generated description."""
from typing import Literal
from pydantic import Field
from .models import StrictModel, Issue
from .services.llm.base import ProviderError

class CoverageGap(StrictModel):
    kind: Literal['missing_action', 'missing_role', 'missing_condition', 'missing_message', 'wrong_relationship']
    source_excerpt: str = Field(min_length=1, max_length=1000)
    message: str = Field(min_length=1, max_length=600)
    suggested_fix: str = Field(min_length=1, max_length=600)
    node_ids: list[str] = Field(default_factory=list, max_length=30)

class CoverageReport(StrictModel):
    gaps: list[CoverageGap] = Field(default_factory=list, max_length=30)

def coverage_issues(report, process, source_text, answers=None):
    node_ids = {node.id for node in process.nodes}
    evidence = [source_text, *(str(value) for value in (answers or {}).values())]
    issues = []
    for gap in report.gaps:
        if not any(gap.source_excerpt in text for text in evidence) or not set(gap.node_ids).issubset(node_ids):
            raise ProviderError('Проверка полноты вернула неподтверждённые замечания. Результат не опубликован.', reason='invalid_response')
        issues.append(Issue(code='BUSINESS_' + gap.kind.upper(), severity='error', source='BUSINESS_LOGIC',
            category='business_coverage', message=gap.message + ' Источник: ' + gap.source_excerpt[:350],
            suggested_fix=gap.suggested_fix, node_ids=gap.node_ids))
    return issues
