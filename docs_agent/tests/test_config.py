from pathlib import Path

import pytest
from dotenv import load_dotenv

from docs_agent import config


@pytest.mark.parametrize('prefix,model_name', [
    ('AZURE_AI_', 'MODEL'), ('AZURE_OPENAI_', 'DEPLOYMENT'),
])
def test_local_env_and_azure_aliases(monkeypatch, tmp_path, prefix, model_name):
    for name in ('AZURE_AI_ENDPOINT', 'AZURE_AI_MODEL', 'AZURE_AI_API_KEY',
                 'AZURE_OPENAI_ENDPOINT', 'AZURE_OPENAI_DEPLOYMENT', 'AZURE_OPENAI_API_KEY'):
        monkeypatch.setenv(name, '')
    env = tmp_path / '.env'
    env.write_text(f'{prefix}ENDPOINT=https://example.openai.azure.com\n'
                   f'{prefix}{model_name}=test-deployment\n'
                   f'{prefix}API_KEY=test-key\n')
    expected = Path(config.__file__).with_name('.env')
    def load(path, override):
        assert path == expected and override is False
        load_dotenv(env, override=override)
    monkeypatch.setattr(config, 'load_dotenv', load)
    # Empty environment values must be absent for dotenv to populate them.
    for suffix in ('ENDPOINT', model_name, 'API_KEY'):
        monkeypatch.delenv(prefix + suffix)
    settings = config.Settings.from_env()
    assert settings.configured
    assert settings.model == 'test-deployment'
    assert settings.completion_url == 'https://example.openai.azure.com/openai/v1/chat/completions'
    assert 'test-key' not in repr(settings)
