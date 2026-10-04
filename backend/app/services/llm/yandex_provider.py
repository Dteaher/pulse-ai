from .openai_compatible_provider import OpenAICompatibleProvider


class YandexProvider(OpenAICompatibleProvider):
    """Translate the common contract to Yandex AI Studio's compatible API."""
    name = 'yandex'

    def __init__(self, key, base_url, model, structured=None, timeout=90, folder_id='', max_tokens=16000, max_retries=2):
        self.folder_id = folder_id.strip()
        model = model.strip()
        if not self.folder_id and model.startswith('gpt://'):
            self.folder_id = model.split('/')[2]
        model_uri = f'gpt://{self.folder_id}/{model}' if model and not model.startswith('gpt://') and self.folder_id else model
        super().__init__(key, base_url, model_uri, structured, timeout, max_tokens, max_retries)

    def missing_settings(self):
        missing = [name for name, value in [('LLM_API_KEY', self.key), ('YANDEX_FOLDER_ID или каталог в LLM_MODEL', self.folder_id), ('LLM_MODEL', self.model), ('LLM_BASE_URL', self.base_url)] if not value]
        if self.folder_id and self.model.startswith('gpt://') and self.model.split('/')[2] != self.folder_id:
            missing.append('LLM_MODEL (каталог в URI должен совпадать с YANDEX_FOLDER_ID)')
        return missing

    def headers(self):
        return {'Authorization': 'Api-Key ' + self.key, 'OpenAI-Project': self.folder_id}
