"""VisionStory API client.

A thin, dependency-free client for the VisionStory API — one shared
implementation behind the Python SDK, the command-line tool, the MCP
server, and the Agent Skill.

Design constraints:
- Python stdlib only (urllib) — no third-party dependencies.
- Auth is read from the ``VISIONSTORY_API_KEY`` environment variable; never
  accept keys via command line arguments or prompts.
- Long-running generation is wrapped in blocking wait helpers so callers
  get "one call in, one result out" semantics.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time

from pathlib import Path
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://openapi.visionstory.ai"
API_KEY_ENV = "VISIONSTORY_API_KEY"
BASE_URL_ENV = "VISIONSTORY_API_BASE"
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_AUDIO_BYTES = 30 * 1024 * 1024
MAX_BINARY_RESPONSE_BYTES = 30 * 1024 * 1024
VideoResolution = Literal["720p", "1080p", "2k"]
IMAGE_MIME_TYPES = {
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".heic": "image/heic",
}
AUDIO_MIME_TYPES = {
    ".avi": "audio/avi",
    ".mp3": "audio/mp3",
    ".mp4": "audio/mp4",
    ".m4a": "audio/m4a",
    ".wav": "audio/wav",
}

# Allowed local file types for asset upload (POST /api/v1/asset).
ASSET_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".gif": "image/gif",
    ".wav": "audio/wav",
    ".mp3": "audio/mp3",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
}
ASSET_MAX_BYTES = {
    "image": 30 * 1024 * 1024,
    "audio": 15 * 1024 * 1024,
    "video": 100 * 1024 * 1024,
}


class VisionStoryAPIError(RuntimeError):
    """API call failed. The message is written to be actionable for both
    humans and LLM agents (what happened + what to do next)."""


class VideoTimeoutError(TimeoutError):
    """Raised when video generation times out. Carries ``video_id`` so the
    caller can resume later via ``get_video``. Subclass of ``TimeoutError``."""

    def __init__(self, video_id: str, timeout: int):
        super().__init__(f"Video {video_id} timed out after {timeout}s")
        self.video_id = video_id
        self.timeout = timeout


def encode_inline_file(file_path: Path, kind: str) -> dict[str, str]:
    """Encode a local image/audio file as the API's inline_data payload."""
    mime_types = IMAGE_MIME_TYPES if kind == "image" else AUDIO_MIME_TYPES
    max_bytes = MAX_IMAGE_BYTES if kind == "image" else MAX_AUDIO_BYTES
    mime_type = mime_types.get(file_path.suffix.lower())
    if mime_type is None:
        supported = ", ".join(sorted(mime_types))
        raise ValueError(f"Unsupported {kind} file extension. Supported: {supported}")

    file_size = file_path.stat().st_size
    if file_size > max_bytes:
        raise ValueError(f"{kind.capitalize()} file exceeds the {max_bytes // 1024 // 1024}MB limit")

    return {
        "mime_type": mime_type,
        "data": base64.b64encode(file_path.read_bytes()).decode("ascii"),
    }


def encode_asset_file(file_path: Path) -> dict[str, str]:
    """Encode a local media file as the asset API's inline_data payload.

    Assets accept image, audio, and video; extensions and size caps match the
    API's limits (image ≤30MB / audio ≤15MB / video ≤100MB)."""
    mime_type = ASSET_MIME_TYPES.get(file_path.suffix.lower())
    if mime_type is None:
        supported = ", ".join(sorted(ASSET_MIME_TYPES))
        raise ValueError(f"Unsupported asset file extension. Supported: {supported}")

    kind = mime_type.split("/")[0]
    max_bytes = ASSET_MAX_BYTES[kind]
    if file_path.stat().st_size > max_bytes:
        raise ValueError(f"{kind.capitalize()} asset exceeds the {max_bytes // 1024 // 1024}MB limit")

    return {
        "mime_type": mime_type,
        "data": base64.b64encode(file_path.read_bytes()).decode("ascii"),
    }


