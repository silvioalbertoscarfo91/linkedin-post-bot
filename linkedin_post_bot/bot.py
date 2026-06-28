"""Telegram bot wiring.

Slice 01: async ``/genera`` echoing the topic for the authorized user only.
Slice 02: ``/genera`` now drives the Orchestrator to generate three candidate
posts and present them with inline buttons (Post 1/2/3 + "Give me 3 more").
Callback data is ``<session_id>:<action>`` with action in
``{sel1, sel2, sel3, regen}``.
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .auth import is_authorized
from .config import Config

logger = logging.getLogger(__name__)

NO_TOPIC_REPLY = "Please provide a topic, e.g. /genera AI in finance"
REGEN_LABEL = "Give me 3 more"

# Shown when /genera is used but no NVIDIA API key is configured. Manual
# posting via /posta still works without a key.
NO_API_KEY_REPLY = (
    "NVIDIA_API_KEY is not set, so I can't generate posts. "
    "Set it in your .env to use /genera, or use /posta to publish your own text."
)
PASTE_PROMPT = "Send me the post text in your next message, and I'll show you a preview."
# Key in chat_data marking that we're awaiting a pasted post body.
AWAITING_POST_KEY = "awaiting_manual_post"

OWN_PROMPT_PROMPT = (
    "Send me the image prompt in your next message, and I'll generate a new preview."
)
# Key in chat_data holding the session id awaiting a user-supplied image prompt.
# Distinct from AWAITING_POST_KEY so the two capture flows never collide.
AWAITING_IMAGE_PROMPT_KEY = "awaiting_image_prompt_session"


def _extract_topic(args: list[str] | None) -> str:
    """Join command arguments into a trimmed topic string."""
    if not args:
        return ""
    return " ".join(args).strip()


def _render_candidates(posts: list[str]) -> str:
    """Render numbered candidate posts into a single message body."""
    blocks = []
    for i, post in enumerate(posts, start=1):
        blocks.append(f"Post {i}:\n{post}")
    return "\n\n———\n\n".join(blocks)


def _keyboard(session_id: str, count: int) -> InlineKeyboardMarkup:
    """Build the Post 1/2/3 + 'Give me 3 more' inline keyboard."""
    post_buttons = [
        InlineKeyboardButton(f"Post {i}", callback_data=f"{session_id}:sel{i}")
        for i in range(1, count + 1)
    ]
    regen_button = InlineKeyboardButton(
        REGEN_LABEL, callback_data=f"{session_id}:regen"
    )
    return InlineKeyboardMarkup([post_buttons, [regen_button]])


def _manual_keyboard(session_id: str) -> InlineKeyboardMarkup:
    """Build the Publish / Cancel keyboard for a manually-pasted post."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Publish", callback_data=f"{session_id}:pub"
                ),
                InlineKeyboardButton(
                    "Cancel", callback_data=f"{session_id}:cancel"
                ),
            ]
        ]
    )


def _decision_keyboard(session_id: str, *, image_enabled: bool) -> InlineKeyboardMarkup:
    """Build the Publish / [Add image] / Cancel keyboard for a chosen candidate.

    "Add image" is only offered when the image feature is enabled.
    """
    buttons = [InlineKeyboardButton("Publish", callback_data=f"{session_id}:pub")]
    if image_enabled:
        buttons.append(
            InlineKeyboardButton("Add image", callback_data=f"{session_id}:addimg")
        )
    buttons.append(
        InlineKeyboardButton("Cancel", callback_data=f"{session_id}:cancel")
    )
    return InlineKeyboardMarkup([buttons])


