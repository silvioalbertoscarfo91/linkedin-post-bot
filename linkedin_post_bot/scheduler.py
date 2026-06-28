"""Daily scheduled run: cron -> topic rotation -> Orchestrator -> Telegram.

Thin IO shell around ``APScheduler``. At the configured time each day it picks
the next topic from the :class:`~linkedin_post_bot.rotation.TopicRotation` and
calls ``Orchestrator.run(topic, chat_id)`` — the exact same entry point the
manual ``/genera`` command uses, so the scheduler and the manual path share one
Orchestrator/bot instance.

The cron registration here is not unit-tested (side-effect shell, kept thin);
the rotation selection logic it depends on is covered in ``tests/test_rotation``.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .rotation import TopicRotation

logger = logging.getLogger(__name__)


class Scheduler:
    """Register a daily cron job that fires the full generate->present flow."""

    def __init__(
        self,
        orchestrator,
        rotation: TopicRotation,
        chat_id: int,
        *,
        hour: int = 9,
        minute: int = 0,
        scheduler: AsyncIOScheduler | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._rotation = rotation
        self._chat_id = chat_id
        self._hour = hour
        self._minute = minute
        self._scheduler = scheduler or AsyncIOScheduler()

    async def _fire(self) -> None:
        """Pick the next rotation topic and run the orchestrator for it."""
        topic = self._rotation.next_topic()
        logger.info("Scheduled run firing for topic %r", topic)
        try:
            await self._orchestrator.run(topic, self._chat_id)
        except Exception:  # noqa: BLE001 - already reported to the user by the orchestrator
            logger.exception("Scheduled run failed for topic %r", topic)

    def start(self) -> None:
        """Register the daily cron job and start the scheduler."""
        self._scheduler.add_job(
            self._fire,
            CronTrigger(hour=self._hour, minute=self._minute),
            id="daily-rotation",
            replace_existing=True,
        )
        self._scheduler.start()
        logger.info(
            "Scheduler started: daily rotation at %02d:%02d", self._hour, self._minute
        )

    def shutdown(self) -> None:
        """Stop the scheduler (used by tests/teardown)."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
