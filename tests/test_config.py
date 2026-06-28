import pytest

from linkedin_post_bot.config import ConfigError, load_config


def _base_env():
    return {
        "TELEGRAM_BOT_TOKEN": "tok-123",
        "TELEGRAM_ALLOWED_USER_ID": "42",
    }


def test_loads_required_values():
    cfg = load_config(_base_env())
    assert cfg.telegram_bot_token == "tok-123"
    assert cfg.telegram_allowed_user_id == 42


def test_loads_optional_placeholders():
    env = _base_env() | {
        "NVIDIA_API_KEY": "nvapi-1",
        "LINKEDIN_CLIENT_ID": "cid",
        "LINKEDIN_REDIRECT_URI": "http://localhost/cb",
    }
    cfg = load_config(env)
    assert cfg.nvidia_api_key == "nvapi-1"
    assert cfg.linkedin_client_id == "cid"
    assert cfg.linkedin_redirect_uri == "http://localhost/cb"
    # Unset optional stays None.
    assert cfg.linkedin_client_secret is None


def test_nvidia_defaults_and_overrides():
    cfg = load_config(_base_env())
    assert cfg.nvidia_api_key is None
    assert cfg.nvidia_model == "mistralai/mistral-medium-3.5-128b"
    assert cfg.nvidia_base_url == "https://integrate.api.nvidia.com/v1"

    env = _base_env() | {
        "NVIDIA_MODEL": "some/other-model",
        "NVIDIA_BASE_URL": "https://example.test/v1",
    }
    cfg = load_config(env)
    assert cfg.nvidia_model == "some/other-model"
    assert cfg.nvidia_base_url == "https://example.test/v1"


def test_missing_token_fails_fast():
    env = {"TELEGRAM_ALLOWED_USER_ID": "42"}
    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
        load_config(env)


def test_blank_token_fails_fast():
    env = _base_env() | {"TELEGRAM_BOT_TOKEN": "   "}
    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
        load_config(env)


def test_missing_user_id_fails_fast():
    env = {"TELEGRAM_BOT_TOKEN": "tok-123"}
    with pytest.raises(ConfigError, match="TELEGRAM_ALLOWED_USER_ID"):
        load_config(env)


def test_non_integer_user_id_fails_fast():
    env = _base_env() | {"TELEGRAM_ALLOWED_USER_ID": "not-a-number"}
    with pytest.raises(ConfigError, match="must be an integer"):
        load_config(env)


def test_schedule_defaults():
    cfg = load_config(_base_env())
    assert cfg.topics_path == "topics.txt"
    assert cfg.schedule_hour == 9
    assert cfg.schedule_minute == 0


def test_schedule_overrides():
    env = _base_env() | {
        "TOPICS_PATH": "my_topics.txt",
        "SCHEDULE_HOUR": "18",
        "SCHEDULE_MINUTE": "45",
    }
    cfg = load_config(env)
    assert cfg.topics_path == "my_topics.txt"
    assert cfg.schedule_hour == 18
    assert cfg.schedule_minute == 45


def test_schedule_hour_out_of_range_fails():
    env = _base_env() | {"SCHEDULE_HOUR": "24"}
    with pytest.raises(ConfigError, match="between 0 and 23"):
        load_config(env)


def test_schedule_minute_non_integer_fails():
    env = _base_env() | {"SCHEDULE_MINUTE": "xx"}
    with pytest.raises(ConfigError, match="must be an integer"):
        load_config(env)