def _image_preview_keyboard(session_id: str) -> InlineKeyboardMarkup:
    """Build the full control keyboard shown under an image preview.

    Publish (``pubimg``) posts the previewed image with the selected text.
    Regenerate image (``regimg``) makes a new image from the same prompt.
    Regenerate prompt (``regprompt``) crafts a new super-prompt then a new image.
    Provide my own prompt (``ownprompt``) captures the next message as the prompt.
    Cancel (``cancel``) abandons without posting and strips the keyboard.
    """
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Publish", callback_data=f"{session_id}:pubimg")],
            [
                InlineKeyboardButton(
                    "Regenerate image", callback_data=f"{session_id}:regimg"
                ),
                InlineKeyboardButton(
                    "Regenerate prompt", callback_data=f"{session_id}:regprompt"
                ),
            ],
            [
                InlineKeyboardButton(
                    "Provide my own prompt", callback_data=f"{session_id}:ownprompt"
                )
            ],
            [InlineKeyboardButton("Cancel", callback_data=f"{session_id}:cancel")],
        ]
    )


class TelegramBot:
    """IO shell: renders presentations and acks; delegates logic to Orchestrator."""

    def __init__(self, application: Application) -> None:
        self._application = application

    async def present(
        self, chat_id: int, session_id: str, posts: list[str]
    ) -> None:
        """Send the numbered candidates plus the inline keyboard."""
        await self._application.bot.send_message(
            chat_id=chat_id,
            text=_render_candidates(posts),
            reply_markup=_keyboard(session_id, len(posts)),
        )

    async def present_manual(
        self, chat_id: int, session_id: str, text: str
    ) -> None:
        """Send a single pasted post body with Publish / Cancel buttons."""
        await self._application.bot.send_message(
            chat_id=chat_id,
            text=f"Preview:\n\n{text}",
            reply_markup=_manual_keyboard(session_id),
        )

    async def present_decision(
        self,
        chat_id: int,
        session_id: str,
        chosen: str,
        *,
        image_enabled: bool,
    ) -> None:
        """Show the chosen candidate with Publish / Add image / Cancel buttons."""
        await self._application.bot.send_message(
            chat_id=chat_id,
            text=f"Selected:\n\n{chosen}",
            reply_markup=_decision_keyboard(session_id, image_enabled=image_enabled),
        )

    async def present_image_preview(
        self, chat_id: int, image_bytes: bytes, session_id: str
    ) -> None:
        """Send the generated image as a photo with Publish / Cancel buttons."""
        await self._application.bot.send_photo(
            chat_id=chat_id,
            photo=image_bytes,
            reply_markup=_image_preview_keyboard(session_id),
        )

    async def request_own_prompt(self, chat_id: int, session_id: str) -> None:
        """Prompt for a user-supplied image prompt and arm the capture flag.

        Stores the awaiting session id in this chat's ``chat_data`` under a key
        distinct from the ``/posta`` flag, so the next plain message routes to
        the own-prompt flow (see :func:`manual_text_handler`).
        """
        self._application.chat_data[chat_id][AWAITING_IMAGE_PROMPT_KEY] = session_id
        await self._application.bot.send_message(
            chat_id=chat_id,
            text=OWN_PROMPT_PROMPT,
        )

    async def acknowledge(
        self, chat_id: int, session_id: str, chosen: str
    ) -> None:
        """Acknowledge a selected candidate without publishing (fallback path)."""
        await self._application.bot.send_message(
            chat_id=chat_id,
            text=f"Selected:\n\n{chosen}",
        )

    async def confirm_dry_run(
        self, chat_id: int, session_id: str, chosen: str
    ) -> None:
        """Dry-run confirmation: show exactly what would be published."""
        await self._application.bot.send_message(
            chat_id=chat_id,
            text=f"DRY RUN - would publish:\n\n{chosen}",
        )

    async def cancel(self, chat_id: int, session_id: str) -> None:
        """Confirm that a manual post was cancelled (keyboard already stripped)."""
        await self._application.bot.send_message(
            chat_id=chat_id,
            text="Cancelled - nothing was published.",
        )

    async def confirm(
        self, chat_id: int, session_id: str, chosen: str, url: str
    ) -> None:
        """Confirm a successful publish with a link to the live post."""
        await self._application.bot.send_message(
            chat_id=chat_id,
            text=f"Published to LinkedIn:\n{url}",
        )

    async def send_error(self, chat_id: int, message: str) -> None:
        """Surface a user-facing error message."""
        await self._application.bot.send_message(chat_id=chat_id, text=message)


