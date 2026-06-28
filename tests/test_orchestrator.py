from unittest.mock import AsyncMock, MagicMock

import pytest

from linkedin_post_bot.orchestrator import (
    STATUS_CANCELLED,
    STATUS_OPEN,
    STATUS_PUBLISHED,
    Orchestrator,
)


class FakeGenerator:
    """Deterministic stand-in for PostTextGenerator that records `avoid`."""

    def __init__(self):
        self.calls = []
        self._batch = 0

    def generate(self, topic, n=3, avoid=None):
        self.calls.append({"topic": topic, "n": n, "avoid": list(avoid or [])})
        self._batch += 1
        return [f"{topic} post {self._batch}.{i}" for i in range(1, n + 1)]


class FakePublisher:
    """Stand-in for LinkedInPublisher recording publish calls."""

    def __init__(self, *, url="https://www.linkedin.com/feed/update/urn:li:share:1",
                 error=None):
        self.calls = []
        self.image_calls = []
        self._url = url
        self._error = error

    def publish(self, text):
        self.calls.append(text)
        if self._error is not None:
            raise self._error
        return self._url

    def publish_with_image(self, text, image_bytes, alt_text=None):
        self.image_calls.append((text, image_bytes, alt_text))
        if self._error is not None:
            raise self._error
        return self._url


class FakePromptCrafter:
    """Deterministic stand-in for PromptCrafter recording crafted texts."""

    def __init__(self, *, prompt="super prompt", error=None):
        self.calls = []
        self._prompt = prompt
        self._error = error

    def craft(self, post_text):
        self.calls.append(post_text)
        if self._error is not None:
            raise self._error
        return self._prompt


class FakeImageGenerator:
    """Stand-in for ImageGenerator recording prompts, returning fixed bytes."""

    def __init__(self, *, image=b"\x89PNG-bytes", error=None):
        self.calls = []
        self._image = image
        self._error = error

    def generate(self, prompt):
        self.calls.append(prompt)
        if self._error is not None:
            raise self._error
        return self._image


