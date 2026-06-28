# Tasks: Optional AI-Generated Image for LinkedIn Posts

Vertical tracer-bullet slices. Numeric order = implementation order.
Project root: `~/Projects/linkedin-post-bot/`. Builds on the existing
generator/orchestrator/bot/publisher modules.

---

## Task 01-add-image-preview

After a user picks a post candidate, the bot offers Publish / Add image / Cancel.
Tapping "Add image" crafts a LinkedIn-optimized image super-prompt from the post text
(NVIDIA/Mistral), generates an image (Together `Qwen/Qwen-Image`), and sends it to
Telegram as a photo preview. No LinkedIn publishing in this slice — the preview ends
with placeholder Publish/Cancel. The image feature is gated on `TOGETHER_API_KEY`:
absent → "Add image" is not shown and text publishing is unaffected. This is the
thinnest end-to-end image slice (config → prompt craft → image gen → Telegram preview).

### Implementation steps

- [x] Add optional `TOGETHER_API_KEY` and `TOGETHER_IMAGE_MODEL` (default `Qwen/Qwen-Image`) to config; image feature enabled iff `TOGETHER_API_KEY` present.
- [x] New `PromptCrafter.craft(post_text) -> str`: one NVIDIA chat call, system prompt for a pro LinkedIn image super-prompt; strip fences/quotes; raise `PromptCraftError` on empty.
- [x] New `ImageGenerator.generate(prompt) -> bytes`: Together `client.images.generate(model, prompt, response_format="b64_json")`; base64-decode `data[0].b64_json`; raise `ImageGenerationError` on empty/failure.
- [x] Change candidate selection so `selN` opens a decision step with Publish / Add image / Cancel (Add image only when feature enabled); add `addimg` action.
- [x] `addimg` → craft prompt → generate image → store `image_prompt`/`image_bytes` in session → `TelegramBot.present_image_preview(chat_id, image_bytes, session_id)` with Publish/Cancel.
- [x] Add `add_image` dependencies (PromptCrafter, ImageGenerator, optional) to Orchestrator + build them in `run()` only when keys present; add `together` (or OpenAI-compatible) client lib.

### Acceptance criteria

- [x] Selecting a candidate shows Publish / Add image / Cancel; with no `TOGETHER_API_KEY`, "Add image" is absent and text publish still works (UAT 1, 8). *(tests: `test_select_opens_decision_step_without_publishing`, `test_select_offers_add_image_when_feature_enabled`, `test_addimg_disabled_reports_unavailable_and_publishes_nothing`, plus existing manual/select publish tests — all green)*
- [x] Tapping "Add image" results in a photo preview message with Publish / Cancel buttons (UAT 2, partial). *(test: `test_addimg_crafts_generates_and_previews` asserts `present_image_preview` awaited; `present_image_preview` sends photo with `_image_preview_keyboard`)*
- [x] `PromptCrafter.craft` returns a non-empty cleaned string and raises `PromptCraftError` on empty/malformed model output. *(tests in `test_prompt_crafter.py`: cleaned prompt, fence/quote strip, empty→raise, no-choices→raise)*
- [x] `ImageGenerator.generate` returns decoded image bytes from `b64_json` and raises `ImageGenerationError` on empty `data`. *(tests in `test_image_generator.py`: decode, empty data, missing payload, provider error, undecodable)*
- [x] Image generation/craft failure → bot shows a clear error and publishes nothing (UAT 9). *(test: `test_addimg_generation_failure_reports_error_and_stays_open`)*
- [x] Only the authorized user can trigger `addimg` (UAT 15). *(`addimg` routes through the existing `callback_handler` gated by `auth.is_authorized`; `addimg` added to `ACTIONS`; auth covered by existing `test_bot.py` callback gate)*

### Quality gates

- [x] `pytest` green: `PromptCrafter` unit, `ImageGenerator` unit, Orchestrator `addimg` integration (craft→generate→preview, feature-disabled path, failure path). *(86 passed)*
- [x] `python -m compileall .` no errors. *(`compileall linkedin_post_bot auth_linkedin.py tests` exit 0)*
- [x] `ruff check .` no violations. *(All checks passed)*

---

## Task 02-publish-with-image

From the image preview, tapping Publish posts the selected text with the image attached
to LinkedIn: register an image upload, PUT the bytes, then create an IMAGE post
referencing the asset. The published post carries the exact selected text. `DRY_RUN`
covers this path (reply "would publish text + image", no LinkedIn call). Idempotency and
clear-error behavior match the existing publish path.

### Implementation steps