async def genera_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ``/genera <topic>``.

    Ignores any user other than the configured allowed id. With no topic,
    replies asking for one. Otherwise generates and presents candidate posts.
    """
    config: Config = context.application.bot_data["config"]

    user = update.effective_user
    user_id = user.id if user is not None else None
    if not is_authorized(user_id, config.telegram_allowed_user_id):
        logger.info("Ignoring /genera from unauthorized user id=%s", user_id)
        return

    message = update.effective_message
    if message is None:
        return

    if not config.nvidia_api_key:
        await message.reply_text(NO_API_KEY_REPLY)
        return

    topic = _extract_topic(context.args)
    if not topic:
        await message.reply_text(NO_TOPIC_REPLY)
        return

    orchestrator = context.application.bot_data["orchestrator"]
    try:
        await orchestrator.run(topic, message.chat_id)
    except Exception:  # noqa: BLE001 - already reported to the user by the orchestrator
        logger.exception("Orchestrator.run failed for topic %r", topic)


async def posta_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ``/posta``: ask the authorized user for text to publish manually.

    Sets a per-chat flag so the next plain message from the user is captured as
    the post body (see :func:`manual_text_handler`). Bypasses generation
    entirely, so it works without a NVIDIA API key.
    """
    config: Config = context.application.bot_data["config"]

    user = update.effective_user
    user_id = user.id if user is not None else None
    if not is_authorized(user_id, config.telegram_allowed_user_id):
        logger.info("Ignoring /posta from unauthorized user id=%s", user_id)
        return

    message = update.effective_message
    if message is None:
        return

    context.chat_data[AWAITING_POST_KEY] = True
    await message.reply_text(PASTE_PROMPT)


async def manual_text_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Capture the next plain message for whichever capture flow is awaiting.

    Routes to the ``/posta`` manual-post flow (:data:`AWAITING_POST_KEY`) or the
    image own-prompt flow (:data:`AWAITING_IMAGE_PROMPT_KEY`) - never both, and
    the two flags are distinct so they can't collide. Only acts for the
    authorized user and only when a capture is pending; otherwise it is a no-op
    so ordinary chatter is ignored.
    """
    config: Config = context.application.bot_data["config"]

    user = update.effective_user
    user_id = user.id if user is not None else None
    if not is_authorized(user_id, config.telegram_allowed_user_id):
        return

    awaiting_post = bool(context.chat_data.get(AWAITING_POST_KEY))
    awaiting_image_session = context.chat_data.get(AWAITING_IMAGE_PROMPT_KEY)
    if not awaiting_post and not awaiting_image_session:
        return

    message = update.effective_message
    if message is None:
        return

    orchestrator = context.application.bot_data["orchestrator"]
    text = (message.text or "").strip()

    if awaiting_image_session:
        if not text:
            await message.reply_text(
                "That message had no text. Tap 'Provide my own prompt' again."
            )
            context.chat_data.pop(AWAITING_IMAGE_PROMPT_KEY, None)
            return
        context.chat_data.pop(AWAITING_IMAGE_PROMPT_KEY, None)
        await orchestrator.submit_own_prompt(awaiting_image_session, text)
        return

    if not text:
        await message.reply_text(
            "That message had no text. Send /posta again with the post text."
        )
        context.chat_data[AWAITING_POST_KEY] = False
        return

    context.chat_data[AWAITING_POST_KEY] = False
    await orchestrator.present_manual(text, message.chat_id)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route an inline-button tap to the Orchestrator."""
    config: Config = context.application.bot_data["config"]

    user = update.effective_user
    user_id = user.id if user is not None else None
    if not is_authorized(user_id, config.telegram_allowed_user_id):
        logger.info("Ignoring callback from unauthorized user id=%s", user_id)
        return

    query = update.callback_query
    if query is None or not query.data:
        return

    await query.answer()
    session_id, _, action = query.data.partition(":")
    if not session_id or not action:
        logger.warning("Malformed callback data %r", query.data)
        return

    # Remove the keyboard so the choice can't be re-tapped.
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001 - editing may fail if already edited
        logger.debug("Could not strip keyboard for session %s", session_id)

    orchestrator = context.application.bot_data["orchestrator"]
    await orchestrator.handle_callback(session_id, action)


