from app.config import Settings

def test_hosting_environment_overrides_dotenv_and_constructor_overrides_environment(tmp_path,monkeypatch):
    env=tmp_path/'.env'
    env.write_text('PRIMARY_LLM_MODEL=file-model\nLLM_TIMEOUT=90\nPRIMARY_LLM_API_KEY=file-key\n',encoding='utf-8')
    monkeypatch.setenv('PRIMARY_LLM_MODEL','hosting-model')
    monkeypatch.setenv('PRIMARY_LLM_API_KEY','hosting-test-key')
    monkeypatch.setenv('LLM_TIMEOUT','120')
    s=Settings(_env_file=env)
    assert s.primary_llm_model=='hosting-model' and s.primary_llm_api_key=='hosting-test-key' and s.llm_timeout==120
    assert Settings(_env_file=env,primary_llm_model='explicit-test').primary_llm_model=='explicit-test'
