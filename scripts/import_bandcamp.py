#!/usr/bin/env python3
"""Bandcamp catalog importer.

Scrapes an artist's Bandcamp page and imports every album into the
ai-record-label SQLite database as RELEASED projects with their tracklists.

Usage:
    python scripts/import_bandcamp.py https://artistname.bandcamp.com
    python scripts/import_bandcamp.py https://artistname.bandcamp.com --overwrite
    python scripts/import_bandcamp.py https://artistname.bandcamp.com --dry-run

The importer leans on Bandcamp's <script type="application/ld+json"> block
which contains a fully structured representation of each album, including
the tracklist, durations, descriptions, tags, release dates and cover art.
HTML scraping is reserved for the artist /music index page where the
album cards live (each carries a ``data-item-id`` attribute).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError as exc:  # pragma: no cover - import error guidance
    print(
        "Missing dependency: install with `uv pip install requests beautifulsoup4` "
        "or run via the project venv.",
        file=sys.stderr,
    )
    raise SystemExit(1) from exc


# ── Constants ────────────────────────────────────────────────────────────

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

REQUEST_TIMEOUT = 20  # seconds
REQUEST_DELAY = 1.0   # polite delay between requests


# ── DB path resolution (mirrors mcp_server.py) ───────────────────────────

def resolve_db_path() -> str:
    explicit_db = os.environ.get("DB_PATH")
    if explicit_db:
        return explicit_db

    explicit_dir = os.environ.get("AI_RECORD_LABEL_DATA")
    if explicit_dir:
        return str(Path(explicit_dir) / "hermes.db")

    system = platform.system()
    if system == "Darwin":
        base = Path.home() / "Library" / "Application Support" / "ai-record-label"
    elif system == "Windows":
        appdata = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        base = Path(appdata) / "ai-record-label"
    else:
        base = Path.home() / ".local" / "share" / "ai-record-label"
    return str(base / "hermes.db")


# ── Data models ──────────────────────────────────────────────────────────

@dataclass
class BandcampTrack:
    title: str
    track_number: int
    duration_seconds: float | None = None
    bandcamp_track_url: str | None = None
    streaming_url: str | None = None


@dataclass
class BandcampAlbum:
    title: str
    album_url: str
    bandcamp_id: str | None = None
    release_date: str | None = None
    description: str | None = None
    tags: list[str] = field(default_factory=list)
    cover_art_url: str | None = None
    tracks: list[BandcampTrack] = field(default_factory=list)


# ── HTTP helpers ─────────────────────────────────────────────────────────

def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.5",
        }
    )
    return session


def fetch(session: requests.Session, url: str) -> str:
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    time.sleep(REQUEST_DELAY)
    return response.text


# ── Parsing ──────────────────────────────────────────────────────────────

def normalize_artist_root(url: str) -> str:
    """Return ``https://artist.bandcamp.com`` with no trailing slash or path."""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or parsed.path
    return f"{scheme}://{netloc}".rstrip("/")


def discover_album_urls(html: str, artist_root: str) -> list[tuple[str, str | None]]:
    """Parse the artist's /music page, returning (album_url, bandcamp_id)."""
    soup = BeautifulSoup(html, "html.parser")
    album_urls: list[tuple[str, str | None]] = []
    seen: set[str] = set()

    grid = soup.find("ol", id="music-grid") or soup.find("ol", class_="music-grid")
    candidates = grid.find_all("li") if grid else soup.find_all("li", attrs={"data-item-id": True})

    for item in candidates:
        href = None
        anchor = item.find("a", href=True)
        if anchor:
            href = str(anchor["href"])
        if not href:
            continue
        full_url = urljoin(artist_root + "/", href)
        if full_url in seen:
            continue
        seen.add(full_url)

        item_id_raw = item.get("data-item-id")  # e.g. "album-1234567890"
        item_id: str | None = str(item_id_raw) if item_id_raw is not None else None
        if item_id and "-" in item_id:
            item_id = item_id.split("-", 1)[1]
        album_urls.append((full_url, item_id))

    # Fall back to a broader anchor scan if the grid is missing.
    if not album_urls:
        for anchor in soup.find_all("a", href=True):
            href = str(anchor["href"])
            if href.startswith("/album/") or href.startswith("/track/"):
                full_url = urljoin(artist_root + "/", href)
                if full_url not in seen:
                    seen.add(full_url)
                    album_urls.append((full_url, None))

    return album_urls