def _fake_bot():
    return MagicMock(
        present=AsyncMock(),
        present_manual=AsyncMock(),
        present_decision=AsyncMock(),
        present_image_preview=AsyncMock(),
        acknowledge=AsyncMock(),
        confirm=AsyncMock(),
        confirm_dry_run=AsyncMock(),
        cancel=AsyncMock(),
        send_error=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_run_generates_and_presents():
    gen = FakeGenerator()
    bot = _fake_bot()
    orch = Orchestrator(gen, bot)

    session_id = await orch.run("AI in finance", chat_id=7)

    assert gen.calls == [{"topic": "AI in finance", "n": 3, "avoid": []}]
    bot.present.assert_awaited_once()
    chat_id, sid, posts = bot.present.await_args.args
    assert chat_id == 7
    assert sid == session_id
    assert len(posts) == 3


@pytest.mark.asyncio
async def test_select_opens_decision_step_without_publishing():
    gen = FakeGenerator()
    bot = _fake_bot()
    pub = FakePublisher()
    orch = Orchestrator(gen, bot, pub)

    session_id = await orch.run("topic", chat_id=7)
    _, _, posts = bot.present.await_args.args

    await orch.handle_callback(session_id, "sel2")

    # No publish yet: selecting opens a Publish / Cancel decision step.
    assert pub.calls == []
    bot.confirm.assert_not_called()
    bot.present_decision.assert_awaited_once()
    chat_id, sid, chosen = bot.present_decision.await_args.args
    assert chat_id == 7
    assert sid == session_id
    assert chosen == posts[1]
    # Image feature disabled -> "Add image" not offered.
    assert bot.present_decision.await_args.kwargs["image_enabled"] is False
    assert orch._sessions[session_id].status == STATUS_OPEN


@pytest.mark.asyncio
async def test_select_then_publish_confirms_with_link():
    gen = FakeGenerator()
    bot = _fake_bot()
    pub = FakePublisher(url="https://www.linkedin.com/feed/update/urn:li:share:42")
    orch = Orchestrator(gen, bot, pub)

    session_id = await orch.run("topic", chat_id=7)
    _, _, posts = bot.present.await_args.args

    await orch.handle_callback(session_id, "sel2")
    await orch.handle_callback(session_id, "pub")

    # Published exactly the chosen text, once.
    assert pub.calls == [posts[1]]

    # Confirmation carries the live link.
    bot.confirm.assert_awaited_once()
    chat_id, sid, chosen, url = bot.confirm.await_args.args
    assert chat_id == 7
    assert chosen == posts[1]
    assert url == "https://www.linkedin.com/feed/update/urn:li:share:42"
    bot.send_error.assert_not_called()


@pytest.mark.asyncio
async def test_publish_failure_reports_error_and_session_stays_open():
    gen = FakeGenerator()
    bot = _fake_bot()
    pub = FakePublisher(error=RuntimeError("LinkedIn 500"))
    orch = Orchestrator(gen, bot, pub)

    session_id = await orch.run("topic", chat_id=7)
    await orch.handle_callback(session_id, "sel1")
    await orch.handle_callback(session_id, "pub")

    # Clear error, no false success.
    bot.send_error.assert_awaited_once()
    bot.confirm.assert_not_called()

    # Session left open so the user can retry; not marked published.
    assert orch._sessions[session_id].status == STATUS_OPEN

    # A retry is allowed and publishes.
    await orch.handle_callback(session_id, "pub")
    assert len(pub.calls) == 2


@pytest.mark.asyncio
async def test_regenerate_uses_avoid_with_previous_candidates():
    gen = FakeGenerator()
    bot = _fake_bot()
    orch = Orchestrator(gen, bot)

    session_id = await orch.run("topic", chat_id=7)
    _, _, first = bot.present.await_args.args

    await orch.handle_callback(session_id, "regen")

    # Second generate call must avoid the first batch.
    assert gen.calls[1]["topic"] == "topic"
    assert gen.calls[1]["avoid"] == first

    # Re-presented with a new, distinct set.
    assert bot.present.await_count == 2
    _, _, second = bot.present.await_args.args
    assert set(second).isdisjoint(set(first))


@pytest.mark.asyncio
async def test_double_callback_publishes_once():
    gen = FakeGenerator()
    bot = _fake_bot()
    pub = FakePublisher()
    orch = Orchestrator(gen, bot, pub)

    session_id = await orch.run("topic", chat_id=7)

    await orch.handle_callback(session_id, "sel1")
    await orch.handle_callback(session_id, "pub")
    await orch.handle_callback(session_id, "pub")

    # Idempotency guard: only the first publish publishes and confirms.
    assert len(pub.calls) == 1
    assert bot.confirm.await_count == 1


@pytest.mark.asyncio
async def test_generation_failure_reports_error_and_no_session():
    gen = MagicMock()
    gen.generate.side_effect = RuntimeError("boom")
    bot = _fake_bot()
    orch = Orchestrator(gen, bot)

    with pytest.raises(RuntimeError):
        await orch.run("topic", chat_id=7)

    bot.present.assert_not_called()
    bot.send_error.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_session_callback_ignored():
    gen = FakeGenerator()
    bot = _fake_bot()
    orch = Orchestrator(gen, bot)

    await orch.handle_callback("nonexistent", "sel1")

    bot.confirm.assert_not_called()


@pytest.mark.asyncio
async def test_status_transitions_open_to_published():
    gen = FakeGenerator()
    bot = _fake_bot()
    pub = FakePublisher()
    orch = Orchestrator(gen, bot, pub)

    session_id = await orch.run("topic", chat_id=7)
    assert orch._sessions[session_id].status == STATUS_OPEN

    await orch.handle_callback(session_id, "sel1")
    assert orch._sessions[session_id].status == STATUS_OPEN

    await orch.handle_callback(session_id, "pub")
    assert orch._sessions[session_id].status == STATUS_PUBLISHED


@pytest.mark.asyncio
async def test_present_manual_shows_single_candidate_with_buttons():
    bot = _fake_bot()
    orch = Orchestrator(None, bot)

    session_id = await orch.present_manual("My own post text", chat_id=7)

    bot.present_manual.assert_awaited_once_with(7, session_id, "My own post text")
    session = orch._sessions[session_id]
    assert session.candidates == ["My own post text"]
    assert session.status == STATUS_OPEN


@pytest.mark.asyncio
async def test_manual_publish_calls_publisher_and_confirms():
    bot = _fake_bot()
    pub = FakePublisher(url="https://www.linkedin.com/feed/update/urn:li:share:7")
    orch = Orchestrator(None, bot, pub)

    session_id = await orch.present_manual("Manual body", chat_id=7)
    await orch.handle_callback(session_id, "pub")

    assert pub.calls == ["Manual body"]
    bot.confirm.assert_awaited_once()
    _, _, chosen, url = bot.confirm.await_args.args
    assert chosen == "Manual body"
    assert url == "https://www.linkedin.com/feed/update/urn:li:share:7"
    assert orch._sessions[session_id].status == STATUS_PUBLISHED


@pytest.mark.asyncio
async def test_manual_publish_failure_stays_open_no_false_success():
    bot = _fake_bot()
    pub = FakePublisher(error=RuntimeError("LinkedIn 500"))
    orch = Orchestrator(None, bot, pub)

    session_id = await orch.present_manual("Manual body", chat_id=7)
    await orch.handle_callback(session_id, "pub")

    bot.send_error.assert_awaited_once()
    bot.confirm.assert_not_called()
    assert orch._sessions[session_id].status == STATUS_OPEN


@pytest.mark.asyncio
async def test_dry_run_publish_makes_no_linkedin_call():
    bot = _fake_bot()
    pub = FakePublisher()
    orch = Orchestrator(None, bot, pub, dry_run=True)

    session_id = await orch.present_manual("Would-be post", chat_id=7)
    await orch.handle_callback(session_id, "pub")

    # No publish, dry-run confirmation shows the exact text.
    assert pub.calls == []
    bot.confirm.assert_not_called()
    bot.confirm_dry_run.assert_awaited_once()
    _, _, chosen = bot.confirm_dry_run.await_args.args
    assert chosen == "Would-be post"
    assert orch._sessions[session_id].status == STATUS_PUBLISHED


@pytest.mark.asyncio
async def test_dry_run_applies_to_generated_selection_too():
    gen = FakeGenerator()
    bot = _fake_bot()
    pub = FakePublisher()
    orch = Orchestrator(gen, bot, pub, dry_run=True)

    session_id = await orch.run("topic", chat_id=7)
    await orch.handle_callback(session_id, "sel1")
    await orch.handle_callback(session_id, "pub")

    assert pub.calls == []
    bot.confirm_dry_run.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_drops_session_publishes_nothing():
    bot = _fake_bot()
    pub = FakePublisher()
    orch = Orchestrator(None, bot, pub)

    session_id = await orch.present_manual("Manual body", chat_id=7)
    await orch.handle_callback(session_id, "cancel")

    assert pub.calls == []
    bot.confirm.assert_not_called()
    bot.cancel.assert_awaited_once_with(7, session_id)
    assert orch._sessions[session_id].status == STATUS_CANCELLED


@pytest.mark.asyncio
async def test_retap_after_cancel_does_nothing():
    bot = _fake_bot()
    pub = FakePublisher()
    orch = Orchestrator(None, bot, pub)

    session_id = await orch.present_manual("Manual body", chat_id=7)
    await orch.handle_callback(session_id, "cancel")
    # Re-tap Publish on the resolved (cancelled) session.
    await orch.handle_callback(session_id, "pub")

    assert pub.calls == []
    bot.confirm.assert_not_called()


@pytest.mark.asyncio
async def test_manual_double_publish_publishes_once():
    bot = _fake_bot()
    pub = FakePublisher()
    orch = Orchestrator(None, bot, pub)

    session_id = await orch.present_manual("Manual body", chat_id=7)
    await orch.handle_callback(session_id, "pub")
    await orch.handle_callback(session_id, "pub")

    assert len(pub.calls) == 1
    assert bot.confirm.await_count == 1


# --- post-image feature (Task 01) -----------------------------------------


def _image_orch(bot, *, crafter=None, image_gen=None, pub=None, dry_run=False):
    return Orchestrator(
        FakeGenerator(),
        bot,
        pub or FakePublisher(),
        dry_run=dry_run,
        prompt_crafter=crafter,
        image_generator=image_gen,
    )


@pytest.mark.asyncio
async def test_select_offers_add_image_when_feature_enabled():
    bot = _fake_bot()
    orch = _image_orch(bot, crafter=FakePromptCrafter(), image_gen=FakeImageGenerator())

    session_id = await orch.run("topic", chat_id=7)
    await orch.handle_callback(session_id, "sel1")

    bot.present_decision.assert_awaited_once()
    assert bot.present_decision.await_args.kwargs["image_enabled"] is True
    assert orch.image_enabled is True


@pytest.mark.asyncio
async def test_addimg_crafts_generates_and_previews():
    bot = _fake_bot()
    crafter = FakePromptCrafter(prompt="a crisp boardroom photo")
    image_gen = FakeImageGenerator(image=b"IMG-BYTES")
    orch = _image_orch(bot, crafter=crafter, image_gen=image_gen)

    session_id = await orch.run("AI in finance", chat_id=7)
    _, _, posts = bot.present.await_args.args
    await orch.handle_callback(session_id, "sel2")
    await orch.handle_callback(session_id, "addimg")

    # Crafted from the selected candidate's text, then generated from that prompt.
    assert crafter.calls == [posts[1]]
    assert image_gen.calls == ["a crisp boardroom photo"]

    # Stored on the session and previewed as a photo.
    session = orch._sessions[session_id]
    assert session.image_prompt == "a crisp boardroom photo"
    assert session.image_bytes == b"IMG-BYTES"
    bot.present_image_preview.assert_awaited_once_with(7, b"IMG-BYTES", session_id)
    bot.send_error.assert_not_called()
    # Nothing published; session still open for the preview decision.
    assert session.status == STATUS_OPEN


@pytest.mark.asyncio
async def test_addimg_disabled_reports_unavailable_and_publishes_nothing():
    bot = _fake_bot()
    orch = _image_orch(bot)  # no crafter / image generator

    session_id = await orch.run("topic", chat_id=7)
    await orch.handle_callback(session_id, "sel1")
    await orch.handle_callback(session_id, "addimg")

    assert orch.image_enabled is False
    bot.present_image_preview.assert_not_called()
    bot.send_error.assert_awaited_once()
    assert orch._sessions[session_id].status == STATUS_OPEN


@pytest.mark.asyncio
async def test_addimg_generation_failure_reports_error_and_stays_open():
    bot = _fake_bot()
    crafter = FakePromptCrafter()
    image_gen = FakeImageGenerator(error=RuntimeError("Together 500"))
    orch = _image_orch(bot, crafter=crafter, image_gen=image_gen)

    session_id = await orch.run("topic", chat_id=7)
    await orch.handle_callback(session_id, "sel1")
    await orch.handle_callback(session_id, "addimg")

    bot.send_error.assert_awaited_once()
    bot.present_image_preview.assert_not_called()
    session = orch._sessions[session_id]
    assert session.status == STATUS_OPEN
    assert session.image_bytes is None


# --- publish with image (Task 02) -----------------------------------------


async def _previewed_image_session(bot, *, pub, dry_run=False):
    """Run a session up to a previewed image; return (orch, session_id, posts)."""
    crafter = FakePromptCrafter(prompt="a crisp boardroom photo")
    image_gen = FakeImageGenerator(image=b"IMG-BYTES")
    orch = _image_orch(
        bot, crafter=crafter, image_gen=image_gen, pub=pub, dry_run=dry_run
    )
    session_id = await orch.run("AI in finance", chat_id=7)
    posts = bot.present.await_args.args[2]
    await orch.handle_callback(session_id, "sel2")
    await orch.handle_callback(session_id, "addimg")
    return orch, session_id, posts


@pytest.mark.asyncio
async def test_pubimg_publishes_with_image_and_selected_text():
    bot = _fake_bot()
    pub = FakePublisher(url="https://www.linkedin.com/feed/update/urn:li:share:99")
    orch, session_id, posts = await _previewed_image_session(bot, pub=pub)

    await orch.handle_callback(session_id, "pubimg")

    # Published once, with the selected candidate text and the previewed image.
    assert pub.calls == []  # not the text-only path
    assert pub.image_calls == [(posts[1], b"IMG-BYTES", None)]
    bot.confirm.assert_awaited_once()
    _, _, chosen, url = bot.confirm.await_args.args
    assert chosen == posts[1]
    assert url == "https://www.linkedin.com/feed/update/urn:li:share:99"
    bot.send_error.assert_not_called()
    assert orch._sessions[session_id].status == STATUS_PUBLISHED


@pytest.mark.asyncio
async def test_pubimg_dry_run_makes_no_linkedin_call():
    bot = _fake_bot()
    pub = FakePublisher()
    orch, session_id, posts = await _previewed_image_session(
        bot, pub=pub, dry_run=True
    )

    await orch.handle_callback(session_id, "pubimg")

    # No publisher call; dry-run confirmation shows the exact text.
    assert pub.image_calls == []
    assert pub.calls == []
    bot.confirm.assert_not_called()
    bot.confirm_dry_run.assert_awaited_once()
    _, _, chosen = bot.confirm_dry_run.await_args.args
    assert chosen == posts[1]
    assert orch._sessions[session_id].status == STATUS_PUBLISHED


@pytest.mark.asyncio
async def test_pubimg_failure_reports_error_and_stays_open():
    bot = _fake_bot()
    pub = FakePublisher(error=RuntimeError("LinkedIn 500"))
    orch, session_id, _ = await _previewed_image_session(bot, pub=pub)

    await orch.handle_callback(session_id, "pubimg")

    bot.send_error.assert_awaited_once()
    bot.confirm.assert_not_called()
    # No false success; session left open so the user can retry.
    assert orch._sessions[session_id].status == STATUS_OPEN

    # Retry is allowed and re-attempts the image publish.
    await orch.handle_callback(session_id, "pubimg")
    assert len(pub.image_calls) == 2


@pytest.mark.asyncio
async def test_pubimg_double_publishes_once():
    bot = _fake_bot()
    pub = FakePublisher()
    orch, session_id, _ = await _previewed_image_session(bot, pub=pub)

    await orch.handle_callback(session_id, "pubimg")
    await orch.handle_callback(session_id, "pubimg")

    # Idempotency guard: only the first publish posts and confirms.
    assert len(pub.image_calls) == 1
    assert bot.confirm.await_count == 1


# --- preview controls (Task 03) ------------------------------------------


@pytest.mark.asyncio
async def test_regimg_reuses_stored_prompt_new_image_and_repreviews():
    bot = _fake_bot()
    crafter = FakePromptCrafter(prompt="a crisp boardroom photo")
    image_gen = FakeImageGenerator(image=b"IMG-1")
    orch = _image_orch(bot, crafter=crafter, image_gen=image_gen)

    session_id = await orch.run("AI in finance", chat_id=7)
    await orch.handle_callback(session_id, "sel2")
    await orch.handle_callback(session_id, "addimg")

    # Different bytes on regeneration so we can tell the new image apart.
    image_gen._image = b"IMG-2"
    await orch.handle_callback(session_id, "regimg")

    # Prompt crafted only once (regimg reuses the stored prompt).
    assert crafter.calls == [bot.present.await_args.args[2][1]]
    # Image generated twice, both from the same stored prompt.
    assert image_gen.calls == ["a crisp boardroom photo", "a crisp boardroom photo"]

    session = orch._sessions[session_id]
    assert session.image_prompt == "a crisp boardroom photo"
    assert session.image_bytes == b"IMG-2"
    assert bot.present_image_preview.await_count == 2
    bot.present_image_preview.assert_awaited_with(7, b"IMG-2", session_id)
    assert session.status == STATUS_OPEN


@pytest.mark.asyncio
async def test_regprompt_crafts_new_prompt_new_image_and_repreviews():
    bot = _fake_bot()
    crafter = FakePromptCrafter(prompt="prompt-A")
    image_gen = FakeImageGenerator(image=b"IMG-A")
    orch = _image_orch(bot, crafter=crafter, image_gen=image_gen)

    session_id = await orch.run("AI in finance", chat_id=7)
    posts = bot.present.await_args.args[2]
    await orch.handle_callback(session_id, "sel2")
    await orch.handle_callback(session_id, "addimg")

    crafter._prompt = "prompt-B"
    image_gen._image = b"IMG-B"
    await orch.handle_callback(session_id, "regprompt")

    # Crafted twice from the selected text; generated twice (one per prompt).
    assert crafter.calls == [posts[1], posts[1]]
    assert image_gen.calls == ["prompt-A", "prompt-B"]

    session = orch._sessions[session_id]
    assert session.image_prompt == "prompt-B"
    assert session.image_bytes == b"IMG-B"
    assert bot.present_image_preview.await_count == 2
    bot.present_image_preview.assert_awaited_with(7, b"IMG-B", session_id)


@pytest.mark.asyncio
async def test_ownprompt_arms_capture_then_submit_generates_and_previews():
    bot = _fake_bot()
    bot.request_own_prompt = AsyncMock()
    crafter = FakePromptCrafter(prompt="prompt-A")
    image_gen = FakeImageGenerator(image=b"IMG-A")
    orch = _image_orch(bot, crafter=crafter, image_gen=image_gen)

    session_id = await orch.run("AI in finance", chat_id=7)
    await orch.handle_callback(session_id, "sel2")
    await orch.handle_callback(session_id, "addimg")

    await orch.handle_callback(session_id, "ownprompt")
    # The bot is asked to arm the capture flag; no extra generation yet.
    bot.request_own_prompt.assert_awaited_once_with(7, session_id)
    assert image_gen.calls == ["prompt-A"]

    image_gen._image = b"IMG-OWN"
    await orch.submit_own_prompt(session_id, "my own qwen prompt")

    # Generated from the user's exact prompt; crafter not consulted again.
    assert image_gen.calls == ["prompt-A", "my own qwen prompt"]
    assert crafter.calls == [bot.present.await_args.args[2][1]]
    session = orch._sessions[session_id]
    assert session.image_prompt == "my own qwen prompt"
    assert session.image_bytes == b"IMG-OWN"
    bot.present_image_preview.assert_awaited_with(7, b"IMG-OWN", session_id)


@pytest.mark.asyncio
async def test_submit_own_prompt_ignored_on_resolved_session():
    bot = _fake_bot()
    crafter = FakePromptCrafter()
    image_gen = FakeImageGenerator()
    pub = FakePublisher()
    orch = _image_orch(bot, crafter=crafter, image_gen=image_gen, pub=pub)

    session_id = await orch.run("topic", chat_id=7)
    await orch.handle_callback(session_id, "sel1")
    await orch.handle_callback(session_id, "addimg")
    await orch.handle_callback(session_id, "pubimg")  # session now published
    generated_before = len(image_gen.calls)

    await orch.submit_own_prompt(session_id, "late prompt")

    # No image generated for a resolved session.
    assert len(image_gen.calls) == generated_before


@pytest.mark.asyncio
async def test_cancel_on_image_preview_publishes_nothing_and_cancels():
    bot = _fake_bot()
    pub = FakePublisher()
    orch, session_id, _ = await _previewed_image_session(bot, pub=pub)

    await orch.handle_callback(session_id, "cancel")

    assert pub.calls == []
    assert pub.image_calls == []
    bot.confirm.assert_not_called()
    bot.cancel.assert_awaited_once_with(7, session_id)
    assert orch._sessions[session_id].status == STATUS_CANCELLED


@pytest.mark.asyncio
async def test_preview_controls_ignored_on_resolved_session():
    bot = _fake_bot()
    pub = FakePublisher()
    orch, session_id, _ = await _previewed_image_session(bot, pub=pub)

    await orch.handle_callback(session_id, "cancel")  # resolve it
    bot.present_image_preview.reset_mock()

    for action in ("regimg", "regprompt", "ownprompt", "pubimg"):
        await orch.handle_callback(session_id, action)

    assert pub.calls == []
    assert pub.image_calls == []
    bot.present_image_preview.assert_not_called()
    bot.confirm.assert_not_called()


@pytest.mark.asyncio
async def test_regimg_disabled_reports_unavailable():
    bot = _fake_bot()
    orch = _image_orch(bot)  # no crafter / image generator

    session_id = await orch.run("topic", chat_id=7)
    await orch.handle_callback(session_id, "sel1")
    await orch.handle_callback(session_id, "regimg")

    bot.present_image_preview.assert_not_called()
    bot.send_error.assert_awaited_once()
