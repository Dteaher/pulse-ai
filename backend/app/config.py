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
    primary_llm_omit_token_limit: bool = False
    llm_business_coverage_enabled: bool = False
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
    llm_parse_reasoning_effort: Literal['low', 'medium', 'high'] | None = None
    llm_clarify_reasoning_effort: Literal['low', 'medium', 'high'] | None = None
    llm_modify_reasoning_effort: Literal['low', 'medium', 'high'] | None = None
    llm_doctor_reasoning_effort: Literal['low', 'medium', 'high'] | None = None
    llm_corrective_reasoning_effort: Literal['low', 'medium', 'high'] | None = None
    llm_performance_enabled: bool = False
    llm_debug_raw_response: bool = False
    llm_output_retry_enabled: bool = False
    llm_parse_retry_max_tokens: int = Field(default=16000,ge=512,le=64000)
    llm_combined_enabled: bool = False
    llm_patch_enabled: bool = False
    llm_patch_corrective_enabled: bool = False
    llm_combined_parse_enabled: bool = False
    llm_repair_max_tokens: int = Field(default=1600, ge=512, le=4000)
    deterministic_modify_enabled: bool = False
    llm_parse_max_tokens: int = Field(default=8000, ge=512, le=64000)
    llm_clarify_max_tokens: int = Field(default=1500, ge=512, le=64000)
    llm_modify_max_tokens: int = Field(default=8000, ge=512, le=64000)
    llm_doctor_max_tokens: int = Field(default=3000, ge=512, le=64000)
    llm_corrective_max_tokens: int = Field(default=8000, ge=512, le=64000)
    llm_request_budget: int = Field(default=180, ge=5, le=900)
    llm_fallback_reserve: int = Field(default=30, ge=1, le=180)
    performance_cache_entries: int = Field(default=64, ge=0, le=256)
    performance_cache_ttl: int = Field(default=300, ge=1, le=3600)
    yandex_folder_id: str = ''

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        # Explicit test overrides > hosting environment > local .env > mounted secrets.
        return init_settings, env_settings, dotenv_settings, file_secret_settings
