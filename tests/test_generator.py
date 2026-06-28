import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from linkedin_post_bot.generator import GenerationError, PostGenerator


def _text_response(text):
    """Build a fake anthropic message response with one text block."""
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


def _client_returning(*texts):
    """Mock anthropic client whose messages.create returns the given texts in order."""
    client = MagicMock()
    client.messages.create.side_effect = [_text_response(t) for t in texts]
    return client


def test_returns_exactly_n_candidates():
    payload = json.dumps({"posts": ["one", "two", "three"]})
    client = _client_returning(payload)
    gen = PostGenerator(client, model="claude-opus-4-8")

    posts = gen.generate("AI in finance", n=3)

    assert posts == ["one", "two", "three"]
    client.messages.create.assert_called_once()
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-opus-4-8"
    assert kwargs["messages"][0]["role"] == "user"


def test_avoid_candidates_included_in_request_context():
    payload = json.dumps({"posts": ["fresh1", "fresh2", "fresh3"]})
    client = _client_returning(payload)
    gen = PostGenerator(client)

    avoid = ["old candidate alpha", "old candidate beta"]
    gen.generate("topic", n=3, avoid=avoid)

    sent = client.messages.create.call_args.kwargs["messages"][0]["content"]
    for old in avoid:
        assert old in sent


def test_avoid_not_present_when_empty():
    payload = json.dumps({"posts": ["a", "b", "c"]})
    client = _client_returning(payload)
    gen = PostGenerator(client)

    gen.generate("topic", n=3)

    sent = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "previously-shown" not in sent


def test_short_output_retries_then_raises():
    short = json.dumps({"posts": ["only one"]})
    client = _client_returning(short, short)
    gen = PostGenerator(client)

    with pytest.raises(GenerationError):
        gen.generate("topic", n=3)

    # Retried once after the first malformed response.
    assert client.messages.create.call_count == 2


def test_short_output_then_valid_succeeds():
    short = json.dumps({"posts": ["only one"]})
    good = json.dumps({"posts": ["a", "b", "c"]})
    client = _client_returning(short, good)
    gen = PostGenerator(client)

    posts = gen.generate("topic", n=3)

    assert posts == ["a", "b", "c"]
    assert client.messages.create.call_count == 2


def test_non_json_output_raises():
    client = _client_returning("here are some posts!", "still not json")
    gen = PostGenerator(client)

    with pytest.raises(GenerationError):
        gen.generate("topic", n=3)


def test_empty_response_raises():
    client = _client_returning("", "")
    gen = PostGenerator(client)

    with pytest.raises(GenerationError):
        gen.generate("topic", n=3)
