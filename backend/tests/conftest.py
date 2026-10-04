import pytest
from app.services.llm.cooldown import cooldowns


@pytest.fixture(autouse=True)
def isolate_provider_cooldowns():
    cooldowns.deadlines.clear()
    yield
    cooldowns.deadlines.clear()


@pytest.fixture(autouse=True)
def isolate_deployment_coverage_setting(monkeypatch):
    # Old tests isolate their own pipeline stage; coverage tests opt in explicitly.
    monkeypatch.setenv('LLM_BUSINESS_COVERAGE_ENABLED', 'false')


@pytest.fixture(autouse=True)
def isolate_new_preflight_from_legacy_graph_tests(monkeypatch, request):
    # Existing tests isolate extraction/graph/failover. Dedicated preflight tests
    # exercise analysis itself, including its HTTP transport and routing.
    if request.node.path.name in {'test_pipeline_resilience.py', 'test_performance.py','test_qwen_integration.py'}:
        return
    from app.models import AmbiguityAnalysis
    from app.services.llm.openai_compatible_provider import OpenAICompatibleProvider
    from app.services.llm.vertex_gemini_provider import VertexGeminiProvider
    from app.services.llm.router import LLMRouter
    async def complete_description(self, text, context=None):
        return AmbiguityAnalysis()
    monkeypatch.setattr(OpenAICompatibleProvider, 'analyze_ambiguities', complete_description)
    monkeypatch.setattr(VertexGeminiProvider, 'analyze_ambiguities', complete_description)
    monkeypatch.setattr(LLMRouter, 'analyze_ambiguities', complete_description)
