from pathlib import Path
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASE_URLS = {
    'openai': 'https://api.openai.com/v1',
    'yandex': 'https://ai.api.cloud.yandex.net/v1',
    'multiai': 'https://multiai.store/v1',
    'gemini': 'https://generativelanguage.googleapis.com/v1beta/openai',
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / '.env', extra='ignore')
    app_env: str = 'development'
    llm_provider: str = 'openai_compatible'
    primary_llm_provider: str = ''
    primary_llm_model: str | None = None
    primary_llm_base_url: str | None = None
    primary_llm_api_key: str | None = None
    llm_fallback_enabled: bool = True
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_graph_max_retries: int = Field(default=1, ge=0, le=2)
    max_clarification_rounds: int = Field(default=3, ge=1, le=10)
    fallback_llm_provider: str = ''
    fallback_llm_api_key: str = ''
    fallback_llm_base_url: str = ''
    fallback_llm_model: str = ''
    fallback_llm_folder_id: str = ''
    fallback_llm_structured_output: bool | None = None
    llm_api_key: str = ''
    llm_base_url: str = ''
    llm_model: str = ''
    llm_structured_output: bool | None = None
    llm_timeout: int = Field(default=90, ge=1, le=600)
    llm_max_tokens: int = Field(default=16000, ge=512, le=64000)
    llm_reasoning_effort: Literal['low', 'medium', 'high', 'xhigh', 'max'] | None = None
    yandex_folder_id: str = ''

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        # Explicit constructor values support isolated tests; deployment uses .env only.
        return init_settings, dotenv_settings
