"""Real YouTube Data API upload with an explicit artist-approval gate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from publishing.media import probe_video

YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_READ_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
YOUTUBE_SCOPES = [YOUTUBE_UPLOAD_SCOPE, YOUTUBE_READ_SCOPE]


def _channel_identity(channel: dict[str, Any]) -> set[str]:
    snippet = channel.get("snippet") or {}
    values = {
        str(channel.get("id") or ""),
        str(snippet.get("title") or ""),
        str(snippet.get("customUrl") or ""),
    }
    return {value.strip().casefold().removeprefix("@") for value in values if value.strip()}


def _authorized_channel(youtube: Any, expected_channel: str) -> dict[str, str]:
    expected = expected_channel.strip().casefold().removeprefix("@")
    if not expected:
        raise ValueError("The target YouTube channel ID, handle, or title is required")
    response = youtube.channels().list(part="id,snippet", mine=True, maxResults=50).execute()
    channels = response.get("items") or []
    for channel in channels:
        if expected in _channel_identity(channel):
            snippet = channel.get("snippet") or {}
            return {
                "id": str(channel["id"]),
                "title": str(snippet.get("title") or ""),
                "handle": str(snippet.get("customUrl") or ""),
            }
    authorized = [
        f"{(item.get('snippet') or {}).get('title', 'unknown')} ({item.get('id', 'unknown')})"
        for item in channels
    ]
    raise PermissionError(
        f"OAuth authorized {authorized or ['no channel']}, not {expected_channel!r}. "
        "Repeat login and select the requested YouTube channel."
    )


def authorize(
    client_secrets_path: str | Path,
    token_path: str | Path,
    *,
    expected_channel: str,
) -> dict[str, Any]:
    """Complete installed-app OAuth and persist a refreshable upload token."""
    client_secrets = Path(client_secrets_path).expanduser().resolve()
    token = Path(token_path).expanduser().resolve()
    if not client_secrets.is_file():
        raise FileNotFoundError(f"YouTube OAuth client secrets not found: {client_secrets}")
    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secrets),
        scopes=YOUTUBE_SCOPES,
    )
    credentials = flow.run_local_server(host="localhost", port=0, open_browser=True)
    youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    channel = _authorized_channel(youtube, expected_channel)
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text(credentials.to_json(), encoding="utf-8")
    return {"token_path": str(token), "scopes": YOUTUBE_SCOPES, "channel": channel}


def upload_video(
    video_path: str | Path,
    *,
    token_path: str | Path,
    title: str,
    description: str,
    tags: list[str] | None = None,
    category_id: str = "10",
    privacy_status: str = "private",
    expected_channel: str,
    artist_approved: bool = False,
) -> dict[str, Any]:
    """Upload and return a concrete YouTube receipt.

    ``artist_approved`` must be true even for private uploads because this call
    transmits the artist's media to a third party.
    """
    if not artist_approved:
        raise PermissionError("Artist approval is required before any YouTube upload")
    if privacy_status not in {"private", "unlisted", "public"}:
        raise ValueError("Invalid YouTube privacy status")
    video = Path(video_path).expanduser().resolve()
    probe_video(video)
    token = Path(token_path).expanduser().resolve()
    if not token.is_file():
        raise FileNotFoundError(f"YouTube OAuth token not found: {token}")
    credentials = Credentials.from_authorized_user_file(str(token), YOUTUBE_SCOPES)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        token.write_text(credentials.to_json(), encoding="utf-8")
    if not credentials.valid:
        raise PermissionError("YouTube OAuth credentials are invalid or expired")

    youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    channel = _authorized_channel(youtube, expected_channel)
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags or [],
            "categoryId": category_id,
        },
        "status": {"privacyStatus": privacy_status},
    }
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=MediaFileUpload(str(video), mimetype="video/mp4", resumable=True),
    )
    response = None
    while response is None:
        _, response = request.next_chunk()
    video_id = str(response["id"])
    status = youtube.videos().list(part="status,processingDetails", id=video_id).execute()
    return {
        "platform": "youtube",
        "external_id": video_id,
        "url": f"https://youtu.be/{video_id}",
        "privacy_status": privacy_status,
        "channel": channel,
        "status": status,
    }
