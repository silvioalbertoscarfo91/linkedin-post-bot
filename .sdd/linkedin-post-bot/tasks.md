# Tasks: LinkedIn Post Automation Bot

Vertical tracer-bullet slices. Numeric order = implementation order (blockers first).
Project root: `~/Projects/linkedin-post-bot/`.

---

## Task 01-telegram-roundtrip

Stand up the Python project skeleton and a running Telegram bot that responds only to
the authorized user. Sending `/genera <topic>` from the allowed account echoes the topic
back; any other account is ignored. This is the thinnest end-to-end slice (Telegram in →
config/auth → Telegram out) and establishes the scaffold every later slice builds on.

### Implementation steps

- [x] Init Python 3.11+ project (venv, `pyproject.toml`/`requirements.txt`, `pytest`), `.gitignore` excluding `.env`, `*token*.json`, venv.
- [x] Add config loader reading `.env` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_ID`, and placeholders for Claude/LinkedIn) via `python-dotenv`.
- [x] Wire `python-telegram-bot` (async) with a `/genera` command handler that echoes the topic.
- [x] Add an authorization filter: only `TELEGRAM_ALLOWED_USER_ID` is served; others ignored.
- [x] Add `/genera` with no topic → reply asking for a topic.
- [x] README: setup steps for Telegram bot token and `.env`.

### Acceptance criteria

- [x] Authorized user sends `/genera AI in finance` → bot replies echoing `AI in finance`.
- [x] A non-authorized Telegram user id sending `/genera` receives no action/refusal (UAT 8).
- [x] `/genera` with no topic → bot replies asking for a topic (UAT 10).
- [x] Missing required env var → bot fails fast at startup with a clear message.
- [x] Bot starts from a documented command on macOS.

### Quality gates

- [x] `pip install -r requirements.txt` (or `pip install -e .`) succeeds in a clean venv.
- [x] `python -m compileall .` reports no syntax errors.
- [x] `pytest` green (config-loader + auth-filter unit tests).
- [x] `ruff check .` (lint) reports no violations.

---

## Task 02-generate-and-present

`/genera <topic>` triggers Claude to produce three distinct candidate posts, presented in
one Telegram message with four inline buttons (Post 1/2/3 + "Give me 3 more"). Tapping a
post button acknowledges the selection (no publish yet); "Give me 3 more" regenerates three
fresh candidates on the same topic, excluding the ones already shown. Cuts through
generation → presentation → callback → regenerate loop.

### Implementation steps

- [x] Implement `PostGenerator.generate(topic, n=3, avoid=None)` using the `openai` SDK against NVIDIA's OpenAI-compatible API + system prompt for LinkedIn tone/length.
- [x] Validate model output yields exactly `n` candidates; retry/raise on malformed output.
- [x] Implement `TelegramBot.present(chat_id, posts)` rendering 3 numbered candidates + 4 inline buttons; callback data = `<session_id>:<action>` (`sel1|sel2|sel3|regen`).
- [x] Session state map `{session_id: {topic, candidates, status}}`; status `open`.
- [x] Callback handler: `selN` → ack chosen text + strip keyboard; `regen` → regenerate with `avoid=previous`, re-present.
- [x] `Orchestrator.run(topic, chat_id)` wiring generate → present.

### Acceptance criteria

- [x] `/genera <topic>` returns one message with three numbered candidates + four buttons (UAT 1). *(verified: `_render_candidates`/`_keyboard` produce 3 numbered candidates + 4 buttons `sel1|sel2|sel3|regen`; `test_run_generates_and_presents` confirms one `present` call with 3 posts.)*
- [x] "Give me 3 more" returns three new candidates on the same topic, differing from prior set (UAT 3). *(verified: `test_regenerate_uses_avoid_with_previous_candidates` — second `present` set is disjoint from the first, same topic.)*
- [x] Tapping Post N acknowledges the Nth candidate's text and removes the keyboard. *(ack verified by `test_select_acknowledges_chosen_candidate`; keyboard strip done in `callback_handler` via `edit_message_reply_markup(None)` — code-verified, live Telegram strip not exercised offline.)*
- [x] `avoid` candidates are absent from the regenerated set. *(verified: `FakeGenerator` records `avoid==first batch`; new candidates disjoint from prior set in `test_regenerate_uses_avoid_with_previous_candidates`.)*
- [x] Malformed/short model output does not crash the bot (handled error to user). *(verified: `test_short_output_retries_then_raises`, `test_non_json_output_raises`, `test_empty_response_raises` raise `GenerationError`; `Orchestrator.run` catches it and calls `bot.send_error` — `test_generation_failure_reports_error_and_no_session`.)*

### Quality gates

- [x] `pytest` green: `PostGenerator` unit (mock openai — returns n, honors `avoid`, handles bad output) + `Orchestrator` integration (generate→present, regen uses `avoid`). *(27 passed.)*
- [x] `python -m compileall .` no errors. *(exit 0.)*
- [x] `ruff check .` no violations. *(All checks passed!)*

---

## Task 03-publish-to-linkedin

A selected candidate is published to the user's real LinkedIn profile via the official API,
with a one-time OAuth bootstrap and persisted, auto-refreshed token. After publishing, the
bot replies with a confirmation + live link. Publishing failures surface clearly and never
double-post. End-to-end: selection → token mgmt → LinkedIn POST → confirmation.

### Implementation steps

- [x] `auth_linkedin.py` one-time script: authorization-code OAuth flow → store access/refresh token + author URN in gitignored `linkedin_token.json`.
- [x] `LinkedInPublisher.ensure_token()` loads token, refreshes when expired; `publish(text) -> url` POSTs the UGC/post and returns the live URL.
- [x] Resolve + cache author URN (identity endpoint).
- [x] Wire `Orchestrator` select path: `status open` → publish → set `published` → confirmation+link; on error → error message, status stays `open`.
- [x] Idempotency guard: callbacks on a `published` session are ignored.
- [x] README: LinkedIn Developer app, scopes (`w_member_social` + identity), redirect URI, running `auth_linkedin.py`.

### Acceptance criteria

- [x] Tapping Post N publishes that text to LinkedIn; bot replies with confirmation + link (UAT 2). *(verified offline: `test_select_publishes_chosen_candidate_and_confirms_with_link` — `publisher.publish` called once with the Nth candidate text, `bot.confirm` awaited with the live URL `…/feed/update/urn:li:share:42`. Live publish/round-trip to real LinkedIn is manual.)*
- [ ] ~~Opening the link shows the exact chosen text live on the profile (UAT 4).~~ *(deferred: manual, needs live LinkedIn credentials — cannot open a real post in this environment.)*
- [x] Publish failure → clear error message, no false success (UAT 5). *(verified: `test_publish_failure_reports_error_and_session_stays_open` — publisher raises, `bot.send_error` awaited, `bot.confirm` not called, session status stays `open` and a retry re-publishes.)*
- [x] Re-tapping a button on an already-published message publishes nothing again (UAT 7). *(verified: `test_double_callback_publishes_once` — two `sel1` callbacks → `publisher.publish` called exactly once, `bot.confirm` awaited once; idempotency guard short-circuits the non-open session.)*
- [x] Expired token → refreshed (or re-auth prompt) and flow continues (UAT 9). *(verified offline: `test_ensure_token_refreshes_when_expired` — expired token triggers the refresh-token grant, new token persisted; `test_ensure_token_no_refresh_token_raises` / `test_refresh_http_error_raises_token_error` cover the re-auth-prompt fallback. Live refresh against real LinkedIn is manual.)*

### Quality gates

- [x] `pytest` green: `LinkedInPublisher` unit (mock HTTP — payload shape, refresh path, error→typed exception, token persistence round-trip) + `Orchestrator` integration (select→publish once; failure→not published; double-callback→single publish). *(38 passed — 11 new: `tests/test_publisher.py` covers payload shape, refresh, typed errors, persistence round-trip; `tests/test_orchestrator.py` covers select→publish once, failure→stays open, double-callback→single publish.)*
- [x] `python -m compileall .` no errors. *(exit 0 over `linkedin_post_bot`, `auth_linkedin.py`, `tests`.)*
- [x] `ruff check .` no violations. *(All checks passed!)*

---

## Task 04-scheduled-rotation

A daily scheduled run fires the full generate→present flow automatically, choosing the next
topic from a persisted rotation list, requiring no manual action. The manual `/genera` path
continues to work. End-to-end: cron → topic rotation → Orchestrator → Telegram.

### Implementation steps

- [x] `topics` rotation file + round-robin index persisted across runs. *(`linkedin_post_bot/rotation.py`: `load_topics` + `TopicRotation.next_topic()`; index persisted to `topics.txt.index.json` sidecar; `topics.txt.example` provided, `topics.txt`/`*.index.json` gitignored.)*
- [x] `Scheduler` using `APScheduler`: daily cron job → next topic → `Orchestrator.run(topic, chat_id)`. *(`linkedin_post_bot/scheduler.py`: `AsyncIOScheduler` + `CronTrigger(hour, minute)`, job id `daily-rotation`; `APScheduler>=3.10` added to `requirements.txt`/`pyproject.toml`.)*
- [x] Ensure scheduler and manual command share one Orchestrator/bot instance. *(`bot.run` builds one `Orchestrator` and passes it to both the Telegram handlers and `Scheduler`; scheduler started via `application.post_init`, stopped via `post_shutdown`.)*
- [x] README: configure schedule time, topics file, and keeping the process running on macOS. *(README "Configure the daily topic rotation" + "Keeping the process running on macOS" (launchd) sections; `.env.example` documents `TOPICS_PATH`/`SCHEDULE_HOUR`/`SCHEDULE_MINUTE`.)*

### Acceptance criteria

- [x] At the scheduled time the bot sends three candidates for the next rotation topic with no manual action (UAT 6). *(verified: `test_fire_runs_orchestrator_with_next_rotation_topic` + live demo — `Scheduler._fire()` calls the shared `Orchestrator.run("alpha", 42)` which presents 3 candidates with no manual input; real `AsyncIOScheduler` registers job `daily-rotation` with trigger `cron[hour='9', minute='0']`. Live wall-clock fire is time-based and not exercised offline.)*
- [x] Rotation advances round-robin and the index survives a restart. *(verified live across two separate `python` processes on the same file: process 1 → `a b`, process 2 (fresh interpreter) → `c a`; `test_index_persists_across_instances`, `test_next_topic_round_robin_and_wraps`, `test_index_sidecar_written_next_to_topics`.)*
- [x] Manual `/genera <topic>` still works alongside the scheduler. *(verified: live demo runs `orchestrator.run("manual topic", 42)` on the same shared orchestrator after a scheduled fire and presents 3 candidates; the full slice-02/03 `tests/test_orchestrator.py` suite (8 tests) still passes unchanged.)*
- [x] Empty/missing topics file → clear startup error, scheduler does not crash silently. *(verified: `TopicRotation` constructor calls `load_topics`, raising `RotationError` with a clear message at build time — live: missing file and comments-only file both raise; `test_construction_fails_fast_on_missing_file`, `test_load_topics_missing_file_raises`, `test_load_topics_empty_file_raises`.)*

### Quality gates

- [x] `pytest` green: rotation selection unit (round-robin + index persistence). *(54 passed — 16 new across `tests/test_rotation.py` (9), `tests/test_scheduler.py` (3), `tests/test_config.py` schedule parsing (4).)*
- [x] `python -m compileall .` no errors. *(exit 0 over `linkedin_post_bot`, `auth_linkedin.py`, `tests`.)*
- [x] `ruff check .` no violations. *(All checks passed!)*

---

## Task 05-manual-post-and-dry-run

Add a manual posting path that bypasses Claude entirely, plus a dry-run mode, so the
publish flow can be trialed without an Anthropic API key (and optionally without
touching LinkedIn). `/posta` prompts the user to paste post text in the next message;
the pasted text is presented with Publish / Cancel buttons. With `DRY_RUN=true` the bot
shows exactly what it would publish and makes no LinkedIn call; otherwise it publishes
via the existing LinkedInPublisher. The Anthropic API key becomes optional — only
`/genera` requires it.

### Implementation steps

- [x] Make `NVIDIA_API_KEY` optional in config; `/genera` fails gracefully with a clear message if the key is missing, manual mode works without it. *(`nvidia_api_key` already `str | None`; `genera_command` now replies `NO_API_KEY_REPLY` when unset; `bot.run` builds the generator only when a key is present and disables the scheduler otherwise, so a manual-only setup starts cleanly.)*
- [x] Add `DRY_RUN` env (default false) to config. *(`Config.dry_run` + `opt_bool("DRY_RUN", False)` accepting true/false/1/0/yes/no/on/off; invalid value raises `ConfigError`.)*
- [x] `/posta` command (authorized user only): bot asks for text, captures the next plain message from that user as the post body (conversational state per chat/user). *(`posta_command` sets `chat_data[AWAITING_POST_KEY]` + replies `PASTE_PROMPT`; `manual_text_handler` (a `MessageHandler(filters.TEXT & ~filters.COMMAND)`) captures the next plain message only when awaiting and authorized, then clears the flag.)*
- [x] Present the pasted text as a single candidate with Publish / Cancel inline buttons (reuse Orchestrator session-state + idempotency guard). *(`Orchestrator.present_manual` creates an `open` single-candidate `Session`; `TelegramBot.present_manual` renders the preview with a `_manual_keyboard` (`pub`/`cancel`); the existing non-open-session idempotency guard applies unchanged.)*
- [x] On Publish: if `DRY_RUN` → reply "would publish:" + text, no LinkedIn call; else publish via `LinkedInPublisher` and confirm with link. On Cancel: drop session, strip keyboard. *(`_publish` short-circuits to `bot.confirm_dry_run` (no publisher call) when `dry_run`; `cancel` action sets status `cancelled` + `bot.cancel`; the callback handler strips the keyboard via `edit_message_reply_markup(None)` for all actions.)*
- [x] README + `.env.example`: document `/posta`, `DRY_RUN`, and that the NVIDIA key is optional for manual-only use. *(README "Manual posting with /posta" section + optional-env notes; `.env.example` marks `NVIDIA_API_KEY` optional and adds `DRY_RUN=false`.)*

### Acceptance criteria

- [x] With no `NVIDIA_API_KEY` set, the bot starts and `/posta` works end-to-end; `/genera` replies with a clear "key required" message rather than crashing. *(verified: `test_genera_without_api_key_replies_key_required` — `genera_command` replies `NO_API_KEY_REPLY`, `orchestrator.run` not called; `bot.run` guards generator/scheduler construction behind a present key. Live Telegram start not exercised offline.)*
- [x] `/posta` → bot asks for text → user's next message is captured and presented with Publish / Cancel buttons. *(verified: `test_posta_prompts_and_sets_awaiting_flag` + `test_manual_text_captured_and_presented` — flag set, next text routed to `orchestrator.present_manual(text, chat_id)`; `test_present_manual_shows_single_candidate_with_buttons` confirms a single-candidate open session + `present_manual` render.)*
- [x] With `DRY_RUN=true`, tapping Publish replies with the exact text and performs no LinkedIn call. *(verified: `test_dry_run_publish_makes_no_linkedin_call` + `test_dry_run_applies_to_generated_selection_too` — `publisher.publish` never called, `bot.confirm_dry_run` awaited with the exact text.)*
- [x] With `DRY_RUN` false, tapping Publish calls the publisher once and replies with a confirmation link; failure surfaces a clear error and does not mark published. *(verified: `test_manual_publish_calls_publisher_and_confirms` (publish once + link) and `test_manual_publish_failure_stays_open_no_false_success` (error surfaced, status stays `open`).)*
- [x] Tapping Cancel publishes nothing and removes the keyboard; re-tapping a resolved message does nothing (idempotency). *(verified: `test_cancel_drops_session_publishes_nothing` (status `cancelled`, no publish, `bot.cancel`), `test_retap_after_cancel_does_nothing`, `test_manual_double_publish_publishes_once`; keyboard strip done in `callback_handler` — code-verified.)*
- [x] An unauthorized user cannot use `/posta`. *(verified: `test_posta_unauthorized_user_ignored` (no prompt, no flag) and `test_manual_text_ignored_for_unauthorized_user` (no capture even while awaiting).)*

### Quality gates

- [x] `pytest` green: config (optional key, DRY_RUN parse) + Orchestrator manual-publish/dry-run/cancel + capture-next-message handler. *(68 passed — 14 new: 6 in `tests/test_bot.py` (key-required, /posta prompt+auth, capture handler ×3) + 8 in `tests/test_orchestrator.py` (present_manual, manual publish/failure, dry-run ×2, cancel, re-tap, double-publish).)*
- [x] `python -m compileall .` no errors. *(COMPILE_OK over `linkedin_post_bot`, `auth_linkedin.py`, `tests`.)*
- [x] `ruff check .` no violations. *(All checks passed!)*
