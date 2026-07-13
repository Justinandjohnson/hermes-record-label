#!/usr/bin/env python3
"""
import_local_catalog.py — Load the personal-music-model catalog into hermes.db.

Reads:
  data/manifests/source_inventory.csv  → one row per unique source MP3
  data/manifests/metadata.csv          → one row per 30s clip, with AI text descriptions

Writes into hermes.db:
  projects  — one per album folder
  tracks    — one per unique source MP3
  audio_analyses — one per track, aggregated from clip-level text tags
  catalog_imports — audit row
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

MODEL_ROOT = Path.home() / "Downloads/personal-music-model"
INVENTORY  = MODEL_ROOT / "data/manifests/source_inventory.csv"
METADATA   = MODEL_ROOT / "data/manifests/metadata.csv"

DB_PATH = Path(os.environ.get(
    "DB_PATH",
    Path.home() / "Library/Application Support/ai-record-label/hermes.db"
))

# Map folder names → Bandcamp slugs (from earlier scrape)
BANDCAMP_URL_MAP: dict[str, str] = {
    "2018":             "https://just-inn-case.bandcamp.com/album/epoch",
    "2020 was shit":    "https://just-inn-case.bandcamp.com/album/2020-was-shit",
    "818 Spring St":    "https://just-inn-case.bandcamp.com/album/818-spring-st",
    "Arcade":           "https://just-inn-case.bandcamp.com/album/arcade",
    "Compósito":        "https://just-inn-case.bandcamp.com/album/comp-sito",
    "IAMI":             "https://just-inn-case.bandcamp.com/album/iami-3",
    "IAMI (2)":         "https://just-inn-case.bandcamp.com/album/iami-3",
    "Jump":             "https://just-inn-case.bandcamp.com/album/jump",
    "Late Night Drive": "https://just-inn-case.bandcamp.com/album/late-night-drive",
    "Singles":          "https://just-inn-case.bandcamp.com/music",
    "Soundscapes Vol 1":"https://just-inn-case.bandcamp.com/album/soundscapes-vol-1",
    "The Voisey Series":"https://just-inn-case.bandcamp.com/album/the-voisey-series",
    "THe Voisey Series (2)": "https://just-inn-case.bandcamp.com/album/the-voisey-series",
    "Week 1":           "https://just-inn-case.bandcamp.com/album/where-did-my-friends-go-2",
    "Week 2":           "https://just-inn-case.bandcamp.com/album/where-did-my-friends-go-2",
    "Week 3":           "https://just-inn-case.bandcamp.com/album/where-did-my-friends-go-2",
    "Week 4":           "https://just-inn-case.bandcamp.com/album/where-did-my-friends-go-2",
    "Where Did My Friends Go": "https://just-inn-case.bandcamp.com/album/where-did-my-friends-go-2",
    "The Artists I made this with thought these were terrible songs they wrong":
                        "https://just-inn-case.bandcamp.com/music",
}

RELEASE_YEAR_MAP: dict[str, str] = {
    "2018":             "2016",
    "2020 was shit":    "2020",
    "818 Spring St":    "2019",
    "Arcade":           "2019",
    "Compósito":        "2022",
    "Jump":             "2017",
    "Late Night Drive": "2023",
    "Soundscapes Vol 1":"2026",
    "The Voisey Series":"2025",
    "Where Did My Friends Go": "2021",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def fake_hash(album: str, track_name: str) -> str:
    """Deterministic hash for tracks where the file is missing."""
    return hashlib.sha256(f"catalog:{album}:{track_name}".encode()).hexdigest()


def parse_track_number(filename: str) -> int | None:
    m = re.match(r"^(\d+)", Path(filename).stem)
    return int(m.group(1)) if m else None


def aggregate_text_tags(texts: list[str]) -> dict:
    """Collapse many 30s clip descriptions into per-track aggregate tags."""
    moods, tempos, keys, textures, genres = [], [], [], [], []
    for t in texts:
        parts = [p.strip() for p in t.split(",")]
        for p in parts:
            if "mood" in p:
                moods.append(p)
            elif "tempo" in p or "tempo" in p:
                tempos.append(p)
            elif " minor" in p or " major" in p:
                keys.append(p)
            elif "texture" in p:
                textures.append(p)
            elif p in ("jazz trumpet improvisation", "swing feel", "bebop phrasing",
                       "minor blues", "Experimental", "Hip Hop", "Hip-Hop", "abstract"):
                genres.append(p)

    def mode(lst: list[str]) -> str | None:
        if not lst:
            return None
        from collections import Counter
        return Counter(lst).most_common(1)[0][0]

    return {
        "mood": mode(moods),
        "tempo": mode(tempos),
        "key": mode(keys),
        "texture": mode(textures),
        "genre_tags": list(dict.fromkeys(genres))[:6],
        "description": texts[0] if texts else None,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    dry_run = "--dry-run" in sys.argv

    # 1. Load source inventory
    print(f"Reading {INVENTORY}")
    inventory: list[dict] = []
    with open(INVENTORY) as f:
        for row in csv.DictReader(f):
            if row["is_canonical"] == "1":
                inventory.append(row)
    print(f"  {len(inventory)} canonical source files")

    # Group by album (parent folder)
    by_album: dict[str, list[dict]] = defaultdict(list)
    for row in inventory:
        album_name = Path(row["source_file"]).parent.name
        by_album[album_name].append(row)

    # 2. Load clip metadata for text descriptions
    print(f"Reading {METADATA}")
    clip_meta: dict[str, list[str]] = defaultdict(list)  # source_file → [text, ...]
    with open(METADATA) as f:
        for row in csv.DictReader(f):
            src = row.get("source_file", "")
            text = row.get("text", "").strip()
            if src and text:
                clip_meta[src].append(text)
    print(f"  {len(clip_meta)} source files have text descriptions")

    if dry_run:
        print("\n=== DRY RUN ===")
        for album, tracks in sorted(by_album.items()):
            print(f"  [{album}]  {len(tracks)} tracks")
        print(f"\nTotal albums: {len(by_album)}, tracks: {sum(len(v) for v in by_album.values())}")
        return

    # 3. Write to DB
    conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    albums_created = 0
    albums_skipped = 0
    tracks_created = 0
    analyses_created = 0

    for album_name, track_rows in sorted(by_album.items()):
        # Skip known junk folders
        if album_name.lower().startswith("zzz") or album_name == "ZZZ Combine Validation":
            continue

        # Check for existing project
        existing = conn.execute(
            "SELECT id FROM projects WHERE title = ?", (album_name,)
        ).fetchone()

        if existing:
            project_id = existing["id"]
            print(f"  SKIP  [{album_name}] (project #{project_id} exists)")
            albums_skipped += 1
        else:
            bc_url = BANDCAMP_URL_MAP.get(album_name)
            release_year = RELEASE_YEAR_MAP.get(album_name)
            cur = conn.execute(
                """INSERT INTO projects (title, type, state, bandcamp_url, release_date, created_at)
                   VALUES (?, 'album', 'RELEASED', ?, ?, datetime('now'))""",
                (album_name, bc_url, release_year),
            )
            project_id = cur.lastrowid
            print(f"  CREATE [{album_name}] → project #{project_id}  ({len(track_rows)} tracks)")
            albums_created += 1

        for track_row in sorted(track_rows, key=lambda r: r["source_file"]):
            src_path = Path(track_row["source_file"])
            track_name = src_path.stem
            file_exists = src_path.exists()
            track_num = parse_track_number(src_path.name)

            if file_exists:
                file_hash = sha256_of(src_path)
                file_size = src_path.stat().st_size
            else:
                file_hash = fake_hash(album_name, track_name)
                file_size = None

            # Skip if track already exists
            dup = conn.execute(
                "SELECT id FROM tracks WHERE file_hash = ?", (file_hash,)
            ).fetchone()
            if dup:
                continue

            cur2 = conn.execute(
                """INSERT INTO tracks
                   (title, file_path, file_hash, file_size, format, state, project_id, track_number,
                    created_at)
                   VALUES (?, ?, ?, ?, 'mp3', 'RELEASED', ?, ?, datetime('now'))""",
                (track_name, str(src_path) if file_exists else None,
                 file_hash, file_size, project_id, track_num),
            )
            track_id = cur2.lastrowid
            tracks_created += 1

            # Audio analysis from clip metadata
            clip_texts = clip_meta.get(track_row["source_file"], [])
            if clip_texts:
                tags = aggregate_text_tags(clip_texts)
                conn.execute(
                    """INSERT OR IGNORE INTO audio_analyses
                       (track_id, mood_tags, musical_key, created_at)
                       VALUES (?, ?, ?, datetime('now'))""",
                    (track_id,
                     json.dumps(tags["genre_tags"] + ([tags["mood"]] if tags["mood"] else [])),
                     tags["key"]),
                )
                analyses_created += 1

        conn.commit()

    # Audit row
    conn.execute(
        """INSERT INTO catalog_imports (source, artist_url, album_count, track_count, notes)
           VALUES ('local-manifest', 'https://just-inn-case.bandcamp.com',
                   ?, ?, 'Imported from personal-music-model manifests')""",
        (albums_created, tracks_created),
    )
    conn.commit()
    conn.close()

    print(f"""
=== Import complete ===
  Albums created : {albums_created}
  Albums skipped : {albums_skipped}
  Tracks created : {tracks_created}
  Analyses added : {analyses_created}
""")


if __name__ == "__main__":
    main()
