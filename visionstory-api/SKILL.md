---
name: visionstory-api
description: Create and manage AI avatar videos through the VisionStory OpenAPI. Use when a user asks to generate a talking-avatar video from text or audio, create an avatar from an image, clone or select a voice, check video status, list generated videos, or work with VisionStory API resources.
---

# VisionStory Video API

Use VisionStory's REST API to create talking-avatar videos and manage the related avatars, voices, and video tasks.

## Configuration

- Use `https://openapi.visionstory.ai` as the base URL.
- Read the API key from `VISIONSTORY_API_KEY`.
- Send the key in the `X-API-Key` header.
- Send and receive JSON unless an endpoint description says otherwise.
- Never print, log, commit, or expose the API key.
- If the environment variable is missing, ask the user to configure it locally. Do not ask them to paste the key into chat.

Use this shell setup for API calls:

```bash
export VISIONSTORY_API_KEY="sk-vs-..."
export VISIONSTORY_API_BASE="https://openapi.visionstory.ai"
```

## Preferred execution

Use `scripts/visionstory_api.py` when it is present. It has no third-party Python dependencies and provides consistent
authentication, base64 encoding, polling, timeouts, error handling, and downloads. The script imports the shared
client layer from `scripts/visionstory_client.py`, which ships in the same package; keep the two files together.

Inspect its commands:

```bash
python3 scripts/visionstory_api.py --help
```

Discover resources:

```bash
python3 scripts/visionstory_api.py models
python3 scripts/visionstory_api.py avatars
python3 scripts/visionstory_api.py voices
python3 scripts/visionstory_api.py credits
```

Create an avatar from a local image:

```bash
python3 scripts/visionstory_api.py create-avatar --image /path/to/avatar.jpg
```

Create, wait for, and download a video:

```bash
python3 scripts/visionstory_api.py create-video \
  --avatar-id AVATAR_ID \
  --text "Hello from VisionStory." \
  --voice-id Alice \
  --output result.mp4
```

If only `SKILL.md` was installed and the script is unavailable, follow the HTTP workflow below.

## Workflow

1. Collect the required input:
   - A script or an audio file.
   - An existing `avatar_id` or an image for creating an avatar.
   - Optional voice, aspect ratio, resolution, emotion, and background color preferences.
2. Discover current resources instead of guessing IDs:
   - `GET /api/v1/models`
   - `GET /api/v1/avatars`
   - `GET /api/v1/voices`
3. If the user supplied an image rather than an `avatar_id`, create an avatar with `POST /api/v1/avatar`.
4. Create the video with `POST /api/v1/video`.
5. Poll `GET /api/v1/video?video_id=...` every 5 seconds until the status is `created` or `failed`. Stop after 10 minutes unless the user asks to keep waiting.
6. Return the `video_id`, final status, model, resolution, and `video_url`. Download the video when the user requests a local file.

## Create a video

Prefer `vs_character_v4` unless the user requests another model. Confirm that the selected model supports the requested resolution by checking `GET /api/v1/models`.

For a text script:

```json
{
  "model_id": "vs_character_v4",
  "avatar_id": "AVATAR_ID",
  "text_script": {
    "text": "Hello from VisionStory.",
    "voice_id": "Alice",
    "speech_rate": "normal"
  },
  "aspect_ratio": "9:16",
  "resolution": "720p",
  "emotion": "cheerful"
}
```

For an audio script, provide either `audio_url` or base64 `inline_data`:

```json
{
  "model_id": "vs_character_v4",
  "avatar_id": "AVATAR_ID",
  "audio_script": {
    "audio_url": "https://example.com/audio.mp3",
    "voice_change": false,
    "denoise": true
  },
  "aspect_ratio": "9:16",
  "resolution": "720p"
}
```

Do not send both `text_script` and `audio_script`. Use a `voice_id` returned by `GET /api/v1/voices`.

## Idempotent retries

`POST /api/v1/video` accepts an optional `client_request_id` field (pattern `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`).
Resubmitting with the same value within 24 hours returns the original task instead of creating and charging a
new one, so set it whenever a retry is possible (network timeouts, interrupted sessions):

```bash
python3 scripts/visionstory_api.py create-video \
  --avatar-id AVATAR_ID \
  --text "Hello from VisionStory." \
  --client-request-id my-task-001
```

- Reuse the same `client_request_id` when retrying the same request; never reuse it for a different request.
- HTTP 409 means the first submission with that key is still in progress. Wait a few seconds, then retry with
  the same key; you will get the original task back once it is registered.

## Create an avatar

Provide either an HTTPS image URL:

```json
{
  "img_url": "https://example.com/avatar.jpg"
}
```

Or provide base64 image data:

```json
{
  "inline_data": {
    "mime_type": "image/jpeg",
    "data": "BASE64_IMAGE_DATA"
  }
}
```

Do not invent unsupported fields. Store the returned `avatar_id` for video creation. Do not delete an avatar or voice unless the user requested deletion or explicitly approved cleanup.

## Poll a video

Treat these statuses as follows:

- `queued` or `creating`: wait 5 seconds and poll again.
- `created`: return the `video_url`.
- `failed`: stop and report the failure.

Use HTTP error handling and a bounded timeout. Videos are retained for 7 days, so download completed videos when persistent storage is required.

## Other operations

- List recent videos: `GET /api/v1/videos`
- Delete a video: `DELETE /api/v1/video?video_id=...`
- Clone a voice: `POST /api/v1/voice`
- Delete a cloned voice: `DELETE /api/v1/voice?voice_id=...`
- Check remaining credits: `GET /api/v1/billing/credits`

Before destructive requests, resolve the exact resource and confirm that it belongs to the user.

## Response handling

- Call `raise_for_status()` or equivalent before reading a response body.
- Read successful payloads from the top-level `data` field.
- Error responses include an `error.hint` field with a one-line suggested next action (for example, a top-up URL
  when credits are insufficient). Follow the hint before retrying blindly.
- Beta endpoints (the AI Video family) enforce a small per-key concurrency cap in addition to the global rate
  limit; HTTP 429 with a concurrency hint means wait for in-flight tasks to finish, then retry.
- Preserve server error details when reporting a failed request, but redact credentials and base64 file content.
- Avoid polling faster than every 5 seconds.
