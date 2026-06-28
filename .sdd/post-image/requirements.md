# Requirements: Optional AI-Generated Image for LinkedIn Posts

## Problem Statement

Text-only LinkedIn posts get less reach and engagement than posts with a strong
visual. Right now the bot can only publish text. When I want an image I have to make
one elsewhere and I can't do it from the same Telegram flow, so in practice I post
without images.

## Solution

After I pick a post candidate, the bot offers to attach an AI-generated image. If I
choose to add one, the bot writes an image "super prompt" optimized for a LinkedIn
visual from my post text, generates an image, and shows it to me in Telegram as a
preview. From the preview I can publish with the image, regenerate the image,
regenerate the prompt (and image), type my own image prompt, or cancel. Publishing
attaches the image to the LinkedIn post. The whole thing stays inside the existing
Telegram conversation and respects dry-run.

## User Stories

1. As a user, after picking a post candidate, I want buttons to Publish, Add image, or Cancel, so that adding an image is an explicit opt-in.
2. As a user, when I tap "Add image", I want the bot to derive an image prompt from my post text automatically, so that I don't have to write one.
3. As a user, I want that derived prompt to be a "super prompt" optimized for a professional LinkedIn image, so that the result looks suitable for my feed.
4. As a user, I want the generated image shown to me in Telegram before publishing, so that I can judge it.
5. As a user, I want a "Regenerate image" button, so that I can get a different image from the same prompt.
6. As a user, I want a "Regenerate prompt" button, so that the bot writes a new super prompt and then a new image when I don't like the direction.
7. As a user, I want a "Provide my own prompt" option, so that I can type an exact Qwen-Image prompt myself.
8. As a user, when I provide my own prompt, I want the bot to generate from it and show the preview, so that I stay in the same review loop.
9. As a user, I want a "Publish" button on the preview that posts the text with the image attached, so that one tap finishes the job.
10. As a user, I want a "Cancel" button at every step, so that I can abandon without posting.
11. As a user, I want the existing text-only publish path to keep working unchanged, so that I can still post without an image.
12. As a user, I want dry-run to also cover images (show what would be posted, including the image, with no LinkedIn call), so that I can trial safely.
13. As a user, I want the image feature to simply be unavailable (with a clear message) if no image service key is configured, so that the rest of the bot still works.
14. As a user, I want a clear error message if image generation or upload fails, so that I know to retry and nothing is half-posted.
15. As a user, I want only my authorized Telegram account to use the image actions, so that the same access rule as the rest of the bot applies.
16. As a user, I want the post that gets published with an image to contain the exact post text I selected, so that the image doesn't change my words.
17. As a user, I want re-tapping buttons on an already-published or cancelled preview to do nothing, so that I never double-post.

## User Acceptance Tests

1. Given I have picked a post candidate, when the candidate is shown, then I see Publish, Add image, and Cancel buttons.
2. Given I tap "Add image", when the bot finishes, then I receive an image in Telegram with Publish / Regenerate image / Regenerate prompt / Provide my own prompt / Cancel buttons.
3. Given an image preview, when I tap "Regenerate image", then I receive a new image generated from the same prompt.
4. Given an image preview, when I tap "Regenerate prompt", then the bot produces a new super prompt and a new image, and shows the new preview.
5. Given an image preview, when I tap "Provide my own prompt" and send a prompt, then the bot generates an image from my prompt and shows the preview.
6. Given an image preview with dry-run enabled, when I tap Publish, then the bot replies indicating it would publish the text plus the image and makes no LinkedIn call.
7. Given an image preview with dry-run disabled, when I tap Publish, then the post appears on my LinkedIn profile with the image attached and the exact selected text, and the bot replies with the live link.
8. Given no image service key is configured, when I pick a candidate, then "Add image" is absent or replies that the image feature is unavailable, and text publishing still works.
9. Given image generation fails, when I tap "Add image", then the bot shows a clear error and does not publish anything.
10. Given an image was already published (or the flow cancelled), when I tap any button on that preview again, then nothing is published.
11. Given I tap Cancel at the preview, when the bot handles it, then nothing is posted and the keyboard is removed.

