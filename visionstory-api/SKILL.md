---
name: visionstory-api
description: Create and manage talking-avatar videos, voices, speech, audio transcripts, and structured media extraction through the VisionStory OpenAPI. Use for avatar video generation, voice discovery or cloning, text-to-speech, transcription, alignment, media understanding, task status, downloads, and related VisionStory API resources.
---

# VisionStory API

Use VisionStory's REST API to create talking-avatar videos, select voices by locale, synthesize speech, and produce timestamped transcripts.

## Configuration

- Use `https://openapi.visionstory.ai` as the base URL.
- Read the API key from `VISIONSTORY_API_KEY`.
- Send the key in the `X-API-Key` header.
- Send and receive JSON unless an endpoint description says otherwise.
- Never print, log, commit, or expose the API key.
- If the environment variable is missing, ask the user to configure it locally. Do not ask them to paste the key into chat.
- When the VisionStory CLI is installed, use `visionstory login` for interactive CLI authentication instead of
  constructing a shell prompt or editing a shell profile. Use `visionstory logout` to remove that saved CLI key.

Use this shell setup for API calls:

```bash
export VISIONSTORY_API_KEY="sk-vs-..."
export VISIONSTORY_API_BASE="https://openapi.visionstory.ai"
```

The SDK, MCP server, and bundled helper continue to read `VISIONSTORY_API_KEY` from the environment. A CLI login is
local to the CLI and must not be presented as configuring those other channels.

## Preferred execution

When the `visionstory` CLI is available and authentication was configured with `visionstory login`, prefer CLI commands
so the saved credential is used. Inspect available commands with `visionstory --help`, and discover current resources
with `visionstory models`, `visionstory avatars`, `visionstory voices`, and `visionstory credits` before generation.

Otherwise, use `scripts/visionstory_api.py` when it is present. It has no third-party Python dependencies and provides consistent
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
python3 scripts/visionstory_api.py voices --locale en-GB --limit 20
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

Create speech, transcribe audio, or align an exact script:

```bash
python3 scripts/visionstory_api.py tts --text "Hello" --voice-id VOICE_ID --locale en-GB --output speech.mp3
python3 scripts/visionstory_api.py transcribe --audio-file speech.mp3 --srt --output speech.srt
python3 scripts/visionstory_api.py align --audio-file speech.mp3 --text "Hello"
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
   Filter voices with a BCP 47 `locale`: `en` matches all English variants, while `en-GB`, `zh-TW`, or `zh-HK` selects one region. Reuse a returned voice's `locale` with `POST /api/v1/tts` when pronunciation should stay regional.
3. If the user supplied an image rather than an `avatar_id`, create an avatar with `POST /api/v1/avatar`.
4. Create the video with `POST /api/v1/video`.
5. Poll `GET /api/v1/video?video_id=...` every 5 seconds until the status is `created` or `failed`. Stop after 10 minutes unless the user asks to keep waiting.
6. Return the `video_id`, final status, model, resolution, and `video_url`. Download the video when the user requests a local file.

## Create a video

Prefer `vs_character_v4` unless the user requests another model. Confirm that the selected model supports the requested resolution by checking `GET /api/v1/models`. Offer only the documented `720p`, `1080p`, or `2k` values. The API keeps legacy raw HTTP requests that send `480p` working by rendering and billing them as `720p`, but `480p` is not a current model capability and must not be suggested for new requests.

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
- Create MP3 speech: `POST /api/v1/tts` (`text`, `voice_id`, optional `locale` and `speech_rate`: `slow`, `normal`, or `fast`). Omitted/null rate uses normal speed. Read actual duration from `X-Audio-Duration-Sec`; billing is unchanged. The helper accepts `tts --speech-rate slow`.
- Extract structured media data: `POST /api/v1/media/understand` with required `prompt` (1–5000 characters), `inputs` (1–8 MediaRef objects), and an object-root JSON `schema`. Each input uses exactly one `url`, `asset_id`, or `inline_data`. This synchronous operation can take 180 seconds. It returns `data.output`, `data.usage` (input/output token counts), and `data.cost_credit`. Successful calls are billed from token usage, rounded up to at least 1 credit; moderation refusal is `37100`. There is no public model selector or free-text mode. Do not blindly retry a timeout: repeating a successful request can charge again.
- Transcribe audio: `POST /api/v1/audio/transcribe` (`audio`, optional independent `diarize` / `srt`; SRT does not require speaker labels)
- Align known text: `POST /api/v1/audio/align` (`audio` and `text`)
- Check remaining credits: `GET /api/v1/billing/credits`

Before destructive requests, resolve the exact resource and confirm that it belongs to the user.

For structured extraction, replace the example URL with media the user can access:

```bash
python3 scripts/visionstory_api.py understand-media --prompt "Identify the main subject" --inputs '[{"url":"https://example.com/photo.jpg"}]' --schema '{"type":"object","properties":{"subject":{"type":"string"}},"required":["subject"],"additionalProperties":false}'
```

For AI video, discover `GET /api/v1/ai_video/models` before selecting a model or limits. Seedance and Wan support `480p`; this does not re-enable `480p` for talking-avatar creation. Kling 3.0 supports `4k`; Kling 3.0 Omni does not. Wan supports prompts up to 20000 characters and integer durations of 2–30 seconds; other models have different limits. Use the cost endpoint before generation. Reference inputs and `generate_audio` do not increase the price. Failed tasks refund credits without automatically switching to another model. Read the [AI video guide](https://developers.visionstory.ai/guides/ai-video.md) for model-specific input combinations.

## Response handling

- Call `raise_for_status()` or equivalent before reading a response body.
- Read successful payloads from the top-level `data` field.
- `POST /api/v1/tts` is the exception: its body is MP3 audio and usage metadata is returned in response headers.
- Error responses include an `error.hint` field with a one-line suggested next action (for example, a top-up URL
  when credits are insufficient). Follow the hint before retrying blindly.
- Beta endpoints (the AI Video family) enforce a small per-key concurrency cap in addition to the global rate
  limit; HTTP 429 with a concurrency hint means wait for in-flight tasks to finish, then retry.
- Preserve server error details when reporting a failed request, but redact credentials and base64 file content.
- Avoid polling faster than every 5 seconds.
