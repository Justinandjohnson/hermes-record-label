#!/usr/bin/env python3
"""
vault_tools.py — Backblaze B2 cloud vault access library.

Used by:
- Hermes agents (read-only key) for searching, listing, and generating share URLs
- sync_to_cloud.sh (admin key) for uploads
- intake_album.py for registering uploaded versions

Environment variables:
    B2_READ_KEY_ID          — read-only application key ID
    B2_READ_APPLICATION_KEY — read-only application key
    B2_WRITE_KEY_ID         — admin key ID (for uploads only, never passed to agents)
    B2_WRITE_APPLICATION_KEY — admin key
    B2_BUCKET_NAME          — bucket name (e.g. "ai-record-label-vault")
    B2_ENDPOINT_URL         — S3-compatible endpoint (e.g. https://s3.us-west-004.backblazeb2.com)
    AI_RECORD_LABEL_DATA    — data directory path
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

DATA_DIR = Path(os.environ.get(
    "AI_RECORD_LABEL_DATA",
    Path.home() / "Library/Application Support/ai-record-label",
))
DB_PATH = DATA_DIR / "hermes.db"

BUCKET = os.environ.get("B2_BUCKET_NAME", "ai-record-label-vault")
ENDPOINT = os.environ.get("B2_ENDPOINT_URL", "")


# ---------------------------------------------------------------------------
# B2 client (read-only — safe for agent use)
# ---------------------------------------------------------------------------

def _get_s3_client(write: bool = False):
    """Return a boto3 S3 client pointed at B2. Uses read-only keys by default."""
    try:
        import boto3  # type: ignore[import]
        from botocore.config import Config  # type: ignore[import]
    except ImportError:
        raise RuntimeError("boto3 not installed. Run: pip install boto3")

    if write:
        key_id = os.environ["B2_WRITE_KEY_ID"]
        app_key = os.environ["B2_WRITE_APPLICATION_KEY"]
    else:
        key_id = os.environ.get("B2_READ_KEY_ID", "")
        app_key = os.environ.get("B2_READ_APPLICATION_KEY", "")

    if not key_id or not app_key:
        raise RuntimeError(
            "B2 credentials not set. Add B2_READ_KEY_ID and B2_READ_APPLICATION_KEY to .env"
        )

    # B2 S3-compatible API requires region_name derived from endpoint
    # e.g. "https://s3.us-east-005.backblazeb2.com" → region "us-east-005"
    region = "us-east-005"
    if ENDPOINT:
        import re
        m = re.search(r"s3\.([^.]+)\.backblazeb2\.com", ENDPOINT)
        if m:
            region = m.group(1)

    return boto3.client(
        service_name="s3",
        endpoint_url=ENDPOINT or None,
        aws_access_key_id=key_id,
        aws_secret_access_key=app_key,
        region_name=region,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
        ),
    )


# ---------------------------------------------------------------------------
# Search & list
# ---------------------------------------------------------------------------

def list_vault_files(prefix: str = "music/", max_keys: int = 1000) -> list[dict[str, Any]]:
    """
    List files in the B2 vault under a given prefix.
    Returns: [{name, size, last_modified, key}, ...]
    """
    client = _get_s3_client()
    paginator = client.get_paginator("list_objects_v2")
    results = []
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix, PaginationConfig={"MaxItems": max_keys}):
        for obj in page.get("Contents", []):
            results.append({
                "key": obj["Key"],
                "name": Path(obj["Key"]).name,
                "size": obj["Size"],
                "size_mb": round(obj["Size"] / (1024 * 1024), 2),
                "last_modified": obj["LastModified"].isoformat(),
            })
    return results


def search_vault(query: str, prefix: str = "music/") -> list[dict[str, Any]]:
    """
    Search vault files by name (case-insensitive substring match).
    Returns matching file metadata.
    """
    all_files = list_vault_files(prefix=prefix)
    q = query.lower()
    return [f for f in all_files if q in f["name"].lower() or q in f["key"].lower()]


def get_vault_stats() -> dict[str, Any]:
    """Return total file count and storage used in the vault."""
    files = list_vault_files(prefix="")
    total_bytes = sum(f["size"] for f in files)
    return {
        "file_count": len(files),
        "total_gb": round(total_bytes / (1024 ** 3), 3),
        "total_mb": round(total_bytes / (1024 ** 2), 1),
    }


# ---------------------------------------------------------------------------
# Download URLs (sharing)
# ---------------------------------------------------------------------------

def get_download_url(b2_key: str, valid_seconds: int = 3600) -> str:
    """
    Generate a presigned download URL for sharing a file.
    Default expiry: 1 hour. Max: 7 days (604800 seconds).
    """
    client = _get_s3_client()
    url = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": BUCKET, "Key": b2_key},
        ExpiresIn=valid_seconds,
    )
    return url


def get_share_url_for_track(track_id: int, valid_hours: int = 24) -> dict[str, Any]:
    """
    Get a shareable URL for a track's latest uploaded version.
    Looks up the file_versions table by track_id.
    """
    conn = sqlite3.connect(DB_PATH, timeout=60.0)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """SELECT fv.b2_key, fv.label, t.title
           FROM file_versions fv
           JOIN tracks t ON t.id = fv.track_id
           WHERE fv.track_id = ? AND fv.b2_key IS NOT NULL
           ORDER BY fv.version_num DESC LIMIT 1""",
        (track_id,),
    ).fetchone()
    conn.close()

    if not row:
        return {"error": f"No uploaded version found for track_id={track_id}"}

    url = get_download_url(row["b2_key"], valid_seconds=valid_hours * 3600)
    return {
        "track_id": track_id,
        "title": row["title"],
        "version_label": row["label"],
        "url": url,
        "expires_in_hours": valid_hours,
    }


# ---------------------------------------------------------------------------
# Comments / annotations (SQLite — no cloud needed)
# ---------------------------------------------------------------------------

def add_track_comment(
    track_id: int,
    body: str,
    author: str,
    timestamp_s: float | None = None,
    version_id: int | None = None,
) -> int:
    """
    Add a timestamped comment to a track. Returns the new comment id.
    timestamp_s: position in seconds (None = general comment).
    """
    conn = sqlite3.connect(DB_PATH, timeout=60.0)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.execute(
        """INSERT INTO track_comments (track_id, version_id, timestamp_s, author, body)
           VALUES (?, ?, ?, ?, ?)""",
        (track_id, version_id, timestamp_s, author, body),
    )
    comment_id = cur.lastrowid
    conn.commit()
    conn.close()
    if comment_id is None:
        raise RuntimeError("INSERT did not return a rowid — check DB connection")
    return comment_id


def get_track_comments(track_id: int, include_resolved: bool = False) -> list[dict[str, Any]]:
    """Get all comments for a track, ordered by timestamp."""
    conn = sqlite3.connect(DB_PATH, timeout=60.0)
    conn.row_factory = sqlite3.Row
    query = """SELECT tc.*, fv.label as version_label
               FROM track_comments tc
               LEFT JOIN file_versions fv ON fv.id = tc.version_id
               WHERE tc.track_id = ?"""
    params: list[Any] = [track_id]
    if not include_resolved:
        query += " AND tc.resolved = 0"
    query += " ORDER BY tc.timestamp_s ASC NULLS LAST, tc.created_at ASC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def resolve_comment(comment_id: int) -> bool:
    """Mark a comment as resolved. Returns True if found and updated."""
    conn = sqlite3.connect(DB_PATH, timeout=60.0)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.execute(
        "UPDATE track_comments SET resolved = 1 WHERE id = ?", (comment_id,)
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Sync status
# ---------------------------------------------------------------------------

def get_sync_status() -> dict[str, Any]:
    """Return recent sync log entries and overall status."""
    conn = sqlite3.connect(DB_PATH, timeout=60.0)
    conn.row_factory = sqlite3.Row
    recent = conn.execute(
        """SELECT operation, status, bytes, error, synced_at
           FROM cloud_sync_log
           ORDER BY synced_at DESC LIMIT 20"""
    ).fetchall()
    last_success = conn.execute(
        """SELECT synced_at FROM cloud_sync_log
           WHERE status = 'success' AND operation IN ('upload', 'sync')
           ORDER BY synced_at DESC LIMIT 1"""
    ).fetchone()
    conn.close()
    return {
        "last_successful_sync": last_success["synced_at"] if last_success else None,
        "recent_operations": [dict(r) for r in recent],
    }


def log_sync_operation(
    operation: str,
    status: str,
    b2_key: str | None = None,
    file_path: str | None = None,
    error: str | None = None,
    bytes_transferred: int | None = None,
    duration_ms: int | None = None,
) -> None:
    """Write a sync audit log entry."""
    conn = sqlite3.connect(DB_PATH, timeout=60.0)
    conn.execute(
        """INSERT INTO cloud_sync_log (operation, b2_key, file_path, status, error, bytes, duration_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (operation, b2_key, file_path, status, error, bytes_transferred, duration_ms),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Restic backup trigger
# ---------------------------------------------------------------------------

def trigger_restic_backup(music_dir: str | None = None) -> dict[str, Any]:
    """
    Stub — B2 vault via boto3 is the backup system. Restic is not used.
    Kept for API compatibility; returns a no-op success response.
    """
    return {"status": "not_configured", "message": "B2 vault is the backup system. Use sync_to_cloud.sh."}


if __name__ == "__main__":
    # Quick test: list vault files (requires B2 env vars)
    import json
    print("Vault stats:", json.dumps(get_vault_stats(), indent=2))
