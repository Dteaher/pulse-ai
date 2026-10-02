import logging
from fastapi import FastAPI, Depends
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from lxml import etree
from pydantic import ValidationError
from .config import Settings
from .models import GenerateRequest, ClarifyRequest, ModifyRequest, ProcessRequest, ImportRequest
from .services.llm.base import LLMProvider, ProviderError
from .services.llm.factory import get_llm_provider
from .pipeline import generate_valid, audit_rules
from .validator import validate_process
from .bpmn import build_bpmn, import_process, validate_xml
from .examples import TEXTS

logger = logging.getLogger('pulse')


def create_app(settings=None, provider: LLMProvider | None = None):
    s = settings or Settings()
    app = FastAPI(title='PULSE', version='0.1.0')
    app.state.provider = provider or get_llm_provider(s)

    def current_connection():
        # Snapshot once per request: retries must use the same provider/model.
        # Explicit settings/provider injections stay fixed for tests and embedding.
        if settings is not None or provider is not None:
            return s, app.state.provider.new_request()
        try:
            active = Settings()
        except ValidationError as exc:
            # Settings errors can contain secret input; never return their details.
            raise ProviderError('Некорректные настройки .env. Проверьте типы и допустимые значения параметров LLM.') from exc
        return active, get_llm_provider(active)

    def current_provider(connection=Depends(current_connection)):
        return connection[1]

    @app.exception_handler(ProviderError)
    async def provider_error(request, exc):
        return JSONResponse(status_code=502, content={'detail': str(exc)})

    @app.exception_handler(ValueError)
    @app.exception_handler(etree.LxmlError)
    async def invalid_process(request, exc):
        return JSONResponse(status_code=422, content={'detail': 'Не удалось обработать процесс: ' + str(exc)[:1500]})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse(status_code=422, content={'detail': 'Проверьте входные данные: описание и структуру процесса.'})

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        logger.error('Unhandled %s', type(exc).__name__)
        return JSONResponse(status_code=500, content={'detail': 'Не удалось обработать запрос. Повторите попытку.'})

    @app.get('/api/health')
    def health(connection=Depends(current_connection)):
        active, selected = connection
        configured = selected.configured
        missing = selected.missing_settings() if hasattr(selected, 'missing_settings') else []
        return {'status': 'ok', 'provider': selected.name, 'configured': configured, 'missing_settings': missing}

    @app.get('/api/llm/info')
    def llm_info(selected: LLMProvider = Depends(current_provider)):
        return selected.connection_info

    @app.get('/api/examples')
    def examples():
        return [{'name': name, 'text': text} for name, text in zip(['Обработка заявки', 'Подключение к электросети', 'Описание с неоднозначностями'], TEXTS)]

    @app.post('/api/process/generate')
    async def generate(body: GenerateRequest, selected=Depends(current_provider)):
        result = await generate_valid(lambda c: selected.parse_process(body.text, c))
        return {**result, 'metadata': selected.metadata}

    @app.post('/api/process/clarify')
    async def clarify(body: ClarifyRequest, selected=Depends(current_provider)):
        expected = {a.id for a in body.process.ambiguities}
        if not body.answers or not set(body.answers).issubset(expected) or any(not a.strip() or len(a) > 2000 for a in body.answers.values()):
            raise ValueError('Ответьте на предложенные вопросы.')
        async def call(correction):
            return (await selected.clarify_process(body.process, body.answers, correction)).process
        result = await generate_valid(call)
        return {**result, 'metadata': selected.metadata}

    @app.post('/api/process/modify')
    async def modify(body: ModifyRequest, selected=Depends(current_provider)):
        errors = validate_process(body.process)
        if errors:
            raise ValueError('; '.join(i.message for i in errors))
        async def call(correction):
            return (await selected.modify_process(body.process, body.command, correction)).process
        result = await generate_valid(call)
        return {**result, 'metadata': selected.metadata}

    @app.post('/api/process/bpmn')
    def bpmn(body: ProcessRequest):
        if any(a.severity == 'critical' for a in body.process.ambiguities):
            raise ValueError('Сначала ответьте на критичные уточнения.')
        return {'xml': build_bpmn(body.process)}

    @app.post('/api/process/import')
    def import_bpmn(body: ImportRequest):
        return {'process': import_process(body.xml, body.previous)}

    @app.post('/api/process/audit')
    async def audit(body: ImportRequest, selected=Depends(current_provider)):
        p = import_process(body.xml, body.previous)
        graph_issues = validate_process(p)
        xml_ok = True
        try:
            validate_xml(body.xml)
        except etree.LxmlError:
            xml_ok = False
        issues = graph_issues + audit_rules(p)
        business_available = False
        notice = ''
        try:
            report = await selected.audit_process(p)
            valid_ids = {n.id for n in p.nodes}
            for issue in report.issues:
                issue.node_ids = [i for i in issue.node_ids if i in valid_ids]
                issue.origin = 'llm'
            issues.extend(report.issues)
            business_available = selected.business_audit_available
        except (ProviderError, ValueError, ValidationError):
            notice = 'Аудит модели недоступен. Показаны результаты программных правил.'
        return {'technical': {'BPMN XML / XSD': xml_ok, 'Граф / связи / события / шлюзы': not graph_issues,
                              'Участники': not any('участник' in i.message.lower() for i in graph_issues)},
                'issues': issues, 'llm_audit': business_available, 'notice': notice,
                'metadata': selected.metadata if business_available else None}

    return app


app = create_app()