def _parse_iso_duration(duration: str | None) -> float | None:
    """Convert an ISO 8601 PT#H#M#S duration into seconds."""
    if not duration:
        return None
    match = re.match(
        r"^P(?:(?P<days>\d+(?:\.\d+)?)D)?"
        r"T?(?:(?P<hours>\d+(?:\.\d+)?)H)?"
        r"(?:(?P<minutes>\d+(?:\.\d+)?)M)?"
        r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?$",
        duration.strip(),
    )
    if not match:
        return None
    parts = {k: float(v) if v else 0.0 for k, v in match.groupdict().items()}
    return (
        parts["days"] * 86400
        + parts["hours"] * 3600
        + parts["minutes"] * 60
        + parts["seconds"]
    ) or None


def _first_str(*values: Any) -> str | None:
    for v in values:
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, list):
            for x in v:
                if isinstance(x, str) and x.strip():
                    return x.strip()
    return None


def _extract_ld_json(html: str) -> dict[str, Any] | None:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("@type") in {"MusicAlbum", "MusicRecording"}:
                    return item
            if data:
                return data[0] if isinstance(data[0], dict) else None
        if isinstance(data, dict):
            return data
    return None


def _extract_tralbum_data(html: str) -> dict[str, Any] | None:
    """Pull the inline ``TralbumData`` variable used by Bandcamp's player."""
    soup = BeautifulSoup(html, "html.parser")
    node = soup.find(attrs={"data-tralbum": True})
    if node:
        try:
            return json.loads(str(node["data-tralbum"]))
        except (json.JSONDecodeError, TypeError):
            pass

    for script in soup.find_all("script"):
        text = script.string or ""
        match = re.search(r"var TralbumData\s*=\s*(\{.*?\});", text, re.DOTALL)
        if match:
            # The inline JS object isn't strict JSON; do a best-effort cleanup.
            raw = match.group(1)
            raw = re.sub(r",\s*([}\]])", r"\1", raw)
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return None
    return None


def parse_album_page(html: str, album_url: str, fallback_id: str | None = None) -> BandcampAlbum:
    ld = _extract_ld_json(html) or {}
    tralbum = _extract_tralbum_data(html) or {}

    title = _first_str(ld.get("name"), tralbum.get("current", {}).get("title")) or "Untitled"

    bandcamp_id = fallback_id
    album_release = tralbum.get("current", {})
    if album_release.get("id"):
        bandcamp_id = str(album_release["id"])
    elif ld.get("@id"):
        bandcamp_id = str(ld["@id"]).rstrip("/").split("/")[-1] or bandcamp_id

    release_date = _first_str(ld.get("datePublished"), album_release.get("release_date"))
    description = _first_str(ld.get("description"), album_release.get("about"))

    tags: list[str] = []
    keywords = ld.get("keywords")
    if isinstance(keywords, list):
        tags = [str(k).strip() for k in keywords if str(k).strip()]
    elif isinstance(keywords, str):
        tags = [t.strip() for t in keywords.split(",") if t.strip()]
    if not tags:
        soup = BeautifulSoup(html, "html.parser")
        tags = [
            a.get_text(strip=True)
            for a in soup.select("a.tag")
            if a.get_text(strip=True)
        ]

    image = ld.get("image")
    if isinstance(image, list):
        cover_art_url = next((str(i) for i in image if isinstance(i, str)), None)
    elif isinstance(image, str):
        cover_art_url = image
    else:
        cover_art_url = None

    tracks = _parse_tracks(ld, tralbum, album_url)

    return BandcampAlbum(
        title=title,
        album_url=album_url,
        bandcamp_id=bandcamp_id,
        release_date=release_date,
        description=description,
        tags=tags,
        cover_art_url=cover_art_url,
        tracks=tracks,
    )


