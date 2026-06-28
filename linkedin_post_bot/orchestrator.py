"""Orchestration: generate -> present -> handle callback (select / regenerate).

The ``Orchestrator`` owns per-session state and wires the pure ``PostTextGenerator``
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

ACTIONS = frozenset(
    {
        "sel1",
        "sel2",
        "sel3",
        "regen",
        "pub",
        "cancel",
        "addimg",
        "pubimg",
        "regimg",
        "regprompt",
        "ownprompt",
    }
)

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
    # Text the user committed to (chosen candidate or manual body). Set when a
    # candidate is selected or a manual post is presented; this is what gets
    # published, with or without an image.
    selected_text: str | None = None
    # Image state for the post-image feature (None until "Add image" is used).
    image_prompt: str | None = None
    image_bytes: bytes | None = None


class Orchestrator:
    """Drive generation, presentation, and callback handling for one user."""

    def __init__(
        self,
        generator,
        bot,
        publisher=None,
        *,
        n: int = 3,
        dry_run: bool = False,
        prompt_crafter=None,
        image_generator=None,
    ) -> None:
        self._generator = generator
        self._bot = bot
        self._publisher = publisher
        self._n = n
        self._dry_run = dry_run
        # The image feature is enabled iff both collaborators are wired (which
        # the caller only does when TOGETHER_API_KEY is present).
        self._prompt_crafter = prompt_crafter
        self._image_generator = image_generator
        self._sessions: dict[str, Session] = {}

    @property
    def image_enabled(self) -> bool:
        """True when the optional AI image feature is wired up."""
        return self._prompt_crafter is not None and self._image_generator is not None

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
            selected_text=text,
        )
        self._sessions[session_id] = session
        await self._bot.present_manual(chat_id, session_id, text)
        return session_id

    async def handle_callback(self, session_id: str, action: str) -> None:
        """Handle a button tap.

        ``selN`` records the chosen candidate and opens a decision step
        (Publish / Add image / Cancel) rather than publishing immediately;
        ``addimg`` crafts an image prompt, generates an image, and previews it;
        ``pub`` publishes the selected text and confirms with a live link;
        ``regen`` produces fresh candidates avoiding everything already shown.
        Callbacks on a non-open session are ignored (idempotency guard) so a
        re-tap never double-posts.
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

        if action == "addimg":
            await self._add_image(session_id, session)
            return

        if action == "regimg":
            await self._regenerate_image(session_id, session)
            return

        if action == "regprompt":
            await self._regenerate_prompt(session_id, session)
            return

        if action == "ownprompt":
            await self._request_own_prompt(session_id, session)
            return

        if action == "pub":
            await self._publish(session_id, session, self._publish_text(session))
            return

        if action == "pubimg":
            await self._publish_with_image(session_id, session)
            return

        # ``selN`` opens the decision step: record the chosen candidate and
        # offer Publish / Add image / Cancel (Add image only when enabled).
        index = int(action[-1]) - 1
        if not 0 <= index < len(session.candidates):
            logger.warning("Callback index %d out of range", index)
            return

        session.selected_text = session.candidates[index]
        await self._bot.present_decision(
            session.chat_id,
            session_id,
            session.selected_text,
            image_enabled=self.image_enabled,
        )

    @staticmethod
    def _publish_text(session: Session) -> str:
        """The text to publish: the explicitly selected text, else first candidate."""
        if session.selected_text is not None:
            return session.selected_text
        return session.candidates[0]

    async def _add_image(self, session_id: str, session: Session) -> None:
        """Craft an image prompt, generate an image, and preview it.

        On any craft/generation failure the user gets a clear error and nothing
        is published; the session stays open so they can retry or publish text.
        """
        if not self.image_enabled:
            logger.info("Ignoring addimg: image feature disabled (session %s)", session_id)
            await self._report_image_disabled(session)
            return

        text = self._publish_text(session)
        try:
            prompt = await asyncio.to_thread(self._prompt_crafter.craft, text)
        except Exception as exc:  # noqa: BLE001 - surface a clear, retryable error
            logger.exception("Prompt crafting failed for session %s", session_id)
            await self._bot.send_error(
                session.chat_id,
                f"Sorry, I couldn't create an image: {exc}. Nothing was posted "
                "- you can try again or publish the text as-is.",
            )
            return

        await self._generate_and_preview(session_id, session, prompt)

    async def _regenerate_image(self, session_id: str, session: Session) -> None:
        """Regenerate the image from the *stored* prompt and re-preview (UAT 3).

        Reuses ``session.image_prompt`` so the user gets a different image from
        the same direction. On failure the session stays open and unchanged.
        """
        if not self.image_enabled:
            await self._report_image_disabled(session)
            return
        prompt = session.image_prompt
        if prompt is None:
            # No prior prompt (shouldn't happen via the normal flow): fall back
            # to crafting one from the selected text.
            await self._add_image(session_id, session)
            return
        await self._generate_and_preview(session_id, session, prompt)

    async def _regenerate_prompt(self, session_id: str, session: Session) -> None:
        """Craft a *new* super-prompt, generate a new image, and re-preview (UAT 4)."""
        await self._add_image(session_id, session)

    async def _request_own_prompt(self, session_id: str, session: Session) -> None:
        """Ask the user to type their own Qwen-Image prompt (UAT 5).

        The bot sets a per-chat awaiting flag (distinct from the ``/posta``
        flag) keyed to this session; the next plain message is routed back here
        via :meth:`submit_own_prompt`.
        """
        if not self.image_enabled:
            await self._report_image_disabled(session)
            return
        await self._bot.request_own_prompt(session.chat_id, session_id)

    async def submit_own_prompt(self, session_id: str, prompt: str) -> None:
        """Generate from a user-supplied prompt and re-preview (UAT 5, 8).

        Called by the bot's text handler when an own-prompt capture is pending.
        A no-op on a resolved session (idempotency parity with callbacks).
        """
        session = self._sessions.get(session_id)
        if session is None:
            logger.warning("own-prompt for unknown session %r", session_id)
            return
        if session.status != STATUS_OPEN:
            logger.info("Ignoring own-prompt on %s session %s", session.status, session_id)
            return
        if not self.image_enabled:
            await self._report_image_disabled(session)
            return
        await self._generate_and_preview(session_id, session, prompt)

    async def _report_image_disabled(self, session: Session) -> None:
        await self._bot.send_error(
            session.chat_id,
            "The image feature is not configured. You can still publish "
            "the text as-is.",
        )

    async def _generate_and_preview(
        self, session_id: str, session: Session, prompt: str
    ) -> None:
        """Generate an image from ``prompt``, store it on the session, and preview.

        On any generation failure the user gets a clear error and nothing is
        published; the session stays open so they can retry or publish text.
        """
        try:
            image_bytes = await asyncio.to_thread(self._image_generator.generate, prompt)
        except Exception as exc:  # noqa: BLE001 - surface a clear, retryable error
            logger.exception("Image creation failed for session %s", session_id)
            await self._bot.send_error(
                session.chat_id,
                f"Sorry, I couldn't create an image: {exc}. Nothing was posted "
                "- you can try again or publish the text as-is.",
            )
            return

        session.image_prompt = prompt
        session.image_bytes = image_bytes
        await self._bot.present_image_preview(session.chat_id, image_bytes, session_id)

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

    async def _publish_with_image(self, session_id: str, session: Session) -> None:
        """Publish the selected text with the previewed image attached.

        Mirrors :meth:`_publish`: the status flips to ``published`` *before* the
        network call so a concurrent re-tap is short-circuited by the
        idempotency guard, and a failure reverts to ``open`` so the user can
        retry (no false success). Dry-run makes no LinkedIn call.
        """
        chosen = self._publish_text(session)
        image_bytes = session.image_bytes
        if image_bytes is None:
            # No image previewed (shouldn't happen via the normal flow); fall
            # back to a text publish rather than crashing.
            logger.warning(
                "pubimg with no image bytes on session %s; publishing text", session_id
            )
            await self._publish(session_id, session, chosen)
            return

        if self._dry_run:
            # Dry run: confirm what would be published (text + image), no call.
            session.status = STATUS_PUBLISHED
            await self._bot.confirm_dry_run(session.chat_id, session_id, chosen)
            return

        if self._publisher is None:
            session.status = STATUS_PUBLISHED
            await self._bot.acknowledge(session.chat_id, session_id, chosen)
            return

        session.status = STATUS_PUBLISHED
        try:
            url = await asyncio.to_thread(
                self._publisher.publish_with_image, chosen, image_bytes
            )
        except Exception as exc:  # noqa: BLE001 - surface a clear, retryable error
            session.status = STATUS_OPEN
            logger.exception("Image publishing failed for session %s", session_id)
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
