"""Generate an image from a prompt via Together AI's images API.

``ImageGenerator`` is a thin logic module: prompt in, decoded image bytes out.
The Together client is injected so tests can mock the external boundary and stay
offline/deterministic. It has no Telegram or LinkedIn knowledge.

Prototype contract (from the Together SDK)::

    response = client.images.generate(
        prompt="...", model="Qwen/Qwen-Image", response_format="b64_json"
    )
    image_bytes = base64.b64decode(response.data[0].b64_json)
"""

from __future__ import annotations

import base64
import binascii
import logging
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "Qwen/Qwen-Image"
DEFAULT_WIDTH = 1056
DEFAULT_HEIGHT = 1320
DEFAULT_STEPS = 30

# Diffusion-side negations. This is the correct place for "do not draw X"
# instructions (the chat prompt describes only what IS in the frame).
_TEXT_NEGATIVES = (
    "text",
    "words",
    "letters",
    "typography",
    "captions",
)
_BASE_NEGATIVES = (
    "watermark",
    "signature",
    "logo",
    "brand marks",
    "charts",
    "graphs",
    "UI",
    "frame",
    "border",
    "hexagon network",
    "glowing nodes",
    "lens flare",
    "low quality",
    "blurry",
    "deformed",
    "distorted hands",
    "extra fingers",
    "oversaturated",
    "cluttered",
)


class ImageGenerationError(RuntimeError):
    """Raised when an image could not be generated or decoded."""


class ImageGenerator:
    """Generate image bytes from a text prompt using Together AI."""

    def __init__(
        self,
        client: Any,
        *,
        model: str = DEFAULT_MODEL,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        allow_text: bool = False,
    ) -> None:
        self._client = client
        self._model = model
        self._width = width
        self._height = height
        terms = _BASE_NEGATIVES if allow_text else (_TEXT_NEGATIVES + _BASE_NEGATIVES)
        self._negative_prompt = ", ".join(terms)

    def generate(self, prompt: str) -> bytes:
        """Return decoded image bytes for ``prompt``.

        Raises:
            ImageGenerationError: If the call fails, returns no data, or the
                base64 payload cannot be decoded.
        """
        try:
            response = self._client.images.generate(
                model=self._model,
                prompt=prompt,
                negative_prompt=self._negative_prompt,
                width=self._width,
                height=self._height,
                steps=DEFAULT_STEPS,
                n=1,
                response_format="b64_json",
            )
        except Exception as exc:  # noqa: BLE001 - surface any provider failure
            raise ImageGenerationError(f"image generation failed: {exc}") from exc

        data = getattr(response, "data", None) or []
        if not data:
            raise ImageGenerationError("image API returned no data")

        b64 = getattr(data[0], "b64_json", None)
        if not b64:
            raise ImageGenerationError("image API returned no b64_json payload")

        try:
            return base64.b64decode(b64)
        except (binascii.Error, ValueError) as exc:
            raise ImageGenerationError(f"could not decode image bytes: {exc}") from exc
