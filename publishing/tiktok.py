"""TikTok Content Posting API client with creator-capability preflight."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from publishing.media import probe_video

API = "https://open.tiktokapis.com/v2/post/publish"
MIB = 1024 * 1024


def _raise_api_error(payload: dict[str, Any]) -> None:
    error = payload.get("error") or {}
    if error.get("code") not in {None, "ok"}:
        raise RuntimeError(f"TikTok API error {error.get('code')}: {error.get('message')}")


def query_creator_info(access_token: str) -> dict[str, Any]:
    response = httpx.post(
        f"{API}/creator_info/query/",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json={},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    _raise_api_error(payload)
    return dict(payload["data"])


def _source_info(size: int) -> dict[str, Any]:
    if size <= 128 * MIB:
        return {
            "source": "FILE_UPLOAD",
            "video_size": size,
            "chunk_size": size,
            "total_chunk_count": 1,
        }
    chunk_size = 64 * MIB
    total = size // chunk_size
    final_size = size - chunk_size * (total - 1)
    if final_size > 128 * MIB:
        raise ValueError("TikTok chunk plan exceeds the 128 MiB final-chunk limit")
    return {
        "source": "FILE_UPLOAD",
        "video_size": size,
        "chunk_size": chunk_size,
        "total_chunk_count": total,
    }


def direct_post_video(
    video_path: str | Path,
    *,
    access_token: str,
    title: str,
    privacy_level: str = "SELF_ONLY",
    disable_comment: bool = False,
    disable_duet: bool = False,
    disable_stitch: bool = False,
    artist_approved: bool = False,
) -> dict[str, Any]:
    """Upload a real video and return the TikTok publish ID/status receipt."""
    if not artist_approved:
        raise PermissionError("Artist approval is required before any TikTok upload")
    video = Path(video_path).expanduser().resolve()
    probe = probe_video(video)
    video_stream = next(
        stream for stream in probe["streams"] if stream.get("codec_type") == "video"
    )
    if video.suffix.lower() != ".mp4" or video_stream.get("codec_name") != "h264":
        raise ValueError("TikTok deliverable must be an MP4 with H.264 video")

    creator = query_creator_info(access_token)
    allowed_privacy = creator.get("privacy_level_options") or []
    if privacy_level not in allowed_privacy:
        raise ValueError(f"TikTok creator does not allow privacy level {privacy_level}")
    duration = float(probe.get("format", {}).get("duration") or 0)
    maximum = int(creator.get("max_video_post_duration_sec") or 0)
    if maximum and duration > maximum:
        raise ValueError(f"Video is {duration:.1f}s; creator limit is {maximum}s")

    source = _source_info(video.stat().st_size)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
    }
    init = httpx.post(
        f"{API}/video/init/",
        headers=headers,
        json={
            "post_info": {
                "title": title,
                "privacy_level": privacy_level,
                "disable_comment": disable_comment,
                "disable_duet": disable_duet,
                "disable_stitch": disable_stitch,
            },
            "source_info": source,
        },
        timeout=30,
    )
    init.raise_for_status()
    init_payload = init.json()
    _raise_api_error(init_payload)
    data = init_payload["data"]
    upload_url = str(data["upload_url"])
    publish_id = str(data["publish_id"])

    size = video.stat().st_size
    count = int(source["total_chunk_count"])
    nominal = int(source["chunk_size"])
    with video.open("rb") as handle:
        start = 0
        for index in range(count):
            amount = size - start if index == count - 1 else nominal
            chunk = handle.read(amount)
            end = start + len(chunk) - 1
            upload = httpx.put(
                upload_url,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Range": f"bytes {start}-{end}/{size}",
                },
                content=chunk,
                timeout=300,
            )
            upload.raise_for_status()
            start = end + 1
    if start != size:
        raise RuntimeError(f"TikTok upload incomplete: sent {start} of {size} bytes")

    status_response = httpx.post(
        f"{API}/status/fetch/",
        headers=headers,
        json={"publish_id": publish_id},
        timeout=30,
    )
    status_response.raise_for_status()
    status = status_response.json()
    _raise_api_error(status)
    return {
        "platform": "tiktok",
        "publish_id": publish_id,
        "privacy_level": privacy_level,
        "status": status,
    }