def derive_client_request_id(payload: dict[str, Any]) -> str:
    """Derive a deterministic idempotency key (client_request_id) from the payload.

    A hash of the request parameters (not a random uuid): a retry after a timeout
    or dropped connection sends identical parameters, so the same key lets the
    retry hit the 24h idempotency cache and return the original task instead of
    being charged twice. Any difference in parameters yields a different key. The
    ``auto-`` prefix marks the key as client-derived."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "auto-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def build_video_payload(
    *,
    avatar_id: str,
    text: str | None = None,
    audio_url: str | None = None,
    audio_file: Path | None = None,
    voice_id: str = "Alice",
    speech_rate: str = "normal",
    model_id: str = "vs_character_v4",
    aspect_ratio: str = "9:16",
    resolution: VideoResolution = "720p",
    emotion: str = "cheerful",
    background_color: str = "",
    voice_change: bool = False,
    denoise: bool = False,
) -> dict[str, Any]:
    """Build a POST /api/v1/video payload from plain arguments.

    Exactly one of ``text`` / ``audio_url`` / ``audio_file`` must be given.
    """
    sources = [s for s in (text, audio_url, audio_file) if s is not None]
    if len(sources) != 1:
        raise ValueError("Provide exactly one of: text, audio_url, audio_file")

    payload: dict[str, Any] = {
        "model_id": model_id,
        "avatar_id": avatar_id,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "emotion": emotion,
    }
    if background_color:
        payload["background_color"] = background_color

    if text is not None:
        payload["text_script"] = {
            "text": text,
            "voice_id": voice_id,
            "speech_rate": speech_rate,
        }
    else:
        audio_script: dict[str, Any] = {
            "voice_change": voice_change,
            "denoise": denoise,
        }
        if audio_file is not None:
            audio_script["inline_data"] = encode_inline_file(audio_file, "audio")
        else:
            audio_script["audio_url"] = audio_url
        if voice_change:
            audio_script["voice_id"] = voice_id
        payload["audio_script"] = audio_script

    return payload


TRANSCRIBE_AUDIO_MIME_TYPES = {".wav": "audio/wav", ".mp3": "audio/mp3"}
TRANSCRIBE_MAX_AUDIO_BYTES = 15 * 1024 * 1024


def _audio_media(audio_url, audio_file, asset_id) -> dict[str, Any]:
    """Convert exactly one URL, local file, or asset ID into a MediaRef."""
    provided = [value for value in (audio_url, audio_file, asset_id) if value is not None]
    if len(provided) != 1:
        raise ValueError("Provide exactly one of: audio_url, audio_file, asset_id")
    if audio_file is not None:
        mime_type = TRANSCRIBE_AUDIO_MIME_TYPES.get(audio_file.suffix.lower())
        if mime_type is None:
            raise ValueError("Unsupported audio file extension. Supported: .mp3, .wav")
        if audio_file.stat().st_size > TRANSCRIBE_MAX_AUDIO_BYTES:
            raise ValueError("Audio file exceeds the 15MB limit")
        return {"inline_data": {
            "mime_type": mime_type,
            "data": base64.b64encode(audio_file.read_bytes()).decode("ascii"),
        }}
    if audio_url is not None:
        return {"url": audio_url}
    return {"asset_id": str(asset_id)}


class VisionStoryClient:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, request_timeout: int = 60):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.request_timeout = request_timeout

    @classmethod
    def from_env(cls, request_timeout: int = 60) -> "VisionStoryClient":
        """Build a client from environment variables (the only supported way
        for agent channels to receive credentials)."""
        api_key = os.getenv(API_KEY_ENV)
        if not api_key:
            raise VisionStoryAPIError(
                f"{API_KEY_ENV} is not set. Ask the user to create an API key at "
                "https://developers.visionstory.ai/api-keys and export it as an environment variable."
            )
        base_url = os.getenv(BASE_URL_ENV, DEFAULT_BASE_URL)
        return cls(api_key, base_url, request_timeout)

    # ---- transport -------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        timeout: int | None = None,
    ) -> Any:
        """Single HTTP call. ``timeout`` defaults to the instance
        ``request_timeout``; long-running endpoints (voice clone, asset upload)
        raise it as needed."""
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"

        body = None
        headers = {
            "Accept": "application/json",
            "X-API-Key": self.api_key,
        }
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=timeout or self.request_timeout) as response:
                response_body = response.read().decode("utf-8")
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise VisionStoryAPIError(
                f"{method} {path} failed with HTTP {exc.code}: {error_body[:2000]}"
            ) from exc
        except URLError as exc:
            raise VisionStoryAPIError(f"{method} {path} failed: {exc.reason}") from exc

        try:
            response_data = json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise VisionStoryAPIError(f"{method} {path} returned invalid JSON") from exc

        if isinstance(response_data, dict) and "data" in response_data:
            return response_data["data"]
        return response_data

    def request_binary(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """Make an authenticated request whose successful response is binary."""
        url = f"{self.base_url}{path}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Accept": "audio/*", "X-API-Key": self.api_key}
        if payload is not None:
            headers["Content-Type"] = "application/json"

        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=timeout or self.request_timeout) as response:
                declared_size = response.headers.get("Content-Length")
                if declared_size and int(declared_size) > MAX_BINARY_RESPONSE_BYTES:
                    raise VisionStoryAPIError(
                        f"{method} {path} returned more than the "
                        f"{MAX_BINARY_RESPONSE_BYTES // 1024 // 1024}MB client limit"
                    )
                response_body = response.read(MAX_BINARY_RESPONSE_BYTES + 1)
                if len(response_body) > MAX_BINARY_RESPONSE_BYTES:
                    raise VisionStoryAPIError(
                        f"{method} {path} returned more than the "
                        f"{MAX_BINARY_RESPONSE_BYTES // 1024 // 1024}MB client limit"
                    )
                response_headers = response.headers
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise VisionStoryAPIError(
                f"{method} {path} failed with HTTP {exc.code}: {error_body[:2000]}"
            ) from exc
        except URLError as exc:
            raise VisionStoryAPIError(f"{method} {path} failed: {exc.reason}") from exc

        content_type = response_headers.get_content_type()
        if not content_type.startswith("audio/"):
            raise VisionStoryAPIError(
                f"{method} {path} returned unexpected Content-Type {content_type!r}"
            )

        def _number(name: str, converter: type[int] | type[float]) -> int | float | None:
            raw = response_headers.get(name)
            if raw is None:
                return None
            try:
                return converter(raw)
            except ValueError:
                return None

        return {
            "audio": response_body,
            "mime_type": content_type,
            "audio_id": response_headers.get("X-Audio-Id"),
            "duration_sec": _number("X-Audio-Duration-Sec", float),
            "usage_characters": _number("X-Usage-Characters", int),
            "cost_credit": _number("X-Cost-Credit", int),
        }

    # ---- resource helpers (thin 1:1 wrappers over REST) ------------------

    def list_models(self) -> Any:
        return self.request("GET", "/api/v1/models")

    def list_avatars(self) -> Any:
        return self.request("GET", "/api/v1/avatars")

    def list_voices(self, *, cursor: int | None = None, limit: int | None = None,
                    locale: str | None = None, provider: str | None = None) -> Any:
        """List voices, optionally filtered by BCP 47 locale and provider."""
        params = {
            key: value
            for key, value in {
                "cursor": cursor,
                "limit": limit,
                "locale": locale,
                "provider": provider,
            }.items()
            if value is not None
        }
        return self.request("GET", "/api/v1/voices", params=params or None)

    def list_videos(self) -> Any:
        return self.request("GET", "/api/v1/videos")

    def get_credits(self) -> Any:
        return self.request("GET", "/api/v1/billing/credits")

    def create_avatar(self, *, image_url: str | None = None, image_file: Path | None = None) -> Any:
        if (image_url is None) == (image_file is None):
            raise ValueError("Provide exactly one of: image_url, image_file")
        payload = (
            {"inline_data": encode_inline_file(image_file, "image")}
            if image_file is not None
            else {"img_url": image_url}
        )
        return self.request("POST", "/api/v1/avatar", payload=payload)

    def clone_voice(
        self,
        *,
        audio_url: str | None = None,
        audio_file: Path | None = None,
        preview_text: str | None = None,
        timeout: int = 300,
    ) -> Any:
        """POST /api/v1/voice: clone a voice.

        The server runs upload → clone → status polling synchronously (can take
        90s+), so the default per-request timeout is raised to 300s."""
        if (audio_url is None) == (audio_file is None):
            raise ValueError("Provide exactly one of: audio_url, audio_file")
        payload: dict[str, Any] = {}
        if audio_file is not None:
            payload["inline_data"] = encode_inline_file(audio_file, "audio")
        else:
            payload["audio_url"] = audio_url
        if preview_text:
            payload["preview_text"] = preview_text
        return self.request("POST", "/api/v1/voice", payload=payload, timeout=timeout)

    def upload_asset(
        self,
        *,
        url: str | None = None,
        file_path: Path | None = None,
        timeout: int = 300,
    ) -> Any:
        """POST /api/v1/asset: upload an asset (beta).

        Identical uploads are de-duplicated server-side by content hash. Large
        base64 uploads can be slow, so the default per-request timeout is 300s."""
        if (url is None) == (file_path is None):
            raise ValueError("Provide exactly one of: url, file_path")
        payload = (
            {"inline_data": encode_asset_file(file_path)}
            if file_path is not None
            else {"url": url}
        )
        return self.request("POST", "/api/v1/asset", payload=payload, timeout=timeout)

    def transcribe_audio(
        self,
        *,
        audio_url: str | None = None,
        audio_file: Path | None = None,
        asset_id: str | None = None,
        diarize: bool = False,
        srt: bool = False,
    ) -> Any:
        """Transcribe audio with word timestamps and optional speakers or SRT."""
        payload: dict[str, Any] = {"audio": _audio_media(audio_url, audio_file, asset_id)}
        if diarize:
            payload["diarize"] = True
        if srt:
            payload["srt"] = True
        return self.request("POST", "/api/v1/audio/transcribe", payload=payload, timeout=300)

    def align_audio(
        self,
        *,
        text: str,
        audio_url: str | None = None,
        audio_file: Path | None = None,
        asset_id: str | None = None,
    ) -> Any:
        """Align known text with spoken audio and return word timestamps."""
        payload = {"audio": _audio_media(audio_url, audio_file, asset_id), "text": text}
        return self.request("POST", "/api/v1/audio/align", payload=payload, timeout=300)

    def create_video(self, payload: dict[str, Any]) -> Any:
        return self.request("POST", "/api/v1/video", payload=payload)

    def get_video(self, video_id: str) -> Any:
        return self.request("GET", "/api/v1/video", params={"video_id": video_id})

    def delete_video(self, video_id: str) -> Any:
        return self.request("DELETE", "/api/v1/video", params={"video_id": video_id})

    def delete_avatar(self, avatar_id: str) -> Any:
        return self.request("DELETE", "/api/v1/avatar", params={"avatar_id": avatar_id})

    def delete_voice(self, voice_id: str) -> Any:
        return self.request("DELETE", "/api/v1/voice", params={"voice_id": voice_id})

    # ---- assets ----------------------------------------------------------

    def list_assets(self, *, kind: str | None = None, cursor: str | None = None,
                    limit: int | None = None) -> Any:
        params: dict[str, Any] = {}
        if kind is not None:
            params["kind"] = kind
        if cursor is not None:
            params["cursor"] = cursor
        if limit is not None:
            params["limit"] = limit
        return self.request("GET", "/api/v1/assets", params=params or None)

    def delete_asset(self, asset_id: str) -> Any:
        return self.request("DELETE", "/api/v1/asset", params={"asset_id": asset_id})

    # ---- text to speech --------------------------------------------------

    def create_speech(self, *, text: str, voice_id: str, locale: str | None = None,
                      speech_rate: Literal["slow", "normal", "fast"] | None = None) -> Any:
        """Synthesize MP3 speech. Optional locale and pace; omitted pace defaults to normal."""
        payload = {"text": text, "voice_id": voice_id}
        if locale:
            payload["locale"] = locale
        if speech_rate is not None:
            if speech_rate not in ("slow", "normal", "fast"):
                raise ValueError("speech_rate must be slow, normal, or fast")
            payload["speech_rate"] = speech_rate
        return self.request_binary("POST", "/api/v1/tts", payload=payload)

    def understand_media(self, *, prompt: str, inputs: list[dict[str, Any]],
                         schema: dict[str, Any]) -> Any:
        """Extract structured JSON from 1–8 media references, billed only on success.

        Each input is exactly one asset_id, public url, or inline_data. The JSON
        Schema must describe an object. This synchronous request may take 180s.
        """
        if not isinstance(prompt, str) or not 1 <= len(prompt) <= 5000:
            raise ValueError("prompt must contain 1–5000 characters")
        if not isinstance(inputs, list) or not 1 <= len(inputs) <= 8:
            raise ValueError("inputs must contain 1–8 media references")
        for item in inputs:
            if (not isinstance(item, dict) or not set(item) <= {"asset_id", "url", "inline_data"}
                    or sum(value is not None for value in item.values()) != 1):
                raise ValueError("Each input must provide exactly one of asset_id, url, inline_data")
            if not next(value for value in item.values() if value is not None):
                raise ValueError("Media references cannot be empty")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise ValueError("schema must be a JSON Schema with top-level type object")
        return self.request("POST", "/api/v1/media/understand",
                            payload={"prompt": prompt, "inputs": inputs, "schema": schema}, timeout=180)

    # ---- image generation ------------------------------------------------

    def list_image_models(self) -> Any:
        return self.request("GET", "/api/v1/image/models")

    def create_image(self, payload: dict[str, Any]) -> Any:
        """POST /api/v1/image: text-to-image (beta). payload needs at least model_id + prompt."""
        return self.request("POST", "/api/v1/image", payload=payload)

    # ---- AI video (Seedance) ---------------------------------------------

    def list_ai_video_models(self) -> Any:
        return self.request("GET", "/api/v1/ai_video/models")

    def ai_video_cost(self, *, model_id: str, duration_sec: int,
                      resolution: str | None = None, generate_audio: bool | None = None) -> Any:
        params: dict[str, Any] = {"model_id": model_id, "duration_sec": duration_sec}
        if resolution is not None:
            params["resolution"] = resolution
        if generate_audio is not None:
            params["generate_audio"] = generate_audio
        return self.request("GET", "/api/v1/ai_video/cost", params=params)

    def create_ai_video(self, payload: dict[str, Any]) -> Any:
        """POST /api/v1/ai_video: submit AI video generation (beta). payload needs at least model_id + prompt."""
        return self.request("POST", "/api/v1/ai_video", payload=payload)

    def get_ai_video(self, video_id: str | None = None, *, video_ids: str | None = None) -> Any:
        if (video_id is None) == (video_ids is None):
            raise ValueError("Provide exactly one of: video_id, video_ids")
        params = {"video_id": video_id} if video_id is not None else {"video_ids": video_ids}
        return self.request("GET", "/api/v1/ai_video", params=params)

    def list_ai_videos(self, *, cursor: str | None = None, limit: int | None = None) -> Any:
        params: dict[str, Any] = {}
        if cursor is not None:
            params["cursor"] = cursor
        if limit is not None:
            params["limit"] = limit
        return self.request("GET", "/api/v1/ai_videos", params=params or None)

    def delete_ai_video(self, video_id: str) -> Any:
        return self.request("DELETE", "/api/v1/ai_video", params={"video_id": video_id})

    def wait_for_ai_video(self, video_id: str, *, poll_interval: float = 5,
                          timeout: int = 600) -> dict[str, Any]:
        """Poll an AI video task until it reaches a terminal state or times out
        (same semantics as wait_for_video)."""
        deadline = time.monotonic() + timeout
        while True:
            video = self.get_ai_video(video_id)
            status = video.get("status")
            if status == "created":
                return video
            if status == "failed":
                raise VisionStoryAPIError(
                    f"AI video {video_id} generation failed. Credits for failed tasks "
                    "are refunded automatically; retry or adjust the inputs."
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise VideoTimeoutError(video_id, timeout)
            time.sleep(min(poll_interval, remaining))

    def generate_ai_video(self, payload: dict[str, Any], *, wait: bool = True,
                          poll_interval: float = 5, timeout: int = 600) -> dict[str, Any]:
        """Submit an AI video and block until it finishes by default
        (one call in, one result out; same semantics as generate_video)."""
        created = self.create_ai_video(payload)
        if not wait:
            return created
        video_id = str(created["video_id"])
        return self.wait_for_ai_video(video_id, poll_interval=poll_interval, timeout=timeout)

    # ---- blocking helpers (agent-facing semantics) -----------------------

    def wait_for_video(
        self,
        video_id: str,
        *,
        poll_interval: float = 5,
        timeout: int = 600,
    ) -> dict[str, Any]:
        """Poll until the video reaches a terminal state or timeout."""
        deadline = time.monotonic() + timeout
        while True:
            video = self.get_video(video_id)
            status = video.get("status")
            if status == "created":
                return video
            if status == "failed":
                raise VisionStoryAPIError(
                    f"Video {video_id} generation failed. Credits for failed tasks "
                    "are refunded automatically; retry or adjust the inputs."
                )

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise VideoTimeoutError(video_id, timeout)
            time.sleep(min(poll_interval, remaining))

    def generate_video(
        self,
        payload: dict[str, Any],
        *,
        wait: bool = True,
        poll_interval: float = 5,
        timeout: int = 600,
    ) -> dict[str, Any]:
        """Submit a video job; by default block until it completes.

        This is the primary entrypoint for agent channels (MCP tools and the
        skill CLI): one call in, a finished video (with ``video_url``) out.
        """
        created = self.create_video(payload)
        if not wait:
            return created
        video_id = str(created["video_id"])
        return self.wait_for_video(video_id, poll_interval=poll_interval, timeout=timeout)

    # ---- misc ------------------------------------------------------------

    @staticmethod
    def download(url: str, output_path: Path, timeout: int = 60) -> None:
        request = Request(url, headers={"Accept": "video/*"})
        try:
            with urlopen(request, timeout=timeout) as response:
                with output_path.open("wb") as output_file:
                    while chunk := response.read(1024 * 1024):
                        output_file.write(chunk)
        except (HTTPError, URLError) as exc:
            raise VisionStoryAPIError(f"Failed to download video: {exc}") from exc
