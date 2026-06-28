"""Topic rotation: a persisted round-robin over a list of topics.

The scheduled daily run picks the next topic from a predefined rotation list so
automated posts stay varied and on-brand (no manual action). The rotation index
is persisted to a small sidecar JSON file so the round-robin position survives a
restart of the process.

This module is pure logic with file IO at its edge so it can be unit-tested
offline: ``next_topic()`` reads the topics file, returns the topic at the
current index, then advances and persists the index.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TOPICS_PATH = "topics.txt"


class RotationError(RuntimeError):
    """Raised when the rotation cannot produce a topic (missing/empty file)."""


def load_topics(topics_path: str | Path) -> list[str]:
    """Read non-empty, non-comment lines from the topics file.

    Blank lines and lines starting with ``#`` are ignored so the file can be
    annotated. Raises :class:`RotationError` with a clear message if the file is
    missing or yields no usable topics.
    """
    path = Path(topics_path)
    if not path.exists():
        raise RotationError(
            f"Topics file not found: {path}. Create it with one topic per line "
            "(see README) so the scheduler has something to post about."
        )

    topics: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        topics.append(line)

    if not topics:
        raise RotationError(
            f"Topics file {path} is empty. Add at least one topic (one per line) "
            "so the scheduler has something to post about."
        )
    return topics


class TopicRotation:
    """Round-robin over a topics file with an index persisted across runs."""

    def __init__(
        self,
        topics_path: str | Path = DEFAULT_TOPICS_PATH,
        index_path: str | Path | None = None,
    ) -> None:
        self._topics_path = Path(topics_path)
        # Index lives in a sidecar next to the topics file by default.
        self._index_path = (
            Path(index_path)
            if index_path is not None
            else self._topics_path.with_suffix(self._topics_path.suffix + ".index.json")
        )
        # Fail fast at construction time so an empty/missing topics file surfaces
        # as a clear startup error rather than crashing silently at fire time.
        load_topics(self._topics_path)

    def _load_index(self) -> int:
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return 0
        except (json.JSONDecodeError, OSError):
            logger.warning(
                "Could not read rotation index %s; restarting from 0",
                self._index_path,
            )
            return 0
        index = data.get("index", 0)
        return index if isinstance(index, int) and index >= 0 else 0

    def _save_index(self, index: int) -> None:
        self._index_path.write_text(
            json.dumps({"index": index}), encoding="utf-8"
        )

    def next_topic(self) -> str:
        """Return the current topic, then advance and persist the index.

        Reads the topics file fresh each call so edits to the rotation take
        effect without a restart. The index wraps modulo the topic count.
        """
        topics = load_topics(self._topics_path)
        index = self._load_index() % len(topics)
        topic = topics[index]
        self._save_index((index + 1) % len(topics))
        return topic
