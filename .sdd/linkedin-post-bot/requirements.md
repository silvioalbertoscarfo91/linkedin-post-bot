# Requirements: LinkedIn Post Automation Bot

## Problem Statement

I want to publish on LinkedIn regularly, but writing good posts and going through
the LinkedIn UI every time is slow and friction-heavy. I want to be presented with
a few ready-to-publish options, pick the one I like best from my phone, and have it
posted for me — without ever opening the LinkedIn website.

## Solution

A bot that, on demand or on a schedule, uses Claude to generate three candidate
LinkedIn posts about a topic and sends them to me on Telegram. Each candidate has
a button; I tap the one I want and it is published to my LinkedIn profile
automatically. If I don't like any of the three, a fourth button gives me three new
candidates. After publishing, the bot replies with a confirmation and a link to the
live post.

## User Stories

1. As a user, I want to trigger post generation manually from Telegram by sending a command with a topic, so that I can post about whatever is on my mind right now.
2. As a user, I want the bot to generate posts automatically on a daily schedule, so that I keep a consistent posting cadence without thinking about it.
3. As a user, I want the scheduled run to pick a topic from a predefined rotation list, so that automated posts stay varied and on-brand.
4. As a user, I want to receive three distinct candidate posts in a single Telegram message, so that I can compare them at a glance.
5. As a user, I want each candidate to have its own button (Post 1 / Post 2 / Post 3), so that I can select one with a single tap.
6. As a user, I want a fourth button ("Give me 3 more"), so that I can regenerate when none of the three fit.
7. As a user, I want the regenerate button to produce three genuinely new candidates on the same topic, so that I'm not shown the same text again.
8. As a user, I want the selected post published to my own LinkedIn profile, so that it appears on my feed as if I posted it myself.
9. As a user, I want a confirmation message with a link to the published post, so that I can verify it went live.
10. As a user, I want to be told clearly if publishing failed, so that I know to retry or intervene.
11. As a user, I want the bot to authenticate to LinkedIn once and reuse/refresh the token, so that I don't re-login on every post.
12. As a user, I want only my own Telegram account to control the bot, so that strangers cannot post to my LinkedIn.
13. As a user, I want generated posts to respect a reasonable LinkedIn length and tone, so that they're publishable without editing.
14. As a user, I want the bot to run on my local Mac, so that I keep full control and no third-party hosting is involved.
15. As a user, I want the buttons to stop working after I make a choice, so that I can't accidentally double-post.
16. As a user, I want to optionally edit the chosen text before publishing, so that I can make a small tweak. *(stretch — see Out of Scope)*

## User Acceptance Tests

1. Given the bot is running, when I send `/genera AI agents in finance` on Telegram, then within a short time I receive one message containing three numbered candidate posts and four buttons.
2. Given three candidates are shown, when I tap "Post 2", then the second candidate is published to my LinkedIn profile and the bot replies with a confirmation containing a link to the live post.
3. Given three candidates are shown, when I tap "Give me 3 more", then I receive three new candidates about the same topic that differ from the previous three.
4. Given a post was published, when I open the link in the confirmation message, then I see that exact text live on my LinkedIn profile.
5. Given publishing to LinkedIn fails, when I tap a post button, then the bot replies with a clear error message and does not claim success.
6. Given the daily schedule fires, when the scheduled time arrives, then the bot picks the next topic from the rotation list and sends three candidates to my Telegram with no manual action.
7. Given a candidate has already been chosen and published, when I tap any button on that same message again, then nothing is published a second time.
8. Given a Telegram account that is not mine, when it sends `/genera` to the bot, then the bot ignores the request or refuses it.
9. Given the LinkedIn access token has expired, when generation/publish runs, then the token is refreshed (or I am prompted to re-authorize) and the flow continues.
10. Given I send `/genera` with no topic, when the bot receives it, then it asks me to provide a topic rather than generating empty posts.

## Definition of Done

- All user acceptance tests pass.
- A manual `/genera <topic>` command and a daily scheduled run both produce candidates on Telegram.
- A selected candidate reliably appears on the user's real LinkedIn profile.
- Only the authorized Telegram user can operate the bot.
- LinkedIn authentication persists across runs without manual re-login under normal token lifetime.
- Failures (Claude API, Telegram, LinkedIn) produce clear user-facing messages and never silently lose a post or double-post.
- Automated tests pass for all modules containing logic (PostGenerator, LinkedInPublisher, Orchestrator).
- README documents one-time setup: Claude API key, Telegram bot token, LinkedIn app credentials + OAuth authorization, topic rotation file, and how to start the bot and the scheduler on macOS.

## Out of Scope

- Editing a candidate's text inside Telegram before publishing (story 16) — first version publishes the chosen text as-is.
- Image/media attachments, polls, articles, or carousels — text posts only.
- Posting to LinkedIn Company Pages — personal profile only.
- Analytics on published-post performance (likes/impressions).
- Multi-user / multi-account support.
- Cloud/server deployment — local Mac only for now.
- Scheduling posts for a future publish time — selection publishes immediately.

## Further Notes

- LinkedIn API access requires a LinkedIn Developer app with the appropriate
  products/scopes enabled (`w_member_social` for posting, plus an identity scope to
  resolve the author URN). Approval and OAuth setup is a one-time manual prerequisite
  and is the main external dependency / risk.
