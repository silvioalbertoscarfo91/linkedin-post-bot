from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from linkedin_post_bot.prompt_crafter import PromptCrafter, PromptCraftError


def _text_response(text):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
    )


def _client_returning(text):
    client = MagicMock()
    client.chat.completions.create.return_value = _text_response(text)
    return client


def test_returns_cleaned_prompt():
    client = _client_returning("A confident professional in a bright modern office")
    crafter = PromptCrafter(client, model="mistralai/mistral-medium-3.5-128b")

    prompt = crafter.craft("My post about leadership")

    assert prompt == "A confident professional in a bright modern office"
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "mistralai/mistral-medium-3.5-128b"
    messages = kwargs["messages"]
    assert messages[0]["role"] == "system"
    # The real user post is the LAST message (after the few-shot exemplar).
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == "My post about leadership"


def test_system_prompt_includes_brand_spine_default():
    client = _client_returning("a scene")
    crafter = PromptCrafter(client)
    crafter.craft("post")
    system = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "modern editorial 3D render" in system
    assert "restrained 2-3 colour palette" in system


def test_system_prompt_includes_custom_brand_spine():
    client = _client_returning("a scene")
    crafter = PromptCrafter(client, brand_spine="flat pastel illustration")
    crafter.craft("post")
    system = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "flat pastel illustration" in system


def test_text_rule_flips_with_allow_text():
    client_no = _client_returning("a scene")
    PromptCrafter(client_no, allow_text=False).craft("post")
    system_no = client_no.chat.completions.create.call_args.kwargs["messages"][0][
        "content"
    ]
    assert "Do NOT render any text" in system_no
    assert "MAY include at most one short" not in system_no

    client_yes = _client_returning("a scene")
    PromptCrafter(client_yes, allow_text=True).craft("post")
    system_yes = client_yes.chat.completions.create.call_args.kwargs["messages"][0][
        "content"
    ]
    assert "MAY include at most one short" in system_yes
    assert "Do NOT render any text" not in system_yes


def test_includes_fewshot_exemplar():
    client = _client_returning("a scene")
    PromptCrafter(client).craft("post")
    messages = client.chat.completions.create.call_args.kwargs["messages"]
    roles = [m["role"] for m in messages]
    # system, few-shot user, few-shot assistant, real user.
    assert roles == ["system", "user", "assistant", "user"]
    assert messages[2]["content"].strip()  # exemplar assistant prompt is non-empty


def test_strips_code_fences():
    client = _client_returning("```\nA serene minimalist desk scene\n```")
    crafter = PromptCrafter(client)
    assert crafter.craft("post") == "A serene minimalist desk scene"


def test_strips_wrapping_quotes():
    client = _client_returning('"A bold abstract data visualization"')
    crafter = PromptCrafter(client)
    assert crafter.craft("post") == "A bold abstract data visualization"


def test_empty_output_raises():
    client = _client_returning("   ")
    crafter = PromptCrafter(client)
    with pytest.raises(PromptCraftError):
        crafter.craft("post")


def test_no_choices_raises():
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(choices=[])
    crafter = PromptCrafter(client)
    with pytest.raises(PromptCraftError):
        crafter.craft("post")
