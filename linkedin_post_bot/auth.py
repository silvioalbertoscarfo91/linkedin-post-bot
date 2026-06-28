"""Authorization helpers.

Only a single allowed Telegram user id is served; everyone else is ignored.
"""

from __future__ import annotations


def is_authorized(user_id: int | None, allowed_user_id: int) -> bool:
    """Return ``True`` only if ``user_id`` matches the configured allowed id."""
    return user_id is not None and user_id == allowed_user_id