## Definition of Done

- All user acceptance tests pass.
- Picking a candidate offers Publish / Add image / Cancel; the text-only path is unchanged.
- Adding an image derives a LinkedIn-optimized prompt, generates an image, and previews it before any publish.
- The preview supports regenerate-image, regenerate-prompt, own-prompt, publish, and cancel.
- Publishing with an image results in a real LinkedIn image post carrying the selected text (verified manually with live credentials).
- Dry-run covers the image path with no LinkedIn call.
- Image feature degrades gracefully (clear message, no crash) when its key is absent.
- Failures in prompt-crafting, image generation, or upload produce clear messages and never half-post or double-post.
- Automated tests pass for the new/extended logic modules.
- README and `.env.example` document the image feature and its configuration.

## Out of Scope

- Multiple images / carousels — one image per post.
- Editing the image (crop, filters, text overlay) inside Telegram.
- Video or document attachments.
- Choosing image dimensions/aspect ratio from Telegram (a sensible default is used).
- Storing/reusing previously generated images across sessions.
- Image moderation/safety classification beyond what the providers enforce.
- Alt-text accessibility field tuning (a basic alt text derived from the prompt is acceptable but not a hard requirement).

## Further Notes

- Image generation uses Together AI with the `Qwen/Qwen-Image` model.
- LinkedIn image posting is a multi-step API flow (register upload → upload bytes →
  reference the returned asset in the post). It requires the same `w_member_social`
  authorization already in place.
- The image super-prompt is produced by the same NVIDIA/Mistral LLM already used for
  post generation — no new text provider.

---

## Technical Annex
> Written against codebase as of: 2026-06-28

Existing baseline (verified this session): Python package `linkedin_post_bot` with
`config.py` (dotenv, optional `NVIDIA_API_KEY`, `DRY_RUN`), `post_text_generator.py`
(`PostTextGenerator` over an OpenAI-compatible NVIDIA client), `orchestrator.py`
(`Orchestrator` with a `{session_id: {topic, candidates, status}}` map, `open→published`
idempotency guard, actions `sel1..3`, `regen`, `pub`, `cancel`), `bot.py` (`TelegramBot`
with `/genera`, `/posta`, inline keyboards, callback handler gated by `auth.is_authorized`,
callback data `"<session_id>:<action>"`), `publisher.py` (`LinkedInPublisher` with
`ensure_token()` and `publish(text) -> url` POSTing `/v2/ugcPosts`, author URN cached in
`linkedin_token.json`), plus `rotation.py`/`scheduler.py`. Tests under `tests/` use
`pytest` + `unittest.mock` and (for HTTP) mocked `httpx`.

### Architectural Decisions

**New modules (deep, isolated):**

1. `PromptCrafter` (`prompt_crafter.py`)
   - `craft(post_text: str) -> str` — one NVIDIA/Mistral chat call with a system prompt
     instructing it to output a single image-generation "super prompt" optimized for a
     professional LinkedIn visual (subject, style, composition, lighting; no text in image;
     safe-for-work). Returns plain string (strip fences/quotes, reuse the `_unwrap_*`
     hardening already added to `post_text_generator.py`).
   - Injectable OpenAI-compatible client (same client type as `PostTextGenerator`). Raises
     `PromptCraftError` on empty/failed output.

