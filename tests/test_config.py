from app.config import get_settings


def test_api_key_falls_back_to_orderdesk_name(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ORDERDESK_ANTHROPIC_API_KEY", "key-from-cloud")
    get_settings.cache_clear()
    assert get_settings().anthropic_api_key == "key-from-cloud"


def test_standard_api_key_name_wins(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key-from-env")
    monkeypatch.setenv("ORDERDESK_ANTHROPIC_API_KEY", "key-from-cloud")
    get_settings.cache_clear()
    assert get_settings().anthropic_api_key == "key-from-env"