def _parse_tracks(
    ld: dict[str, Any],
    tralbum: dict[str, Any],
    album_url: str,
) -> list[BandcampTrack]:
    tracks: list[BandcampTrack] = []

    track_section = ld.get("track") or {}
    items = []
    if isinstance(track_section, dict):
        items = track_section.get("itemListElement") or []

    for entry in items:
        if not isinstance(entry, dict):
            continue
        position = entry.get("position") or len(tracks) + 1
        item = entry.get("item") or {}
        title = _first_str(item.get("name")) or "Untitled track"
        duration = _parse_iso_duration(_first_str(item.get("duration")))
        track_url = _first_str(item.get("@id"), item.get("url"))
        if track_url and track_url.startswith("/"):
            track_url = urljoin(album_url, track_url)
        tracks.append(
            BandcampTrack(
                title=title,
                track_number=int(position),
                duration_seconds=duration,
                bandcamp_track_url=track_url,
            )
        )

    # Enrich with streaming URLs and durations from TralbumData if present.
    tralbum_tracks = tralbum.get("trackinfo") or []
    for idx, info in enumerate(tralbum_tracks):
        title = _first_str(info.get("title"))
        duration = info.get("duration")
        streaming = None
        files = info.get("file") or {}
        if isinstance(files, dict):
            streaming = files.get("mp3-128") or next(iter(files.values()), None)

        if idx < len(tracks):
            track = tracks[idx]
            if streaming:
                track.streaming_url = streaming
            if not track.duration_seconds and duration:
                track.duration_seconds = float(duration)
            if title and track.title == "Untitled track":
                track.title = title
        else:
            tracks.append(
                BandcampTrack(
                    title=title or f"Track {idx + 1}",
                    track_number=idx + 1,
                    duration_seconds=float(duration) if duration else None,
                    streaming_url=streaming,
                )
            )

    return tracks


# ── DB persistence ───────────────────────────────────────────────────────

def _find_existing_project(conn: sqlite3.Connection, album: BandcampAlbum) -> int | None:
    if album.bandcamp_id:
        row = conn.execute(
            "SELECT id FROM projects WHERE bandcamp_id = ?", (album.bandcamp_id,)
        ).fetchone()
        if row:
            return int(row["id"])
    row = conn.execute(
        "SELECT id FROM projects WHERE bandcamp_url = ?", (album.album_url,)
    ).fetchone()
    return int(row["id"]) if row else None