2. `ImageGenerator` (`image_generator.py`)
   - `generate(prompt: str) -> bytes` — calls Together `client.images.generate(
     model=<TOGETHER_IMAGE_MODEL>, prompt=prompt, response_format="b64_json")`, decodes
     `response.data[0].b64_json` to PNG/JPEG bytes. Injectable Together client. Raises
     `ImageGenerationError` on failure/empty data.
   - Prototype contract (from the user's snippet):
     ```python
     response = client.images.generate(prompt="...", model="Qwen/Qwen-Image")
     image_bytes = base64.b64decode(response.data[0].b64_json)
     ```

**Extended modules:**

3. `LinkedInPublisher` (`publisher.py`)
   - Add `publish_with_image(text: str, image_bytes: bytes, alt_text: str | None = None) -> str`.
   - Flow: `ensure_token()` → POST `/v2/assets?action=registerUpload` (recipe
     `feedshare-image`, owner = author URN) → PUT bytes to the returned `uploadUrl` →
     POST `/v2/ugcPosts` with `shareMediaCategory=IMAGE` and the returned asset URN.
   - Existing `publish(text)` unchanged. Errors raise the existing `PublishError`
     (no false success).

4. `Orchestrator` (`orchestrator.py`)
   - Session entry gains: `image_prompt`, `image_bytes`, `mode` ("text"|"image").
   - New actions on callback data: `addimg`, `regimg` (regenerate image, same prompt),
     `regprompt` (new super prompt + image), `ownprompt` (await user text → image),
     `pubimg` (publish with image), plus existing `pub`/`cancel`.
   - Holds injected `PromptCrafter` + `ImageGenerator` (both optional — None when keys
     absent → `addimg` disabled / replies unavailable).
   - Idempotency: `pubimg` honors the same `open→published` guard; failures revert to `open`.
   - Dry-run: when `config.dry_run`, `pubimg` sends a "would publish text + image" confirmation
     (image already previewed) and makes no publisher call.

5. `TelegramBot` (`bot.py`)
   - Candidate presentation keyboard extended with **Add image** (only when image feature
     enabled). New `present_image_preview(chat_id, image_bytes, session_id)` sends the photo
     with Publish / Regenerate image / Regenerate prompt / Provide my own prompt / Cancel.
   - "Provide my own prompt" sets a per-chat awaiting flag (mirrors the `/posta` capture
     handler) to capture the next message as the Qwen-Image prompt.
   - Auth gate unchanged (all new callbacks pass through `is_authorized`).

6. `config.py`
   - Add optional `TOGETHER_API_KEY` and `TOGETHER_IMAGE_MODEL` (default `Qwen/Qwen-Image`).
     Image feature enabled iff `TOGETHER_API_KEY` present (NVIDIA key already required for
     the super prompt; if NVIDIA key absent the whole generation path is already disabled).

**Config / data flow:** Telegram callback → Orchestrator action dispatch → (PromptCrafter →
ImageGenerator) → bot preview → publish action → LinkedInPublisher.publish_with_image. Image
bytes live only in session state in memory; not persisted.

### Automated Testing Decisions

**What makes a good test here:** exercise each module's public interface; mock the three
external boundaries (NVIDIA chat client, Together images client, LinkedIn HTTP). Assert
behavior and payload shape, not prompt wording or private helpers. Deterministic + offline.

**Modules with automated tests (all logic modules):**

- `PromptCrafter` — **unit**. Mock NVIDIA client: returns a non-empty crafted string;
  strips fences/quotes; raises `PromptCraftError` on empty/malformed output.
- `ImageGenerator` — **unit**. Mock Together client: decodes `b64_json` to bytes;
  raises `ImageGenerationError` on empty `data`/exception.
- `LinkedInPublisher.publish_with_image` — **unit** (mock `httpx`). Assert the 3-step
  sequence (registerUpload → PUT bytes → ugcPosts with IMAGE category + asset URN),
  returned URL, token-refresh reuse, and HTTP error → `PublishError` (no false success).
- `Orchestrator` image flow — **integration** (fake PromptCrafter/ImageGenerator/Publisher/
  Bot). Assert: `addimg` crafts→generates→previews; `regimg` reuses prompt, new image;
  `regprompt` new prompt + image; `ownprompt` uses supplied text; `pubimg` publishes once
  with image + selected text; dry-run makes no publish call; failure reverts to open and
  reports; double `pubimg` publishes once; feature-disabled (no generators) path replies
  unavailable.

**Not unit-tested** (thin IO shells): `TelegramBot` photo-send wiring and the actual
Together/LinkedIn network calls — covered via Orchestrator integration + manual UATs.

**Prior art:** mirror `tests/test_post_text_generator.py` (mock chat client), `tests/test_publisher.py`
(mock `httpx`, payload-shape asserts, error→typed-exception), and `tests/test_orchestrator.py`
(fakes + idempotency/dry-run assertions).
