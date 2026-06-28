from unittest.mock import AsyncMock, MagicMock

import pytest

from linkedin_post_bot.orchestrator import (
    STATUS_CANCELLED,
    STATUS_OPEN,
    STATUS_PUBLISHED,
    Orchestrator,
)


class FakeGenerator:
    """Deterministic stand-in for PostGenerator that records `avoid`."""

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
        self._url = url
        self._error = error

    def publish(self, text):
        self.calls.append(text)
        if self._error is not None:
            raise self._error
        return self._url


def _fake_bot():
    return MagicMock(
        present=AsyncMock(),
        present_manual=AsyncMock(),
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
async def test_select_publishes_chosen_candidate_and_confirms_with_link():
    gen = FakeGenerator()
    bot = _fake_bot()
    pub = FakePublisher(url="https://www.linkedin.com/feed/update/urn:li:share:42")
    orch = Orchestrator(gen, bot, pub)

    session_id = await orch.run("topic", chat_id=7)
    _, _, posts = bot.present.await_args.args

    await orch.handle_callback(session_id, "sel2")

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

    # Clear error, no false success.
    bot.send_error.assert_awaited_once()
    bot.confirm.assert_not_called()

    # Session left open so the user can retry; not marked published.
    assert orch._sessions[session_id].status == STATUS_OPEN

    # A retry is allowed and publishes.
    await orch.handle_callback(session_id, "sel1")
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
    await orch.handle_callback(session_id, "sel1")

    # Idempotency guard: only the first selection publishes and confirms.
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
