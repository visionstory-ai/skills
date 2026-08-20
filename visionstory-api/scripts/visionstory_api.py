#!/usr/bin/env python3
"""VisionStory API command-line client — the Agent Skill entry point.

All HTTP, polling, and encoding lives in the shared client module. This CLI
loads it from the sibling ``visionstory_client.py`` that ships next to this
script, so it runs standalone with no third-party dependencies (Python 3.10+).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys

from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent


def _load_client_module():
    """Locate and load the shared client module by file path.

    Prefer the sibling ``visionstory_client.py`` shipped next to this script.
    Loading by path (rather than a package import) keeps the CLI runnable as a
    plain ``python scripts/visionstory_api.py`` with the script directory on
    ``sys.path``.
    """
    candidates = [_SCRIPT_DIR / "visionstory_client.py"]
    candidates += [parent / "client" / "core.py" for parent in _SCRIPT_DIR.parents]
    for path in candidates:
        if path.is_file():
            spec = importlib.util.spec_from_file_location("visionstory_client", path)
            module = importlib.util.module_from_spec(spec)
            sys.modules["visionstory_client"] = module
            spec.loader.exec_module(module)
            return module
    raise ImportError(
        "Cannot locate the VisionStory client module: expected "
        "visionstory_client.py next to this script."
    )


_client = _load_client_module()
API_KEY_ENV = _client.API_KEY_ENV
BASE_URL_ENV = _client.BASE_URL_ENV
DEFAULT_BASE_URL = _client.DEFAULT_BASE_URL
VisionStoryAPIError = _client.VisionStoryAPIError
VisionStoryClient = _client.VisionStoryClient
build_video_payload = _client.build_video_payload

# ===== BEGIN generated from the VisionStory OpenAPI spec — do not edit by hand =====
# These enum values and the client_request_id pattern are generated from the
# VisionStory API's published OpenAPI spec, so this CLI never drifts from the API.
RESOLUTIONS = ('480p', '720p', '1080p', '2k')
ASPECT_RATIOS = ('9:16', '16:9', '1:1')
EMOTIONS = ('cheerful', 'angry', 'marketing', 'news', 'singing')
SPEECH_RATES = ('slow', 'normal', 'fast')
CLIENT_REQUEST_ID_PATTERN = '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
# ===== END generated =====


def print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def add_list_command(subparsers, name: str) -> None:
    subparsers.add_parser(name)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="VisionStory OpenAPI command-line client")
    parser.add_argument(
        "--base-url",
        default=os.getenv(BASE_URL_ENV, DEFAULT_BASE_URL),
        help=f"API base URL. Defaults to ${BASE_URL_ENV} or {DEFAULT_BASE_URL}",
    )
    parser.add_argument("--request-timeout", type=int, default=60)
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("models", "avatars", "voices", "videos", "credits"):
        add_list_command(subparsers, command)

    create_avatar = subparsers.add_parser("create-avatar")
    avatar_source = create_avatar.add_mutually_exclusive_group(required=True)
    avatar_source.add_argument("--image", type=Path)
    avatar_source.add_argument("--image-url")

    create_video = subparsers.add_parser("create-video")
    create_video.add_argument("--avatar-id", required=True)
    script_source = create_video.add_mutually_exclusive_group(required=True)
    script_source.add_argument("--text")
    script_source.add_argument("--audio-file", type=Path)
    script_source.add_argument("--audio-url")
    create_video.add_argument("--voice-id", default="Alice")
    create_video.add_argument("--speech-rate", choices=SPEECH_RATES, default="normal")
    create_video.add_argument("--model-id", default="vs_character_v4")
    create_video.add_argument("--aspect-ratio", choices=ASPECT_RATIOS, default="9:16")
    create_video.add_argument("--resolution", choices=RESOLUTIONS, default="720p")
    create_video.add_argument(
        "--emotion",
        choices=EMOTIONS,
        default="cheerful",
    )
    create_video.add_argument("--background-color", default="")
    create_video.add_argument("--voice-change", action="store_true")
    create_video.add_argument("--denoise", action="store_true")
    create_video.add_argument(
        "--client-request-id",
        help="Optional idempotency key: resubmitting with the same value within 24h "
             "returns the original task instead of charging again",
    )
    create_video.add_argument("--no-wait", action="store_true")
    create_video.add_argument("--poll-interval", type=float, default=5)
    create_video.add_argument("--timeout", type=int, default=600)
    create_video.add_argument("--output", type=Path)

    status = subparsers.add_parser("status")
    status.add_argument("--video-id", required=True)

    download = subparsers.add_parser("download")
    download.add_argument("--url", required=True)
    download.add_argument("--output", required=True, type=Path)

    return parser


def create_video_payload(args) -> dict[str, Any]:
    payload = build_video_payload(
        avatar_id=args.avatar_id,
        text=args.text,
        audio_url=args.audio_url,
        audio_file=args.audio_file,
        voice_id=args.voice_id,
        speech_rate=args.speech_rate,
        model_id=args.model_id,
        aspect_ratio=args.aspect_ratio,
        resolution=args.resolution,
        emotion=args.emotion,
        background_color=args.background_color,
        voice_change=args.voice_change,
        denoise=args.denoise,
    )
    if args.client_request_id:
        if not re.match(CLIENT_REQUEST_ID_PATTERN, args.client_request_id):
            raise ValueError(
                f"--client-request-id must match {CLIENT_REQUEST_ID_PATTERN}"
            )
        payload["client_request_id"] = args.client_request_id
    return payload


def run_command(args) -> Any:
    if args.command == "download":
        VisionStoryClient.download(args.url, args.output, args.request_timeout)
        return {"output": str(args.output)}

    api_key = os.getenv(API_KEY_ENV)
    if not api_key:
        raise VisionStoryAPIError(
            f"{API_KEY_ENV} is not set. Configure it locally instead of passing the key on the command line."
        )

    client = VisionStoryClient(api_key, args.base_url, args.request_timeout)
    get_paths = {
        "models": "/api/v1/models",
        "avatars": "/api/v1/avatars",
        "voices": "/api/v1/voices",
        "videos": "/api/v1/videos",
        "credits": "/api/v1/billing/credits",
    }
    if args.command in get_paths:
        return client.request("GET", get_paths[args.command])

    if args.command == "create-avatar":
        return client.create_avatar(image_url=args.image_url, image_file=args.image)

    if args.command == "status":
        return client.get_video(args.video_id)

    if args.command == "create-video":
        if args.no_wait and args.output is not None:
            raise ValueError("--output requires waiting for the video; remove --no-wait")

        created = client.create_video(create_video_payload(args))
        video_id = str(created["video_id"])
        if args.no_wait:
            return created

        video = client.wait_for_video(
            video_id,
            poll_interval=args.poll_interval,
            timeout=args.timeout,
        )
        if args.output is not None:
            video_url = video.get("video_url")
            if not video_url:
                raise VisionStoryAPIError(f"Video {video_id} has no video_url")
            client.download(video_url, args.output, args.request_timeout)
            video["output"] = str(args.output)
        return video

    raise ValueError(f"Unsupported command: {args.command}")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        print_json(run_command(args))
        return 0
    except (OSError, TimeoutError, ValueError, VisionStoryAPIError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
