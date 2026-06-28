"""Candidate post generation via Claude.

``PostGenerator`` is a pure logic module: topic in, list of candidate strings
out. It has no Telegram or LinkedIn knowledge. The ``anthropic`` client is
injected so tests can mock the external boundary and stay offline/deterministic.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-4-8"
MAX_TOKENS = 4096
MAX_ATTEMPTS = 2

SYSTEM_PROMPT = (
    "You write LinkedIn posts. Produce distinct, ready-to-publish candidate "
    "posts on the given topic. Each post must:\n"
    "- be written in first person, professional but conversational tone;\n"
    "- be self-contained and publishable without edits;\n"
    "- respect a reasonable LinkedIn length (roughly 80-200 words, never over "
    "3000 characters);\n"
    "- differ meaningfully from the others in angle, structure, or hook.\n"
    "Return ONLY a JSON object of the form "
    '{"posts": ["...", "..."]} with exactly the requested number of posts, '
    "and no other text."
)


class GenerationError(RuntimeError):
    """Raised when the model output cannot be turned into ``n`` candidates."""


class PostGenerator:
    """Generate candidate LinkedIn posts for a topic using Claude."""

    def __init__(
        self,
        client: Any,
        *,
        model: str = DEFAULT_MODEL,
        max_tokens: int = MAX_TOKENS,
    ) -> None:
        self._client = client
        self._model = model
        self._max_tokens = max_tokens

    def generate(
        self,
        topic: str,
        n: int = 3,
        avoid: list[str] | None = None,
    ) -> list[str]:
        """Return exactly ``n`` distinct candidate posts about ``topic``.

        Args:
            topic: Subject of the posts.
            n: Number of candidates to produce.
            avoid: Previously-shown candidates to exclude, so regeneration
                yields genuinely fresh text.

        Raises:
            GenerationError: If the model never yields exactly ``n`` usable
                candidates after retrying.
        """
        avoid = avoid or []
        prompt = self._build_prompt(topic, n, avoid)

        last_error: str | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            try:
                posts = self._parse(response, n)
            except GenerationError as exc:
                last_error = str(exc)
                logger.warning(
                    "PostGenerator attempt %d/%d failed: %s",
                    attempt,
                    MAX_ATTEMPTS,
                    last_error,
                )
                continue
            return posts

        raise GenerationError(
            f"Model did not return {n} valid candidates: {last_error}"
        )

    def _build_prompt(self, topic: str, n: int, avoid: list[str]) -> str:
        parts = [f"Topic: {topic}", f"Write exactly {n} candidate posts."]
        if avoid:
            joined = "\n".join(f"- {text}" for text in avoid)
            parts.append(
                "Do NOT repeat or lightly reword any of these previously-shown "
                f"candidates; produce genuinely new posts:\n{joined}"
            )
        return "\n\n".join(parts)

    @staticmethod
    def _extract_text(response: Any) -> str:
        """Concatenate the text blocks of an ``anthropic`` message response."""
        chunks: list[str] = []
        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", None) == "text":
                chunks.append(getattr(block, "text", "") or "")
        return "".join(chunks).strip()

    def _parse(self, response: Any, n: int) -> list[str]:
        text = self._extract_text(response)
        if not text:
            raise GenerationError("empty model response")

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise GenerationError(f"response was not valid JSON: {exc}") from exc

        if not isinstance(data, dict) or "posts" not in data:
            raise GenerationError("response JSON missing 'posts' key")

        raw_posts = data["posts"]
        if not isinstance(raw_posts, list):
            raise GenerationError("'posts' was not a list")

        posts = [p.strip() for p in raw_posts if isinstance(p, str) and p.strip()]
        if len(posts) != n:
            raise GenerationError(
                f"expected {n} candidates, got {len(posts)}"
            )
        return posts
