import base64
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from linkedin_post_bot.image_generator import ImageGenerationError, ImageGenerator


def _response(b64):
    return SimpleNamespace(data=[SimpleNamespace(b64_json=b64)])


def _client_returning(b64):
    client = MagicMock()
    client.images.generate.return_value = _response(b64)
    return client


def test_decodes_b64_json_to_bytes():
    raw = b"\x89PNG\r\n\x1a\nfake-image-bytes"
    client = _client_returning(base64.b64encode(raw).decode())
    gen = ImageGenerator(client, model="Qwen/Qwen-Image")

    out = gen.generate("a clean professional photo")

    assert out == raw
    kwargs = client.images.generate.call_args.kwargs
    assert kwargs["model"] == "Qwen/Qwen-Image"
    assert kwargs["prompt"] == "a clean professional photo"
    assert kwargs["response_format"] == "b64_json"
    assert kwargs["width"] == 1056
    assert kwargs["height"] == 1320
    assert kwargs["n"] == 1
    assert "negative_prompt" in kwargs


def test_default_negative_prompt_includes_text_negations():
    client = _client_returning(base64.b64encode(b"x").decode())
    gen = ImageGenerator(client)
    gen.generate("prompt")
    neg = client.images.generate.call_args.kwargs["negative_prompt"]
    for term in ("text", "words", "letters", "typography", "captions"):
        assert term in neg
    # Base negations are always present.
    assert "watermark" in neg
    assert "blurry" in neg


def test_allow_text_drops_text_terms_from_negative_prompt():
    client = _client_returning(base64.b64encode(b"x").decode())
    gen = ImageGenerator(client, allow_text=True)
    gen.generate("prompt")
    neg = client.images.generate.call_args.kwargs["negative_prompt"]
    for term in ("text", "words", "letters", "typography", "captions"):
        assert term not in neg
    # Non-text negations remain.
    assert "watermark" in neg
    assert "blurry" in neg


def test_width_height_injectable():
    client = _client_returning(base64.b64encode(b"x").decode())
    gen = ImageGenerator(client, width=512, height=768)
    gen.generate("prompt")
    kwargs = client.images.generate.call_args.kwargs
    assert kwargs["width"] == 512
    assert kwargs["height"] == 768


def test_empty_data_raises():
    client = MagicMock()
    client.images.generate.return_value = SimpleNamespace(data=[])
    gen = ImageGenerator(client)
    with pytest.raises(ImageGenerationError):
        gen.generate("prompt")


def test_missing_b64_payload_raises():
    client = _client_returning(None)
    gen = ImageGenerator(client)
    with pytest.raises(ImageGenerationError):
        gen.generate("prompt")


def test_provider_exception_wrapped():
    client = MagicMock()
    client.images.generate.side_effect = RuntimeError("Together down")
    gen = ImageGenerator(client)
    with pytest.raises(ImageGenerationError):
        gen.generate("prompt")


def test_undecodable_payload_raises():
    client = _client_returning("not-valid-base64!!!")
    gen = ImageGenerator(client)
    with pytest.raises(ImageGenerationError):
        gen.generate("prompt")
