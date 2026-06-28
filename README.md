# LinkedIn Post Automation Bot

A Telegram-driven bot that generates LinkedIn post candidates with Claude and publishes
a chosen one to your real LinkedIn profile. Send `/genera <topic>`, the bot replies with
three candidates and inline buttons; tap one and it is published to LinkedIn, and the bot
confirms with a link to the live post. Only a single authorized Telegram user is served.

## Requirements

- macOS
- Python 3.11+ (developed and tested on 3.14)

## Setup

### 1. Create a Telegram bot and get its token

1. Open Telegram and message [@BotFather](https://t.me/BotFather).
2. Send `/newbot` and follow the prompts to name your bot.
3. BotFather replies with an **HTTP API token** — copy it.

### 2. Find your Telegram user id

Message [@userinfobot](https://t.me/userinfobot) (or similar) and note the numeric
**Id** it returns. Only this id will be served by the bot; all other users are ignored.

### 3. Configure `.env`

Copy the example and fill in the values:

```bash
cp .env.example .env
```

Required:

- `TELEGRAM_BOT_TOKEN` — the token from BotFather.
- `TELEGRAM_ALLOWED_USER_ID` — your numeric Telegram user id.

Optional:

- `ANTHROPIC_API_KEY` — your Claude API key. Only `/genera` (Claude-generated
  candidates) needs it. Leave it blank to run **manual-only**: `/posta` still publishes
  your own text, and `/genera` simply replies that a key is required (no crash). The
  daily rotation is also disabled without a key.
- `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET`, `LINKEDIN_REDIRECT_URI` — see the
  LinkedIn setup section below (needed to actually publish; not needed for `DRY_RUN`).
- `DRY_RUN` — set to `true` to trial the publish flow without touching LinkedIn. Tapping
  Publish then shows exactly what would be posted and makes no LinkedIn call. Defaults to
  `false`.

The bot fails fast at startup with a clear message if a required Telegram variable is
missing.

### 4. Set up a LinkedIn Developer app and authorize (one-time)

Publishing uses the official LinkedIn REST API, so you need a LinkedIn Developer app.

1. Go to <https://www.linkedin.com/developers/apps> and **Create app** (associate it with
   a Company Page you administer — required by LinkedIn, even for personal posting).
2. On the app's **Products** tab, request/enable:
   - **Sign In with LinkedIn using OpenID Connect** — grants `openid` + `profile`, which
     back the `/v2/userinfo` identity endpoint used to resolve your author URN.
   - **Share on LinkedIn** — grants `w_member_social`, required to publish posts.
3. On the **Auth** tab:
   - Copy the **Client ID** and **Client Secret** into `.env` as `LINKEDIN_CLIENT_ID`
     and `LINKEDIN_CLIENT_SECRET`.
   - Add an **Authorized redirect URL** that exactly matches `LINKEDIN_REDIRECT_URI`
     in your `.env`. The default is `http://localhost:8000/callback`.
4. Run the one-time OAuth bootstrap (after creating the venv in step 5):

   ```bash
   source .venv/bin/activate
   python auth_linkedin.py
   ```

   This opens the LinkedIn authorization page in your browser, catches the redirect on
   the local callback, exchanges the code for an access/refresh token, resolves your
   author URN, and writes `linkedin_token.json` (gitignored). The bot loads and
   auto-refreshes this token on every publish, so you do not re-login each time. If the
   refresh token ever expires, re-run `python auth_linkedin.py`.

The combined scopes are `openid profile w_member_social`.

### 5. Create the virtual environment and install

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"      # or: pip install -r requirements.txt
```

### 6. Configure the daily topic rotation (optional)

The bot can also post automatically once a day, picking the next topic from a rotation
list so you keep a cadence without manual action.

1. Create your rotation file (one topic per line; blank lines and `#` comments ignored):

   ```bash
   cp topics.txt.example topics.txt
   # then edit topics.txt with your own topics
   ```

2. Optionally set the schedule and file path in `.env` (defaults shown):

   ```
   TOPICS_PATH=topics.txt
   SCHEDULE_HOUR=9      # 0-23, local time
   SCHEDULE_MINUTE=0    # 0-59
   ```

The scheduler walks the topics round-robin and persists its position in
`topics.txt.index.json` (gitignored) next to the topics file, so the rotation continues
where it left off after a restart. If the topics file is missing or empty, the bot fails
fast at startup with a clear message rather than crashing silently later.

## Run

```bash
source .venv/bin/activate
python -m linkedin_post_bot
```

The same process serves both the manual `/genera <topic>` command and the daily scheduled
rotation — they share one bot. At `SCHEDULE_HOUR:SCHEDULE_MINUTE` each day the bot sends
three candidates for the next rotation topic to your authorized Telegram chat with no
manual action; you select or regenerate exactly as with the manual command.

### Keeping the process running on macOS

The schedule only fires while the process is alive, so keep it running. Simplest is to
leave the `python -m linkedin_post_bot` process open in a terminal (or `tmux`/`screen`).
To have macOS keep it running across logins, create a **launchd** agent at
`~/Library/LaunchAgents/com.local.linkedin-post-bot.plist` that runs
`/path/to/.venv/bin/python -m linkedin_post_bot` from the project directory with
`RunAtLoad` and `KeepAlive` set to `true`, then load it with
`launchctl load ~/Library/LaunchAgents/com.local.linkedin-post-bot.plist`.

Then, from your authorized Telegram account, send:

```
/genera AI in finance
```

The bot replies with three candidate posts and four inline buttons (Post 1/2/3 + "Give
me 3 more"). Tap a post to publish it to your LinkedIn profile — the bot confirms with a
link to the live post. "Give me 3 more" regenerates three fresh candidates. Once you have
chosen and published, the buttons no longer act (no double-posting). If publishing fails,
the bot tells you clearly and nothing is posted. Sending `/genera` with no topic replies
asking for one. Messages from any other account are ignored.

### Manual posting with `/posta`

To publish text you wrote yourself (no Claude involved), send:

```
/posta
```

The bot asks for the text; your **next** message is captured as the post body and shown
as a preview with **Publish** / **Cancel** buttons. Tap **Publish** to post it to
LinkedIn (the bot confirms with a link); tap **Cancel** to discard it (the buttons are
removed and nothing is posted). Re-tapping a resolved message does nothing. Because
`/posta` never calls Claude, it works even with no `ANTHROPIC_API_KEY` set.

When `DRY_RUN=true`, tapping **Publish** (for either `/posta` or `/genera`) replies with
exactly what *would* be published and makes no LinkedIn call — handy for trialing the
flow before going live.

## Development

```bash
pytest          # run tests
ruff check .    # lint
```