- [x] Add `LinkedInPublisher.publish_with_image(text, image_bytes, alt_text=None) -> url`: registerUpload (`feedshare-image`, owner=author URN) → PUT bytes to `uploadUrl` → `ugcPosts` with `shareMediaCategory=IMAGE` + asset URN; reuse `ensure_token()`; raise `PublishError` on any step failure. *(`publisher.py`: `publish_with_image` + `_register_image_upload`/`_upload_image_bytes`/`_extract_upload_target` helpers; new `REGISTER_UPLOAD_URL`/`FEEDSHARE_IMAGE_RECIPE` constants.)*
- [x] Add `pubimg` action: publish_with_image with session text + image_bytes; `open→published` guard before the call; revert to `open` on failure. *(`orchestrator.py`: `pubimg` in `ACTIONS`, dispatch to `_publish_with_image`.)*
- [x] Dry-run: when `dry_run`, `pubimg` sends a "would publish text + image" confirmation and makes no publisher call. *(`_publish_with_image` dry-run branch calls `confirm_dry_run`, no publisher call.)*
- [x] On success send confirmation with live link; on failure send clear error; strip preview keyboard on terminal outcome. *(success→`bot.confirm`; failure→`bot.send_error` + revert to open; keyboard stripped by `callback_handler.edit_message_reply_markup`.)*
- [x] Re-tap on published/cancelled preview does nothing (idempotency). *(non-open guard in `handle_callback`; image-preview keyboard now uses `pubimg`.)*

### Acceptance criteria

- [x] With `DRY_RUN=true`, Publish on the preview replies it would publish text+image and makes no LinkedIn call (UAT 6). *(test: `test_pubimg_dry_run_makes_no_linkedin_call`.)*
- [x] `publish_with_image` performs registerUpload → PUT bytes → IMAGE ugcPost in order with the asset URN, and returns the post URL (verified with mocked HTTP). *(test: `test_publish_with_image_runs_three_step_flow`.)*
- [x] Published text equals the selected candidate text (UAT 16). *(test: `test_pubimg_publishes_with_image_and_selected_text` asserts `image_calls == [(posts[1], ...)]`.)*
- [x] Publish failure → clear error, session stays open, no false success (UAT 14). *(tests: `test_pubimg_failure_reports_error_and_stays_open`; publisher `register`/`upload`/`post` failure → `PublishError` with no later step attempted.)*
- [x] Re-tapping Publish on a published preview publishes nothing again (UAT 10, 17). *(test: `test_pubimg_double_publishes_once`.)*
- [ ] Live publish to a real profile with image attached (UAT 7) — *deferred (manual, needs live credentials)*.

### Quality gates

- [x] `pytest` green: `LinkedInPublisher.publish_with_image` unit (mock httpx: 3-step sequence, payload shape, error→`PublishError`) + Orchestrator `pubimg` integration (publish once, dry-run no-call, failure-stays-open, double-publish-once). *(94 passed.)*
- [x] `python -m compileall .` no errors. *(`compileall linkedin_post_bot auth_linkedin.py tests` exit 0.)*
- [x] `ruff check .` no violations. *(All checks passed.)*

---

## Task 03-preview-controls

The image preview gains full control: Regenerate image (new image, same prompt),
Regenerate prompt (new super-prompt then new image), Provide my own prompt (user types a
Qwen-Image prompt → image from it), and Cancel. Each keeps the user in the preview review
loop until they Publish or Cancel.

### Implementation steps

- [x] Add actions `regimg` (regenerate image from stored `image_prompt`), `regprompt` (craft new prompt → generate → preview), `ownprompt` (await user text).
- [x] Extend `present_image_preview` keyboard: Publish / Regenerate image / Regenerate prompt / Provide my own prompt / Cancel.
- [x] `ownprompt`: set a per-chat awaiting flag (mirror `/posta` capture) → next message becomes the Qwen-Image prompt → generate → preview; clear flag.
- [x] `cancel` on preview: mark session cancelled, publish nothing, strip keyboard.
- [x] Each regenerate updates session `image_prompt`/`image_bytes` and re-previews.

### Acceptance criteria

- [x] "Regenerate image" produces a new image from the same stored prompt and re-previews (UAT 3).
- [x] "Regenerate prompt" crafts a new super-prompt, generates a new image, and re-previews (UAT 4).
- [x] "Provide my own prompt" → user's next message is used as the image prompt and the resulting image is previewed (UAT 5).
- [x] "Cancel" on the preview posts nothing and removes the keyboard (UAT 11).
- [x] Buttons on a resolved (published/cancelled) preview do nothing (UAT 10, 17).
- [x] Own-prompt capture only fires for the authorized user while awaiting (UAT 15).

### Quality gates

- [x] `pytest` green: Orchestrator integration for `regimg`/`regprompt`/`ownprompt`/`cancel` (correct generator calls, session updates, idempotency) + own-prompt capture handler. *(106 passed.)*
- [x] `python -m compileall .` no errors. *(compileall linkedin_post_bot auth_linkedin.py tests exit 0.)*
- [x] `ruff check .` no violations. *(All checks passed.)*
