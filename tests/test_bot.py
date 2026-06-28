from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from linkedin_post_bot.bot import (
    AWAITING_IMAGE_PROMPT_KEY,
    AWAITING_POST_KEY,
    NO_API_KEY_REPLY,
    NO_TOPIC_REPLY,
    PASTE_PROMPT,
    genera_command,
    manual_text_handler,
    posta_command,
)
from linkedin_post_bot.config import Config

CONFIG = Config(
    telegram_bot_token="tok",
    telegram_allowed_user_id=42,
    nvidia_api_key="nvapi-test",
)


def _make_update(user_id, args, *, text=None, config=CONFIG):
    message = SimpleNamespace(
        reply_text=AsyncMock(), chat_id=1000, text=text
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id) if user_id is not None else None,
        effective_message=message,
    )
    orchestrator = SimpleNamespace(
        run=AsyncMock(), present_manual=AsyncMock()
    )
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={"config": config, "orchestrator": orchestrator}
        ),
        args=args,
        chat_data={},
    )
    return update, context, message, orchestrator


@pytest.mark.asyncio
async def test_authorized_topic_runs_orchestrator():
    update, context, message, orchestrator = _make_update(42, ["AI", "in", "finance"])
    await genera_command(update, context)
    orchestrator.run.assert_awaited_once_with("AI in finance", 1000)
    message.reply_text.assert_not_called()


@pytest.mark.asyncio
async def test_no_topic_prompts_for_topic():
    update, context, message, orchestrator = _make_update(42, [])
    await genera_command(update, context)
    message.reply_text.assert_awaited_once_with(NO_TOPIC_REPLY)
    orchestrator.run.assert_not_called()


@pytest.mark.asyncio
async def test_unauthorized_user_ignored():
    update, context, message, orchestrator = _make_update(99, ["whatever"])
    await genera_command(update, context)
    message.reply_text.assert_not_called()
    orchestrator.run.assert_not_called()


@pytest.mark.asyncio
async def test_missing_user_ignored():
    update, context, message, orchestrator = _make_update(None, ["whatever"])
    await genera_command(update, context)
    message.reply_text.assert_not_called()
    orchestrator.run.assert_not_called()


@pytest.mark.asyncio
async def test_genera_without_api_key_replies_key_required():
    no_key = replace(CONFIG, nvidia_api_key=None)
    update, context, message, orchestrator = _make_update(
        42, ["AI", "in", "finance"], config=no_key
    )
    await genera_command(update, context)
    message.reply_text.assert_awaited_once_with(NO_API_KEY_REPLY)
    orchestrator.run.assert_not_called()


@pytest.mark.asyncio
async def test_posta_prompts_and_sets_awaiting_flag():
    update, context, message, orchestrator = _make_update(42, [])
    await posta_command(update, context)
    message.reply_text.assert_awaited_once_with(PASTE_PROMPT)
    assert context.chat_data[AWAITING_POST_KEY] is True


@pytest.mark.asyncio
async def test_posta_unauthorized_user_ignored():
    update, context, message, orchestrator = _make_update(99, [])
    await posta_command(update, context)
    message.reply_text.assert_not_called()
    assert AWAITING_POST_KEY not in context.chat_data


@pytest.mark.asyncio
async def test_manual_text_captured_and_presented():
    update, context, message, orchestrator = _make_update(
        42, [], text="My handwritten post"
    )
    context.chat_data[AWAITING_POST_KEY] = True

    await manual_text_handler(update, context)

    orchestrator.present_manual.assert_awaited_once_with("My handwritten post", 1000)
    # Flag cleared so the *next* plain message isn't captured.
    assert context.chat_data[AWAITING_POST_KEY] is False


@pytest.mark.asyncio
async def test_manual_text_ignored_when_not_awaiting():
    update, context, message, orchestrator = _make_update(
        42, [], text="random chatter"
    )
    await manual_text_handler(update, context)
    orchestrator.present_manual.assert_not_called()


@pytest.mark.asyncio
async def test_manual_text_ignored_for_unauthorized_user():
    update, context, message, orchestrator = _make_update(
        99, [], text="hello"
    )
    context.chat_data[AWAITING_POST_KEY] = True
    await manual_text_handler(update, context)
    orchestrator.present_manual.assert_not_called()


# --- own-prompt capture routing (Task 03) --------------------------------


def _add_submit_own_prompt(orchestrator):
    orchestrator.submit_own_prompt = AsyncMock()
    return orchestrator


@pytest.mark.asyncio
async def test_image_prompt_captured_and_routed_to_submit():
    update, context, message, orchestrator = _make_update(
        42, [], text="a serene mountain at dawn"
    )
    _add_submit_own_prompt(orchestrator)
    context.chat_data[AWAITING_IMAGE_PROMPT_KEY] = "sess-123"

    await manual_text_handler(update, context)

    orchestrator.submit_own_prompt.assert_awaited_once_with(
        "sess-123", "a serene mountain at dawn"
    )
    # Capture flag cleared; manual-post flow untouched.
    assert AWAITING_IMAGE_PROMPT_KEY not in context.chat_data
    orchestrator.present_manual.assert_not_called()


@pytest.mark.asyncio
async def test_image_prompt_capture_does_not_collide_with_posta():
    # Only the manual-post flag is armed -> routes to present_manual, not image.
    update, context, message, orchestrator = _make_update(
        42, [], text="a manual post"
    )
    _add_submit_own_prompt(orchestrator)
    context.chat_data[AWAITING_POST_KEY] = True

    await manual_text_handler(update, context)

    orchestrator.present_manual.assert_awaited_once_with("a manual post", 1000)
    orchestrator.submit_own_prompt.assert_not_called()


@pytest.mark.asyncio
async def test_image_prompt_capture_takes_precedence_when_both_armed():
    # If both flags are somehow set, the image capture is consumed first and
    # nothing is published as a manual post.
    update, context, message, orchestrator = _make_update(
        42, [], text="qwen prompt text"
    )
    _add_submit_own_prompt(orchestrator)
    context.chat_data[AWAITING_POST_KEY] = True
    context.chat_data[AWAITING_IMAGE_PROMPT_KEY] = "sess-9"

    await manual_text_handler(update, context)

    orchestrator.submit_own_prompt.assert_awaited_once_with("sess-9", "qwen prompt text")
    orchestrator.present_manual.assert_not_called()


@pytest.mark.asyncio
async def test_image_prompt_capture_ignored_for_unauthorized_user():
    update, context, message, orchestrator = _make_update(
        99, [], text="qwen prompt"
    )
    _add_submit_own_prompt(orchestrator)
    context.chat_data[AWAITING_IMAGE_PROMPT_KEY] = "sess-1"

    await manual_text_handler(update, context)

    orchestrator.submit_own_prompt.assert_not_called()


@pytest.mark.asyncio
async def test_empty_image_prompt_clears_flag_and_does_not_submit():
    update, context, message, orchestrator = _make_update(42, [], text="   ")
    _add_submit_own_prompt(orchestrator)
    context.chat_data[AWAITING_IMAGE_PROMPT_KEY] = "sess-1"

    await manual_text_handler(update, context)

    orchestrator.submit_own_prompt.assert_not_called()
    assert AWAITING_IMAGE_PROMPT_KEY not in context.chat_data
    message.reply_text.assert_awaited_once()
