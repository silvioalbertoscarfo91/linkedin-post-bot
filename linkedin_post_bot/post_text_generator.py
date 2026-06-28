"""Candidate post generation via NVIDIA's OpenAI-compatible API.

``PostTextGenerator`` is a pure logic module: topic in, list of candidate strings
out. It has no Telegram or LinkedIn knowledge. The ``openai`` client is
injected so tests can mock the external boundary and stay offline/deterministic.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _unwrap_json(text: str) -> str:
    """Strip markdown code fences / surrounding prose so ``json.loads`` succeeds.

    Some models (e.g. Mistral) wrap JSON in ```json ... ``` fences or add a
    sentence around it even when asked for raw JSON. Pull out the first JSON
    object/array if a fence or prose is present; otherwise return as-is.
    """
    text = text.strip()
    if text.startswith("```"):
        # drop opening fence line (``` or ```json) and the closing fence
        text = text.split("\n", 1)[-1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        text = text.strip()
    if not text.startswith(("{", "[")):
        # locate the first JSON object/array embedded in surrounding prose
        starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
        ends = [i for i in (text.rfind("}"), text.rfind("]")) if i != -1]
        if starts and ends:
            text = text[min(starts) : max(ends) + 1]
    return text.strip()

DEFAULT_MODEL = "mistralai/mistral-medium-3.5-128b"
MAX_TOKENS = 4096
MAX_ATTEMPTS = 2
TEMPERATURE = 0.85
TOP_P = 1.0

AUTHOR_PROFILE = (
    "Senior full-stack engineer in Bern, Switzerland. 5+ years shipping React "
    "Native, Spring Boot, Kafka, and AWS at scale (incl. a large Swiss "
    "government customs system). Now building agentic AI systems and tooling, "
    "hands-on with Claude Code daily. Writes from real engineering experience, "
    "not thought-leadership abstraction. Opinionated, concrete, allergic to "
    "hype. Angle: what AI-assisted engineering actually looks like from the "
    "trenches."
)

SYSTEM_PROMPT_TEMPLATE = (
    "You write LinkedIn posts for a software engineer growing a following. "
    "Audience: engineers, eng leaders, and people building with AI - highly "
    "technical, allergic to marketing voice and AI-generated filler.\n\n"
    "AUTHOR (write as this person, in first person, grounded in their real "
    "experience - never invent specifics that contradict it):\n"
    "{author_profile}\n\n"
    "Each post MUST:\n"
    "- Open with a HOOK in the first line that stops the scroll on its own (a "
    "sharp claim, a concrete number, a contrarian take, or a 'here's what "
    "broke' moment). No warm-up, no 'In today's world', no throat-clearing. "
    "Assume everything after line 2 is hidden behind 'see more' until the hook "
    "earns the click.\n"
    "- Make ONE specific, opinionated point backed by a concrete detail, "
    "story, or number from the author's world. Specificity over breadth. No "
    "generic advice anyone could write.\n"
    "- Be skimmable: short lines, frequent line breaks, whitespace. No dense "
    "paragraphs.\n"
    "- End with ONE engagement driver: a genuine, easy-to-answer question OR a "
    "claim sharp enough that people want to reply. Vary this across posts.\n"
    "- Run 50-180 words. Shorter and punchier beats longer and complete.\n\n"
    "Across the candidates, vary the FORMAT meaningfully: e.g. one contrarian "
    "take, one concrete how-it-actually-works lesson, one short story with a "
    "takeaway. Different hook style each.\n\n"
    "NEVER use: emojis as bullets or decoration; 'I'm thrilled/excited to'; "
    "'game-changer', 'leverage', 'unlock', 'in today's fast-paced world', 'let "
    "that sink in', 'the result?'; the 'It's not X, it's Y' cadence; "
    "rhetorical-question-then-immediate-answer rhythm; bold section headers. "
    "Hashtags: 0-3 max, relevant, only at the very end - or none.\n\n"
    "Return ONLY a JSON object {{\"posts\": [\"...\", \"...\"]}} with exactly "
    "the requested number of posts, and no other text."
)


class PostTextGenerationError(RuntimeError):
    """Raised when the model output cannot be turned into ``n`` candidates."""


class PostTextGenerator:
    """Generate candidate LinkedIn posts for a topic using NVIDIA's API."""

    def __init__(
        self,
        client: Any,
        *,
        model: str = DEFAULT_MODEL,
        max_tokens: int = MAX_TOKENS,
        author_profile: str = AUTHOR_PROFILE,
    ) -> None:
        self._client = client
        self._model = model
        self._max_tokens = max_tokens
        self._system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            author_profile=author_profile
        )

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
            PostTextGenerationError: If the model never yields exactly ``n`` usable
                candidates after retrying.
        """
        avoid = avoid or []
        prompt = self._build_prompt(topic, n, avoid)

        last_error: str | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            response = self._client.chat.completions.create(
                model=self._model,
                max_tokens=self._max_tokens,
                temperature=TEMPERATURE,
                top_p=TOP_P,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": prompt},
                ],
            )
            try:
                posts = self._parse(response, n)
            except PostTextGenerationError as exc:
                last_error = str(exc)
                logger.warning(
                    "PostTextGenerator attempt %d/%d failed: %s",
                    attempt,
                    MAX_ATTEMPTS,
                    last_error,
                )
                continue
            return posts

        raise PostTextGenerationError(
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
        """Read the message content of an OpenAI-style chat completion response."""
        choices = getattr(response, "choices", None) or []
        if not choices:
            return ""
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None) if message is not None else None
        return _unwrap_json(content or "")

    def _parse(self, response: Any, n: int) -> list[str]:
        text = self._extract_text(response)
        if not text:
            raise PostTextGenerationError("empty model response")

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PostTextGenerationError(f"response was not valid JSON: {exc}") from exc

        if not isinstance(data, dict) or "posts" not in data:
            raise PostTextGenerationError("response JSON missing 'posts' key")

        raw_posts = data["posts"]
        if not isinstance(raw_posts, list):
            raise PostTextGenerationError("'posts' was not a list")

        posts = [p.strip() for p in raw_posts if isinstance(p, str) and p.strip()]
        if len(posts) != n:
            raise PostTextGenerationError(
                f"expected {n} candidates, got {len(posts)}"
            )
        return posts
