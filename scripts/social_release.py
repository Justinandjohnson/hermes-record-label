"""Prepare or publish approved music-video deliverables."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from publishing.higgsfield_plan import (
    approve_and_expand,
    build_plan,
    generate_concept_draft,
    generate_label_video_thoughts,
    load_label_thoughts,
)
from publishing.media import render_music_video
from publishing.tiktok import direct_post_video
from publishing.youtube import authorize, upload_video


def _write_json(payload: Any, path: str | None) -> None:
    rendered = json.dumps(payload, indent=2)
    if path:
        destination = Path(path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered, encoding="utf-8")
    print(rendered)


def _read_json(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).expanduser().resolve().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return payload


def _explicit_label_thoughts(values: list[str]) -> list[dict[str, str]]:
    thoughts: list[dict[str, str]] = []
    for value in values:
        if "=" not in value:
            raise ValueError("--label-thought must use agent=thought")
        agent, thought = value.split("=", 1)
        if not agent.strip() or not thought.strip():
            raise ValueError("--label-thought must contain both agent and thought")
        thoughts.append(
            {"source": agent.strip(), "kind": "video_direction", "thought": thought.strip()}
        )
    return thoughts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Render verified YouTube and TikTok MP4s")
    prepare.add_argument("--audio", required=True)
    prepare.add_argument("--artwork", required=True)
    prepare.add_argument("--output-dir", required=True)
    prepare.add_argument("--slug", required=True)
    youtube = commands.add_parser("youtube", help="Upload an approved YouTube video")
    youtube.add_argument("--video", required=True)
    youtube.add_argument("--token", required=True)
    youtube.add_argument("--title", required=True)
    youtube.add_argument("--channel", required=True, help="Target channel ID, @handle, or title")
    youtube.add_argument("--description", default="")
    youtube.add_argument("--privacy", choices=("private", "unlisted", "public"), default="private")
    youtube.add_argument("--artist-approved", action="store_true")
    youtube_auth = commands.add_parser("youtube-auth", help="Create a YouTube OAuth token")
    youtube_auth.add_argument("--client-secrets", required=True)
    youtube_auth.add_argument("--token", required=True)
    youtube_auth.add_argument(
        "--channel", required=True, help="Channel ID, @handle, or title to verify after login"
    )
    tiktok = commands.add_parser("tiktok", help="Direct Post an approved TikTok video")
    tiktok.add_argument("--video", required=True)
    tiktok.add_argument("--title", required=True)
    tiktok.add_argument("--privacy", default="SELF_ONLY")
    tiktok.add_argument("--token-env", default="TIKTOK_ACCESS_TOKEN")
    tiktok.add_argument("--artist-approved", action="store_true")
    higgsfield = commands.add_parser("higgsfield-plan", help="Write a truthful 20-shot plan")
    higgsfield.add_argument("--concept", required=True)
    higgsfield.add_argument("--duration", required=True, type=float)
    higgsfield.add_argument("--output", required=True)
    higgsfield_draft = commands.add_parser(
        "higgsfield-draft", help="Combine artist and label thoughts into three treatments"
    )
    higgsfield_draft.add_argument("--user-direction", required=True)
    higgsfield_draft.add_argument("--duration", required=True, type=float)
    higgsfield_draft.add_argument("--aspect-ratio", default="9:16")
    higgsfield_draft.add_argument("--model-profile", required=True)
    higgsfield_draft.add_argument("--db")
    higgsfield_draft.add_argument("--track-id", type=int)
    higgsfield_draft.add_argument("--label-thought", action="append", default=[])
    higgsfield_draft.add_argument("--output", required=True)
    higgsfield_approve = commands.add_parser(
        "higgsfield-approve", help="Approve/edit one treatment and create its 20-shot plan"
    )
    higgsfield_approve.add_argument("--draft", required=True)
    higgsfield_approve.add_argument("--candidate", required=True, type=int)
    higgsfield_approve.add_argument("--edited-prompt-file")
    higgsfield_approve.add_argument("--artist-approved", action="store_true")
    higgsfield_approve.add_argument("--output", required=True)
    args = parser.parse_args()

    if args.command == "prepare":
        output = Path(args.output_dir).expanduser().resolve()
        payload = {
            "youtube": render_music_video(
                args.audio,
                args.artwork,
                output / f"{args.slug}-youtube.mp4",
                aspect_ratio="16:9",
            ),
            "tiktok": render_music_video(
                args.audio,
                args.artwork,
                output / f"{args.slug}-tiktok.mp4",
                aspect_ratio="9:16",
            ),
        }
        _write_json(payload, str(output / f"{args.slug}-deliverables.json"))
    elif args.command == "youtube-auth":
        _write_json(
            authorize(args.client_secrets, args.token, expected_channel=args.channel),
            None,
        )
    elif args.command == "youtube":
        receipt = upload_video(
            args.video,
            token_path=args.token,
            title=args.title,
            description=args.description,
            privacy_status=args.privacy,
            expected_channel=args.channel,
            artist_approved=args.artist_approved,
        )
        _write_json(receipt, None)
    elif args.command == "tiktok":
        token = os.environ.get(args.token_env, "").strip()
        if not token:
            raise RuntimeError(f"TikTok access token is missing from {args.token_env}")
        receipt = direct_post_video(
            args.video,
            access_token=token,
            title=args.title,
            privacy_level=args.privacy,
            artist_approved=args.artist_approved,
        )
        _write_json(receipt, None)
    elif args.command == "higgsfield-draft":
        thoughts = _explicit_label_thoughts(args.label_thought)
        if args.db or args.track_id:
            if not args.db or args.track_id is None:
                raise ValueError("--db and --track-id must be provided together")
            evidence = load_label_thoughts(args.db, args.track_id)
            evidence.extend(thoughts)
            thoughts = generate_label_video_thoughts(args.user_direction, evidence)
        draft = generate_concept_draft(
            args.user_direction,
            thoughts,
            _read_json(args.model_profile),
            track_duration_seconds=args.duration,
            aspect_ratio=args.aspect_ratio,
        )
        _write_json(draft, args.output)
    elif args.command == "higgsfield-approve":
        edited = None
        if args.edited_prompt_file:
            edited = (
                Path(args.edited_prompt_file).expanduser().resolve().read_text(encoding="utf-8")
            )
        approved = approve_and_expand(
            _read_json(args.draft),
            args.candidate,
            edited_prompt=edited,
            artist_approved=args.artist_approved,
        )
        _write_json(approved, args.output)
    else:
        _write_json(build_plan(args.concept, duration_seconds=args.duration), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
