#!/usr/bin/env python3
"""
intake_album.py — Drop an album folder into the AI Record Label system.

Usage:
    python scripts/intake_album.py /path/to/album/folder
    python scripts/intake_album.py /path/to/album/folder --title "Album Name" --year 2023
    python scripts/intake_album.py /path/to/album/folder --state DRAFT --no-copy
    python scripts/intake_album.py /path/to/album/folder --json   # machine-readable output

The script:
1. Finds all audio files in the folder (recursively)
2. Reads ID3/FLAC/MP4 metadata via mutagen (falls back to filename)
3. Creates or reuses a project row (the album) and track rows in the DB
4. Copies audio files into the configured inbox folder (so the pipeline sees them)
5. Prints a summary and the album's project_id for reference

Environment variables:
    AI_RECORD_LABEL_DATA  — path to data dir (default: ~/Library/Application Support/ai-record-label)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".aiff", ".aif", ".ogg", ".m4a"}

DATA_DIR = Path(os.environ.get(
    "AI_RECORD_LABEL_DATA",
    Path.home() / "Library/Application Support/ai-record-label",
))
DB_PATH = DATA_DIR / "hermes.db"
INBOX = DATA_DIR / "inbox"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def read_metadata(path: Path) -> dict:
    """Read ID3/FLAC/MP4 tags via mutagen. Falls back to filename parsing."""
    meta: dict = {"title": path.stem, "duration_seconds": None, "artist": None,
                  "album": None, "tracknumber": None, "date": None}
    try:
        from mutagen import File as MutagenFile  # type: ignore[import]
        audio = MutagenFile(path, easy=True)
        if audio:
            def _first(key: str) -> str | None:
                val = audio.get(key)
                return val[0] if val else None
            meta["title"] = _first("title") or path.stem
            meta["artist"] = _first("artist")
            meta["album"] = _first("album")
            meta["tracknumber"] = _first("tracknumber")
            meta["date"] = _first("date")
            if hasattr(audio, "info") and hasattr(audio.info, "length"):
                meta["duration_seconds"] = audio.info.length
    except ImportError:
        pass  # mutagen not installed — use filename fallback
    except Exception:
        pass
    return meta


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Intake an album folder into the AI Record Label system"
    )
    parser.add_argument("folder", help="Path to the album folder")
    parser.add_argument("--title", help="Album title (overrides ID3 tag)")
    parser.add_argument("--year", help="Release year (overrides ID3 tag)")
    parser.add_argument(
        "--state", default="DRAFT",
        choices=["DRAFT", "IN_REVIEW", "APPROVED"],
        help="Initial track state (default: DRAFT)"
    )
    parser.add_argument(
        "--type", default="album",
        choices=["single", "ep", "album"],
        dest="project_type",
        help="Project type (default: album)"
    )
    parser.add_argument(
        "--no-copy", action="store_true",
        help="Register files in place without copying to inbox"
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help=(
            "Output a single JSON object to stdout (all human-readable output goes to stderr). "
            "The JSON contains: project_id, album, tracks_added, skipped_duplicates, track_ids."
        ),
    )
    args = parser.parse_args()

    # When --json is active, human output must go to stderr so stdout is clean JSON.
    out = sys.stderr if args.json_output else sys.stdout

    folder = Path(args.folder).resolve()
    if not folder.is_dir():
        print(f"Error: {folder} is not a directory", file=sys.stderr)
        sys.exit(1)

    # Collect audio files (recursive)
    audio_files = sorted([
        p for p in folder.rglob("*")
        if p.suffix.lower() in AUDIO_EXTENSIONS and p.is_file()
    ])
    if not audio_files:
        print(f"No audio files found in {folder}", file=sys.stderr)
        print(f"Supported formats: {', '.join(sorted(AUDIO_EXTENSIONS))}", file=sys.stderr)
        sys.exit(1)

    print(f"\n🎵 AI Record Label — Intake", file=out)
    print(f"   Folder: {folder}", file=out)
    print(f"   Found:  {len(audio_files)} audio files\n", file=out)

    # Determine album metadata from first file + CLI args
    sample_meta = read_metadata(audio_files[0])
    album_title = args.title or sample_meta.get("album") or folder.name
    raw_date = sample_meta.get("date") or ""
    album_year = args.year or (raw_date[:4] if raw_date else "")

    conn = connect_db()

    # Reuse existing project with same title+type rather than always inserting.
    # Prevents duplicate projects when the same folder is dropped more than once.
    existing_project = conn.execute(
        "SELECT id FROM projects WHERE title = ? AND type = ? ORDER BY id DESC LIMIT 1",
        (album_title, args.project_type),
    ).fetchone()
    if existing_project:
        project_id = int(existing_project["id"])
        print(f"✓ Reusing project: '{album_title}' (id={project_id}, type={args.project_type})", file=out)
    else:
        cur = conn.execute(
            """INSERT INTO projects (title, type, state, target_track_count)
               VALUES (?, ?, 'active', ?)""",
            (album_title, args.project_type, len(audio_files)),
        )
        project_id = int(cur.lastrowid)  # type: ignore[arg-type]
        conn.commit()
        print(f"✓ Created project: '{album_title}' (id={project_id}, type={args.project_type})", file=out)
    if album_year:
        print(f"  Year: {album_year}", file=out)

    INBOX.mkdir(parents=True, exist_ok=True)
    track_ids: list[int] = []
    skipped_duplicates = 0

    for audio_file in audio_files:
        meta = read_metadata(audio_file)
        file_hash = sha256_of(audio_file)
        file_size = audio_file.stat().st_size

        # Duplicate check by hash
        existing_track = conn.execute(
            "SELECT id, title FROM tracks WHERE file_hash = ?", (file_hash,)
        ).fetchone()
        if existing_track:
            print(
                f"  SKIP (duplicate hash): {audio_file.name} → already track_id={existing_track['id']}",
                file=out,
            )
            skipped_duplicates += 1
            continue

        # Copy to inbox unless --no-copy
        dest_path = audio_file
        if not args.no_copy:
            dest = INBOX / audio_file.name
            if dest.exists():
                dest = INBOX / f"{file_hash[:8]}_{audio_file.name}"
            shutil.copy2(audio_file, dest)
            dest_path = dest

        cur = conn.execute(
            """INSERT INTO tracks
               (title, file_path, file_hash, file_size, duration_seconds, format,
                version, state, project_id)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (
                meta["title"],
                str(dest_path),
                file_hash,
                file_size,
                meta.get("duration_seconds"),
                audio_file.suffix.lstrip(".").lower(),
                args.state,
                project_id,
            ),
        )
        track_id = cur.lastrowid
        if track_id is None:
            raise RuntimeError(f"INSERT for {audio_file.name} returned no lastrowid")
        track_ids.append(int(track_id))
        conn.commit()

        duration_str = ""
        if meta.get("duration_seconds"):
            secs = int(meta["duration_seconds"])
            duration_str = f" ({secs//60}:{secs%60:02d})"
        print(f"  ✓  {audio_file.name}{duration_str} → track_id={track_id}", file=out)

    conn.close()

    print(f"\n{'─'*50}", file=out)
    print(f"✅  Intake complete!", file=out)
    print(f"    Album:    {album_title}", file=out)
    print(f"    Project:  id={project_id}", file=out)
    print(f"    Tracks:   {len(track_ids)} added" + (f", {skipped_duplicates} skipped (duplicate)" if skipped_duplicates else ""), file=out)
    if not args.no_copy:
        print(f"    Inbox:    {INBOX}", file=out)
    print(f"\n    State: {args.state}", file=out)
    if args.state == "DRAFT":
        print(f"    → A&R will review when the intake agent triggers.", file=out)
        print(f"    → Or run: hermes message a_and_r 'New intake: project_id={project_id}'", file=out)
    print(file=out)

    if args.json_output:
        # Only JSON to stdout — everything else went to stderr above.
        print(json.dumps({
            "project_id": project_id,
            "album": album_title,
            "tracks_added": len(track_ids),
            "skipped_duplicates": skipped_duplicates,
            "track_ids": track_ids,
        }))


if __name__ == "__main__":
    main()