- Browser automation was explicitly rejected in favor of the official API (ToS and
  reliability).
- Secrets (API keys, tokens) must live outside source control (`.env` / a local
  secrets file), never committed.

---

## Technical Annex
> Written against codebase as of: 2026-06-28 (greenfield — no existing code)

### Architectural Decisions

**Language / stack:** Python 3.11+.

**Key libraries:**
- `anthropic` — Claude API client for post generation. Use latest Claude model (e.g. `claude-opus-4-8` or `claude-sonnet-4-6`) per `claude-api` reference at build time.
- `python-telegram-bot` (v21+, async) — Telegram bot with inline keyboards and callback queries.
- `requests` (or `httpx`) — LinkedIn REST calls (OAuth token exchange + post creation).
- `APScheduler` — in-process cron-style scheduler for the daily run.
- `python-dotenv` — load secrets from `.env`.

**Modules (deep, isolated interfaces):**

1. `PostGenerator`
   - `generate(topic: str, n: int = 3, avoid: list[str] | None = None) -> list[str]`
   - Calls Claude with a system prompt defining LinkedIn tone/length constraints.
   - `avoid` carries previously-shown candidates so "give me 3 more" returns fresh text.
   - No Telegram/LinkedIn knowledge. Pure: topic in, list of strings out.

2. `LinkedInPublisher`
   - `publish(text: str) -> str` → returns public post URL/URN.
   - `ensure_token() -> str` → loads/refreshes OAuth access token from local token store.
   - Resolves author URN (`/v2/userinfo` or `me`) once and caches it.
   - POST to `/v2/ugcPosts` (or `/rest/posts`) with `w_member_social`.
   - Token + URN persisted in a local JSON file outside VCS.

3. `TelegramBot`
   - Owns command handlers (`/genera`), inline keyboard rendering, and callback handling.
   - `present(chat_id, posts: list[str]) -> message` — sends candidates + 4 buttons
     (`Post 1`, `Post 2`, `Post 3`, `Give me 3 more`). Callback data encodes a session id + action.
   - Enforces authorized-user allowlist (Telegram user id from config).
   - Edits/removes the inline keyboard after a terminal choice to prevent double-post.

4. `Orchestrator`
   - `run(topic: str, chat_id) -> None` — generate → present → await callback.
   - On regenerate: re-generate with `avoid=previous`, re-present (loop).
   - On select: call `LinkedInPublisher.publish`, then send confirmation/link or error.
   - Holds per-session state (topic, shown candidates, status) keyed by message/session id;
     idempotency guard so a published session ignores further callbacks.

5. `Scheduler`
   - APScheduler cron job → reads next topic from rotation file (round-robin, persists index) → `Orchestrator.run`.
   - Manual path: `/genera <topic>` handler → `Orchestrator.run`.

**Config / data flow:**
- `.env`: `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_ID`, `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET`, `LINKEDIN_REDIRECT_URI`.
- `topics.txt` (or `topics.yaml`): rotation list for scheduled runs + persisted rotation index.
- `linkedin_token.json`: access/refresh token + author URN (gitignored).
- One-time OAuth bootstrap script (`auth_linkedin.py`) to obtain the first token via the authorization-code flow.

**Callback-data shape (encodes the decision precisely):**
```
"<session_id>:<action>"   where action ∈ {"sel1","sel2","sel3","regen"}
```
Session state map: `{session_id: {topic, candidates: [..], status: "open"|"published"}}`.
Publish handler: if `status != "open"` → ignore (idempotency); else publish, set `"published"`, strip keyboard.

### Automated Testing Decisions

**What makes a good test here:** test external behavior through each module's public
interface, not internal calls. Mock the three external services (Claude, Telegram,
LinkedIn HTTP) at their client boundary so tests are deterministic and offline. Do
not assert on prompt wording or private helpers.

**Modules with automated tests (all logic modules, per decision):**

- `PostGenerator` — **unit**. Mock the `anthropic` client; assert it returns exactly `n`
  candidates, that `avoid` candidates are excluded from the request context, and that
  malformed model output is handled (e.g. fewer than `n` → error or retry).
- `LinkedInPublisher` — **unit**. Mock HTTP. Assert: valid token → correct POST payload
  shape and returned URL; expired token → refresh path invoked; HTTP error → raises a
  typed error (no false success). Token persistence read/write round-trips.
- `Orchestrator` — **integration (in-process)**. Wire real Orchestrator with faked
  PostGenerator/TelegramBot/LinkedInPublisher. Assert: select → publish called once with
  the chosen text + confirmation sent; regenerate → new candidates with `avoid` set;
  second callback on a published session → no second publish (idempotency); publish
  failure → error message, status not marked published.

**Not unit-tested** (side-effect/IO shells, kept thin): `TelegramBot` wiring and
`Scheduler` cron registration — covered indirectly via Orchestrator integration tests
and manual UATs.

**Prior art:** none (greenfield). Establish `tests/` with `pytest`; use
`pytest` fixtures + `unittest.mock`/`responses` (or `respx` if `httpx`) for HTTP mocking.
