"""Safe diagnostics; parser details remain in developer logs."""
from .models import Issue


def classify(issue):
    return issue.diagnostic_classification()


class ProcessValidationError(ValueError):
    def __init__(self, diagnostics):
        super().__init__('Импортированная модель содержит ошибки в графе.')
        self.diagnostics = [classify(i) for i in diagnostics]


class PipelineFailure(Exception):
    def __init__(self, message, diagnostics):
        super().__init__(message)
        self.diagnostics = [classify(i) for i in diagnostics]


def build_failure():
    return PipelineFailure('Не удалось собрать корректный BPMN. Текущая версия сохранена.', [
        Issue(code='BPMN_BUILD_FAILED', severity='error', source='XSD',
              type='INTEROPERABILITY_ERROR', message='Проверка сформированного BPMN не пройдена.',
              suggested_fix='Проверьте сборку XML, ссылки и диаграммные координаты.')])
