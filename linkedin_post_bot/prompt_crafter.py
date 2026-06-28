"""Craft an image-generation "super prompt" from a LinkedIn post body.

``PromptCrafter`` is a pure logic module: post text in, a single image prompt
string out. It reuses the same OpenAI-compatible NVIDIA chat client that
``PostTextGenerator`` uses (injected so tests can mock the external boundary and
stay offline/deterministic). It has no Telegram, Together, or LinkedIn
knowledge.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "mistralai/mistral-medium-3.5-128b"
MAX_TOKENS = 512
TEMPERATURE = 0.7
TOP_P = 1.0

DEFAULT_BRAND_SPINE = (
    "modern editorial 3D render, clean and tactile surfaces, restrained 2-3 "
    "colour palette, soft studio lighting, subtle depth of field, premium and "
    "uncluttered"
)

_TEXT_RULE_NO_TEXT = (
    "Do NOT render any text, words, letters, numbers, logos, watermarks, "
    "charts, or UI in the image."
)
_TEXT_RULE_ALLOW_TEXT = (
    "You MAY include at most one short, clearly-rendered word or short phrase "
    "integrated naturally into the scene (e.g. on a sign or screen) only if it "
    "strengthens the concept; otherwise keep it text-free."
)

# System prompt template. ``{brand_spine}`` and ``{text_rule}`` are filled in
# per-instance from constructor args.
SYSTEM_PROMPT_TEMPLATE = (
    "You are an art director writing prompts for the Qwen-Image text-to-image "
    "model. Given the text of a LinkedIn post, output ONE vivid "
    "image-generation prompt describing a SINGLE concrete scene that "
    "represents the post's core idea as a real situation or visual metaphor.\n"
    "Rules:\n"
    "- Describe only what IS in the frame, concretely: subject, setting, "
    "action, composition, lighting, mood. Plain nouns and verbs.\n"
    "- Always include this house style: {brand_spine}.\n"
    "- Vertical 4:5 portrait composition, clear focal subject, breathing "
    "room.\n"
    "- 60-110 words, one paragraph, no lists.\n"
    "- {text_rule}\n"
    "- Avoid clichés: no handshakes, no glowing brains, no hexagon/node "
    "networks, no suited businesspeople pointing at floating holograms, no "
    "lightbulbs for ideas, no chess pieces, no ladders to success, no generic "
    "stock-photo look.\n"
    "- Output ONLY the prompt text. No preamble, no quotes, no explanation."
)

# Few-shot exemplar anchoring quality: a sample post text and a strong prompt
# that follows the rules above.
_FEWSHOT_USER = (
    "Most teams don't fail because of a lack of ideas. They fail because "
    "decisions get stuck waiting for someone to say yes. Kill the bottleneck: "
    "push authority to the people closest to the work."
)
_FEWSHOT_ASSISTANT = (
    "A single wooden conveyor belt runs through a calm, softly lit studio "
    "space, carrying a row of identical pale-blue paper boxes from left to "
    "right. Near the centre the belt is pinched by a large smooth metal "
    "clamp, and the boxes pile up against it in a gentle jam. One box has "
    "tipped over the side and floats freely just past the clamp, escaping the "
    "queue. Vertical 4:5 portrait framing, the clamp as the clear focal "
    "subject with generous breathing room around it. Modern editorial 3D "
    "render, clean and tactile surfaces, restrained two-colour palette of warm "
    "grey and muted blue, soft studio lighting, subtle depth of field, premium "
    "and uncluttered."
)


class PromptCraftError(RuntimeError):
    """Raised when the model does not return a usable image prompt."""


def _clean(text: str) -> str:
    """Strip markdown fences and wrapping quotes from a model's plain-text reply.

    Some models wrap the answer in ``` fences or surround it with quotation
    marks even when asked for a bare string. Peel those off so the result is a
    clean prompt.
    """
    text = text.strip()
    if text.startswith("```"):
        # Drop the opening fence line (``` or ```text) and the closing fence.
        text = text.split("\n", 1)[-1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        text = text.strip()
    # Strip a single layer of matching wrapping quotes.
    for quote in ('"', "'", "“", "”"):
        if len(text) >= 2 and text[0] == quote and text[-1] == quote:
            text = text[1:-1].strip()
            break
    return text.strip()


class PromptCrafter:
    """Derive an image super-prompt from a post via NVIDIA's chat API."""

    def __init__(
        self,
        client: Any,
        *,
        model: str = DEFAULT_MODEL,
        max_tokens: int = MAX_TOKENS,
        brand_spine: str = DEFAULT_BRAND_SPINE,
        allow_text: bool = False,
    ) -> None:
        self._client = client
        self._model = model
        self._max_tokens = max_tokens
        text_rule = _TEXT_RULE_ALLOW_TEXT if allow_text else _TEXT_RULE_NO_TEXT
        self._system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            brand_spine=brand_spine, text_rule=text_rule
        )

    def craft(self, post_text: str) -> str:
        """Return a cleaned, non-empty image prompt derived from ``post_text``.

        Raises:
            PromptCraftError: If the model returns empty/unusable output.
        """
        response = self._client.chat.completions.create(
            model=self._model,
            max_tokens=self._max_tokens,
            temperature=TEMPERATURE,
            top_p=TOP_P,
            messages=[
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": _FEWSHOT_USER},
                {"role": "assistant", "content": _FEWSHOT_ASSISTANT},
                {"role": "user", "content": post_text},
            ],
        )
        prompt = _clean(self._extract_text(response))
        if not prompt:
            raise PromptCraftError("model returned an empty image prompt")
        return prompt

    @staticmethod
    def _extract_text(response: Any) -> str:
        """Read the message content of an OpenAI-style chat completion response."""
        choices = getattr(response, "choices", None) or []
        if not choices:
            return ""
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None) if message is not None else None
        return content or ""
