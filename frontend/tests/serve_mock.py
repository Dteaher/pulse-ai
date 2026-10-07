"""Isolated browser-test backend: uses explicit fixture settings and never calls real providers."""
import sys
import logging
from pathlib import Path
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from app.main import create_app
from app.config import Settings
from app.services.llm.mock_provider import MockLLMProvider

app = create_app(Settings(_env_file=None, llm_provider='mock', api_limits_enabled=False), MockLLMProvider())
logging.getLogger('pulse.validation').setLevel(logging.WARNING)
logging.getLogger('pulse.llm').setLevel(logging.WARNING)
uvicorn.run(app, host='127.0.0.1', port=8003, log_level='warning')