def _track_hash(album: BandcampAlbum, track: BandcampTrack) -> str:
    """Stable identifier for catalog tracks (no real file to hash)."""
    import hashlib

    key = (
        f"bandcamp:{album.bandcamp_id or album.album_url}:"
        f"{track.track_number}:{track.title}"
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def upsert_album(
    conn: sqlite3.Connection,
    album: BandcampAlbum,
    *,
    overwrite: bool = False,
) -> tuple[int, int, bool]:
    """Persist an album. Returns (project_id, tracks_written, was_new)."""

    existing_id = _find_existing_project(conn, album)
    if existing_id and not overwrite:
        return existing_id, 0, False

    project_type = "album" if len(album.tracks) > 1 else "single"
    payload = {
        "title": album.title,
        "type": project_type,
        "state": "RELEASED",
        "target_track_count": len(album.tracks),
        "target_release_date": album.release_date,
        "bandcamp_url": album.album_url,
        "bandcamp_id": album.bandcamp_id,
        "release_date": album.release_date,
        "bandcamp_tags": json.dumps(album.tags) if album.tags else None,
        "bandcamp_description": album.description,
        "cover_art_url": album.cover_art_url,
    }

    if existing_id:
        set_clause = ", ".join(f"{k} = ?" for k in payload)
        conn.execute(
            f"UPDATE projects SET {set_clause} WHERE id = ?",
            (*payload.values(), existing_id),
        )
        conn.execute("DELETE FROM tracks WHERE project_id = ?", (existing_id,))
        project_id = existing_id
    else:
        columns = ", ".join(payload.keys())
        placeholders = ", ".join(["?"] * len(payload))
        cursor = conn.execute(
            f"INSERT INTO projects ({columns}) VALUES ({placeholders})",
            tuple(payload.values()),
        )
        project_id = int(cursor.lastrowid or 0)

    for track in album.tracks:
        conn.execute(
            """
            INSERT INTO tracks (
                title, file_path, file_hash, file_size, duration_seconds,
                format, state, project_id, bandcamp_track_url, track_number,
                bandcamp_streaming_url
            ) VALUES (?, ?, ?, ?, ?, ?, 'RELEASED', ?, ?, ?, ?)
            """,
            (
                track.title,
                track.streaming_url or track.bandcamp_track_url or album.album_url,
                _track_hash(album, track),
                None,
                track.duration_seconds,
                "bandcamp-stream",
                project_id,
                track.bandcamp_track_url,
                track.track_number,
                track.streaming_url,
            ),
        )

    return project_id, len(album.tracks), existing_id is None


def log_import(
    conn: sqlite3.Connection,
    artist_url: str,
    album_count: int,
    track_count: int,
    notes: str | None = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO catalog_imports (source, artist_url, album_count, track_count, notes)
        VALUES ('bandcamp', ?, ?, ?, ?)
        """,
        (artist_url, album_count, track_count, notes),
    )
    return int(cursor.lastrowid or 0)


# ── High-level orchestration ─────────────────────────────────────────────

def import_album_from_url(
    conn: sqlite3.Connection,
    album_url: str,
    *,
    session: requests.Session | None = None,
    overwrite: bool = False,
    fallback_id: str | None = None,
) -> dict[str, Any]:
    session = session or build_session()
    html = fetch(session, album_url)
    album = parse_album_page(html, album_url, fallback_id=fallback_id)
    project_id, track_count, was_new = upsert_album(conn, album, overwrite=overwrite)
    conn.commit()
    return {
        "project_id": project_id,
        "album_title": album.title,
        "track_count": track_count,
        "was_new": was_new,
        "bandcamp_id": album.bandcamp_id,
    }


def import_artist(
    db_path: str,
    artist_url: str,
    *,
    overwrite: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    artist_root = normalize_artist_root(artist_url)
    music_page = f"{artist_root}/music"

    session = build_session()
    print(f"Fetching artist page: {music_page}")
    html = fetch(session, music_page)

    album_links = discover_album_urls(html, artist_root)
    if not album_links:
        # Some single-album artists land /music straight on the release page.
        album_links = [(music_page, None)]

    print(f"Found {len(album_links)} releases")

    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.row_factory = sqlite3.Row

    total_tracks = 0
    imported_albums: list[dict[str, Any]] = []
    try:
        for idx, (url, fallback_id) in enumerate(album_links, 1):
            print(f"[{idx}/{len(album_links)}] {url}")
            try:
                html = fetch(session, url)
                album = parse_album_page(html, url, fallback_id=fallback_id)
            except Exception as exc:  # noqa: BLE001
                print(f"  ! failed to parse: {exc}")
                continue

            if dry_run:
                print(
                    f"  + (dry run) {album.title} — {len(album.tracks)} tracks, "
                    f"{len(album.tags)} tags"
                )
                imported_albums.append(
                    {
                        "title": album.title,
                        "tracks": len(album.tracks),
                        "tags": album.tags,
                    }
                )
                total_tracks += len(album.tracks)
                continue

            project_id, track_count, was_new = upsert_album(
                conn, album, overwrite=overwrite
            )
            conn.commit()
            total_tracks += track_count
            status = "new" if was_new else ("updated" if overwrite else "skipped")
            print(
                f"  > {status}: project_id={project_id}, "
                f"title='{album.title}', tracks={track_count}"
            )
            imported_albums.append(
                {
                    "project_id": project_id,
                    "title": album.title,
                    "tracks": track_count,
                    "status": status,
                }
            )

        if not dry_run:
            log_import(
                conn,
                artist_url=artist_root,
                album_count=len(imported_albums),
                track_count=total_tracks,
                notes=f"overwrite={overwrite}",
            )
            conn.commit()
    finally:
        conn.close()

    summary = {
        "artist_url": artist_root,
        "albums_processed": len(imported_albums),
        "tracks_imported": total_tracks,
        "albums": imported_albums,
        "dry_run": dry_run,
    }
    print("\n=== Import summary ===")
    print(json.dumps(summary, indent=2))
    return summary


# ── CLI ──────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import a Bandcamp catalog.")
    parser.add_argument("artist_url", help="Artist root URL, e.g. https://artist.bandcamp.com")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-import albums that already exist in the catalog.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scrape and print without writing to the database.",
    )
    parser.add_argument(
        "--db",
        default=resolve_db_path(),
        help="Override the SQLite database path.",
    )
    args = parser.parse_args(argv)

    import_artist(
        db_path=args.db,
        artist_url=args.artist_url,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
