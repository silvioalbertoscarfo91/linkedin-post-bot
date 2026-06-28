"""Entry point: ``python -m linkedin_post_bot``.

Loads configuration (failing fast with a clear message if required env vars are
missing) and starts the Telegram bot.
"""

from __future__ import annotations

import logging
import sys

from .bot import run
from .config import ConfigError, load_config


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    run(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
