"""Configuration loader.

Reads settings from environment variables (optionally populated from a ``.env``
file via ``python-dotenv``). Required variables are validated at load time so the
bot fails fast at startup with a clear message rather than mid-flight.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    """Resolved application configuration."""

    telegram_bot_token: str
    telegram_allowed_user_id: int

    # Claude generation (slice 02) + placeholders for LinkedIn publishing.
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-4-8"
    linkedin_client_id: str | None = None
    linkedin_client_secret: str | None = None
    linkedin_redirect_uri: str | None = None

    # Scheduled rotation (slice 04).
    topics_path: str = "topics.txt"
    schedule_hour: int = 9
    schedule_minute: int = 0

    # Manual posting + dry run (slice 05). When ``dry_run`` is True the bot shows
    # exactly what it would publish and makes no LinkedIn call.
    dry_run: bool = False


def load_config(env: dict[str, str] | None = None, *, use_dotenv: bool = True) -> Config:
    """Load and validate configuration.

    Args:
        env: Optional mapping to read from instead of ``os.environ`` (used by tests).
        use_dotenv: When ``True`` and ``env`` is not supplied, load a local ``.env``
            file into the process environment before reading.

    Raises:
        ConfigError: If a required variable is missing/blank, or if
            ``TELEGRAM_ALLOWED_USER_ID`` is not a valid integer.
    """
    if env is None:
        if use_dotenv:
            load_dotenv()
        env = dict(os.environ)

    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        raise ConfigError(
            "Missing required environment variable TELEGRAM_BOT_TOKEN. "
            "Set it in your .env file (see README)."
        )

    raw_user_id = (env.get("TELEGRAM_ALLOWED_USER_ID") or "").strip()
    if not raw_user_id:
        raise ConfigError(
            "Missing required environment variable TELEGRAM_ALLOWED_USER_ID. "
            "Set it in your .env file (see README)."
        )
    try:
        allowed_user_id = int(raw_user_id)
    except ValueError as exc:
        raise ConfigError(
            "TELEGRAM_ALLOWED_USER_ID must be an integer Telegram user id, "
            f"got {raw_user_id!r}."
        ) from exc

    def opt(name: str) -> str | None:
        value = (env.get(name) or "").strip()
        return value or None

    def opt_bool(name: str, default: bool) -> bool:
        raw = (env.get(name) or "").strip().lower()
        if not raw:
            return default
        if raw in ("1", "true", "yes", "on"):
            return True
        if raw in ("0", "false", "no", "off"):
            return False
        raise ConfigError(
            f"{name} must be a boolean (true/false), got {raw!r}."
        )

    def opt_int(name: str, default: int, *, lo: int, hi: int) -> int:
        raw = (env.get(name) or "").strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError as exc:
            raise ConfigError(f"{name} must be an integer, got {raw!r}.") from exc
        if not lo <= value <= hi:
            raise ConfigError(f"{name} must be between {lo} and {hi}, got {value}.")
        return value

    return Config(
        telegram_bot_token=token,
        telegram_allowed_user_id=allowed_user_id,
        anthropic_api_key=opt("ANTHROPIC_API_KEY"),
        anthropic_model=opt("ANTHROPIC_MODEL") or "claude-opus-4-8",
        linkedin_client_id=opt("LINKEDIN_CLIENT_ID"),
        linkedin_client_secret=opt("LINKEDIN_CLIENT_SECRET"),
        linkedin_redirect_uri=opt("LINKEDIN_REDIRECT_URI"),
        topics_path=opt("TOPICS_PATH") or "topics.txt",
        schedule_hour=opt_int("SCHEDULE_HOUR", 9, lo=0, hi=23),
        schedule_minute=opt_int("SCHEDULE_MINUTE", 0, lo=0, hi=59),
        dry_run=opt_bool("DRY_RUN", False),
    )
