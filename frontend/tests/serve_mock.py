"""Isolated browser-test backend: uses explicit fixture settings and never calls real providers."""
import sys
from pathlib import Path
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from app.main import create_app
from app.config import Settings
from app.services.llm.mock_provider import MockLLMProvider

uvicorn.run(create_app(Settings(_env_file=None, llm_provider='mock'), MockLLMProvider()), host='127.0.0.1', port=8001)
