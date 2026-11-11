from desk.config import Settings


def _base(**kwargs):
    values = dict(
        openai_api_key=None,
        openai_model="gpt-4o-mini",
        paypal_client_id=None,
        paypal_client_secret=None,
        paypal_mode_override="mock",
        host="127.0.0.1",
        port=8000,
        gemini_api_key=None,
        gemini_model="gemini-flash-latest",
    )
    values.update(kwargs)
    return Settings(**values)


def test_gemini_is_default_when_both_keys_are_present():
    settings = _base(gemini_api_key="gemini-key-for-test", openai_api_key="openai-key-for-test")
    assert settings.llm_provider == "gemini"
    assert settings.llm_model == "gemini-flash-latest"
    assert settings.llm_ready is True


def test_openai_is_used_when_gemini_is_missing():
    settings = _base(openai_api_key="openai-key-for-test")
    assert settings.llm_provider == "openai"
    assert settings.llm_model == "gpt-4o-mini"


def test_override_can_select_openai_when_both_exist():
    settings = _base(
        gemini_api_key="gemini-key-for-test",
        openai_api_key="openai-key-for-test",
        llm_provider_override="openai",
    )
    assert settings.llm_provider == "openai"


def test_no_provider_without_keys():
    settings = _base()
    assert settings.llm_provider is None
    assert settings.llm_ready is False