def build_application(config: Config, orchestrator=None) -> Application:
    """Build a configured (but not yet running) telegram ``Application``.

    If ``orchestrator`` is None, the caller is expected to build and attach one
    that uses a :class:`TelegramBot` wrapping this application.
    """
    application = Application.builder().token(config.telegram_bot_token).build()
    application.bot_data["config"] = config
    if orchestrator is not None:
        application.bot_data["orchestrator"] = orchestrator
    application.add_handler(CommandHandler("genera", genera_command))
    application.add_handler(CommandHandler("posta", posta_command))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, manual_text_handler)
    )
    application.add_handler(CallbackQueryHandler(callback_handler))
    return application


def run(config: Config) -> None:
    """Build the application, wire the orchestrator, and run via long polling."""
    import httpx
    from openai import OpenAI

    from .orchestrator import Orchestrator
    from .post_text_generator import PostTextGenerator
    from .publisher import LinkedInPublisher

    application = build_application(config)

    # The NVIDIA key is optional: without it /genera replies with a clear
    # "key required" message (handled in genera_command) and only manual /posta
    # works. Only build the generator when a key is present.
    generator = None
    nvidia_client = None
    if config.nvidia_api_key:
        nvidia_client = OpenAI(
            api_key=config.nvidia_api_key, base_url=config.nvidia_base_url
        )
        generator = PostTextGenerator(nvidia_client, model=config.nvidia_model)

    # Optional AI image feature: enabled only when TOGETHER_API_KEY is present
    # (and the NVIDIA client exists to craft the super prompt). Absent -> the
    # orchestrator reports the feature disabled and text publishing is
    # unaffected.
    prompt_crafter = None
    image_generator = None
    if config.together_api_key and nvidia_client is not None:
        from together import Together

        from .image_generator import ImageGenerator
        from .prompt_crafter import PromptCrafter

        prompt_crafter = PromptCrafter(
            nvidia_client,
            model=config.nvidia_model,
            allow_text=config.together_image_allow_text,
        )
        image_generator = ImageGenerator(
            Together(api_key=config.together_api_key),
            model=config.together_image_model,
            allow_text=config.together_image_allow_text,
            width=config.together_image_width,
            height=config.together_image_height,
        )

    bot = TelegramBot(application)

    publisher = LinkedInPublisher(
        httpx.Client(timeout=30),
        client_id=config.linkedin_client_id,
        client_secret=config.linkedin_client_secret,
    )
    orchestrator = Orchestrator(
        generator,
        bot,
        publisher,
        dry_run=config.dry_run,
        prompt_crafter=prompt_crafter,
        image_generator=image_generator,
    )
    application.bot_data["orchestrator"] = orchestrator

    # Wire the daily scheduled rotation onto the *same* orchestrator/bot instance
    # the manual /genera command uses. Fails fast here if the topics file is
    # missing/empty (clear startup error, no silent crash at fire time).
    # The rotation drives generation, so it is only enabled when a NVIDIA
    # key is present; manual-only setups (no key) start without it.
    if generator is not None:
        from .rotation import TopicRotation
        from .scheduler import Scheduler

        rotation = TopicRotation(config.topics_path)
        scheduler = Scheduler(
            orchestrator,
            rotation,
            config.telegram_allowed_user_id,
            hour=config.schedule_hour,
            minute=config.schedule_minute,
        )

        async def _start_scheduler(app: Application) -> None:
            scheduler.start()

        async def _stop_scheduler(app: Application) -> None:
            scheduler.shutdown()

        application.post_init = _start_scheduler
        application.post_shutdown = _stop_scheduler
    else:
        logger.info(
            "No NVIDIA_API_KEY set: scheduled rotation disabled, "
            "manual /posta only."
        )

    logger.info("Starting LinkedIn post bot (long polling)...")
    application.run_polling()
