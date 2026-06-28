"""Orchestration: generate -> present -> handle callback (select / regenerate).

The ``Orchestrator`` owns per-session state and wires the pure ``PostGenerator``
to the IO-shell ``TelegramBot`` and ``LinkedInPublisher``. It has no direct
Telegram, generation-API, or LinkedIn HTTP knowledge beyond the small interfaces it
calls, so it can be integration-tested in-process with fakes.

Slice 03 scope: selecting a candidate publishes it to LinkedIn and confirms
with a live link. A publish failure surfaces a clear error and leaves the
session open (no false success, retryable). The published-session idempotency
guard prevents double-posting on a re-tap.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

ACTIONS = frozenset({"sel1", "sel2", "sel3", "regen", "pub", "cancel"})

STATUS_OPEN = "open"
STATUS_PUBLISHED = "published"
STATUS_CANCELLED = "cancelled"


@dataclass
class Session:
    """Per-presentation state, keyed by ``session_id``."""

    topic: str
    chat_id: int
    candidates: list[str]
    status: str = STATUS_OPEN
    # All candidates ever shown for this topic, so regeneration can avoid them.
    shown: list[str] = field(default_factory=list)


class Orchestrator:
    """Drive generation, presentation, and callback handling for one user."""

    def __init__(
        self, generator, bot, publisher=None, *, n: int = 3, dry_run: bool = False
    ) -> None:
        self._generator = generator
        self._bot = bot
        self._publisher = publisher
        self._n = n
        self._dry_run = dry_run
        self._sessions: dict[str, Session] = {}

    async def run(self, topic: str, chat_id: int) -> str:
        """Generate candidates for ``topic`` and present them. Returns the id.

        On generation failure the user is told and no session is created.
        """
        try:
            candidates = self._generator.generate(topic, n=self._n)
        except Exception as exc:  # noqa: BLE001 - surface any generation failure
            logger.exception("Generation failed for topic %r", topic)
            await self._bot.send_error(
                chat_id, f"Sorry, I couldn't generate posts right now: {exc}"
            )
            raise

        session_id = uuid.uuid4().hex
        session = Session(
            topic=topic,
            chat_id=chat_id,
            candidates=candidates,
            shown=list(candidates),
        )
        self._sessions[session_id] = session
        await self._bot.present(chat_id, session_id, candidates)
        return session_id

    async def present_manual(self, text: str, chat_id: int) -> str:
        """Present a user-supplied post body with Publish / Cancel buttons.

        This bypasses generation entirely: the text is stored as the single
        candidate of a new ``open`` session, reusing the same session-state and
        idempotency machinery as the generated path.
        """
        session_id = uuid.uuid4().hex
        session = Session(
            topic="",
            chat_id=chat_id,
            candidates=[text],
            shown=[text],
        )
        self._sessions[session_id] = session
        await self._bot.present_manual(chat_id, session_id, text)
        return session_id

    async def handle_callback(self, session_id: str, action: str) -> None:
        """Handle a button tap.

        ``selN`` publishes the chosen candidate to LinkedIn and confirms with a
        live link; ``regen`` produces fresh candidates avoiding everything
        already shown. Callbacks on a non-open session are ignored (idempotency
        guard) so a re-tap never double-posts.
        """
        if action not in ACTIONS:
            logger.warning("Ignoring unknown callback action %r", action)
            return

        session = self._sessions.get(session_id)
        if session is None:
            logger.warning("Ignoring callback for unknown session %r", session_id)
            return

        if session.status != STATUS_OPEN:
            logger.info("Ignoring callback on %s session %s", session.status, session_id)
            return

        if action == "regen":
            await self._regenerate(session_id, session)
            return

        if action == "cancel":
            session.status = STATUS_CANCELLED
            await self._bot.cancel(session.chat_id, session_id)
            return

        if action == "pub":
            await self._publish(session_id, session, session.candidates[0])
            return

        index = int(action[-1]) - 1
        if not 0 <= index < len(session.candidates):
            logger.warning("Callback index %d out of range", index)
            return

        chosen = session.candidates[index]
        await self._publish(session_id, session, chosen)

    async def _publish(self, session_id: str, session: Session, chosen: str) -> None:
        """Publish the chosen candidate, confirm on success, report on failure.

        The status is flipped to ``published`` *before* the network call so a
        concurrent re-tap is short-circuited by the idempotency guard and can
        never double-post. On failure the status is reverted to ``open`` so the
        user can retry (no false success).
        """
        if self._dry_run:
            # Dry run: show exactly what would be published, make no network
            # call. The session is still marked published so a re-tap is a
            # no-op (idempotency parity with the real path).
            session.status = STATUS_PUBLISHED
            await self._bot.confirm_dry_run(session.chat_id, session_id, chosen)
            return

        if self._publisher is None:
            # No publisher wired (should not happen in production); fall back to
            # acknowledging without publishing rather than crashing.
            session.status = STATUS_PUBLISHED
            await self._bot.acknowledge(session.chat_id, session_id, chosen)
            return

        session.status = STATUS_PUBLISHED
        try:
            url = await asyncio.to_thread(self._publisher.publish, chosen)
        except Exception as exc:  # noqa: BLE001 - surface a clear, retryable error
            session.status = STATUS_OPEN
            logger.exception("Publishing failed for session %s", session_id)
            await self._bot.send_error(
                session.chat_id,
                f"Sorry, publishing to LinkedIn failed: {exc}. Nothing was "
                "posted - please try again.",
            )
            return

        await self._bot.confirm(session.chat_id, session_id, chosen, url)

    async def _regenerate(self, session_id: str, session: Session) -> None:
        try:
            candidates = self._generator.generate(
                session.topic, n=self._n, avoid=list(session.shown)
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Regeneration failed for topic %r", session.topic)
            await self._bot.send_error(
                session.chat_id,
                f"Sorry, I couldn't generate more posts right now: {exc}",
            )
            return

        session.candidates = candidates
        session.shown.extend(candidates)
        await self._bot.present(session.chat_id, session_id, candidates)
