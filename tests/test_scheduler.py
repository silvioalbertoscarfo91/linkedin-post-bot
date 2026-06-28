from unittest.mock import AsyncMock, MagicMock

import pytest

from linkedin_post_bot.rotation import TopicRotation
from linkedin_post_bot.scheduler import Scheduler


def _rotation(tmp_path, topics):
    path = tmp_path / "topics.txt"
    path.write_text("\n".join(topics) + "\n", encoding="utf-8")
    return TopicRotation(path)


@pytest.mark.asyncio
async def test_fire_runs_orchestrator_with_next_rotation_topic(tmp_path):
    rotation = _rotation(tmp_path, ["alpha", "beta"])
    orchestrator = MagicMock(run=AsyncMock())
    sched = Scheduler(orchestrator, rotation, chat_id=99)

    await sched._fire()
    await sched._fire()
    await sched._fire()

    # Advances round-robin, no manual action, always the shared orchestrator.
    assert [c.args for c in orchestrator.run.await_args_list] == [
        ("alpha", 99),
        ("beta", 99),
        ("alpha", 99),
    ]


@pytest.mark.asyncio
async def test_fire_swallows_orchestrator_error(tmp_path):
    rotation = _rotation(tmp_path, ["alpha"])
    orchestrator = MagicMock(run=AsyncMock(side_effect=RuntimeError("boom")))
    sched = Scheduler(orchestrator, rotation, chat_id=1)

    # The orchestrator reports errors to the user itself; the scheduler must not
    # crash the process when a single run fails.
    await sched._fire()
    orchestrator.run.assert_awaited_once()


def test_start_registers_daily_cron_job(tmp_path):
    rotation = _rotation(tmp_path, ["alpha"])
    orchestrator = MagicMock(run=AsyncMock())
    fake_apscheduler = MagicMock()
    sched = Scheduler(
        orchestrator, rotation, chat_id=1, hour=7, minute=30,
        scheduler=fake_apscheduler,
    )

    sched.start()

    fake_apscheduler.add_job.assert_called_once()
    trigger = fake_apscheduler.add_job.call_args.args[1]
    # CronTrigger carries the configured time fields.
    fields = {f.name: str(f) for f in trigger.fields}
    assert fields["hour"] == "7"
    assert fields["minute"] == "30"
    fake_apscheduler.start.assert_called_once()
