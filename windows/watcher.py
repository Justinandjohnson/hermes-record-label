#!/usr/bin/env python3
"""
AI Record Label - Windows File Watcher
Watches a local folder for new/modified audio files and sends events
to the Mac's HTTP API via a Cloudflare tunnel.
"""

import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

# ── Configuration ─────────────────────────────────────────────────────────────

AUDIO_EXTENSIONS = {".wav", ".aif", ".aiff", ".flac", ".mp3"}
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5

# ── Logging ───────────────────────────────────────────────────────────────────


def setup_logging() -> logging.Logger:
    """Set up a logger that writes timestamped lines to stdout."""
    logger = logging.getLogger("watcher")
    logger.setLevel(logging.DEBUG)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        fmt="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


log = setup_logging()

# ── Config loading ─────────────────────────────────────────────────────────────


def load_config() -> dict:
    """
    Load config.json from the same directory as this script.
    Exits with a clear error message if the file is missing or malformed.
    """
    script_dir = Path(__file__).parent.resolve()
    config_path = script_dir / "config.json"

    if not config_path.exists():
        print(
            "\n[ERROR] config.json not found.\n"
            f"  Expected: {config_path}\n"
            "  Copy config.example.json to config.json and fill in your values.\n"
        )
        sys.exit(1)

    try:
        with config_path.open(encoding="utf-8") as f:
            config = json.load(f)
    except json.JSONDecodeError as exc:
        print(f"\n[ERROR] config.json is not valid JSON:\n  {exc}\n")
        sys.exit(1)

    required_keys = ["remote_url", "api_token", "watch_folder"]
    missing = [k for k in required_keys if not config.get(k)]
    if missing:
        print(
            f"\n[ERROR] config.json is missing required keys: {', '.join(missing)}\n"
            "  Check config.example.json for the expected format.\n"
        )
        sys.exit(1)

    # Normalise URL — strip trailing slash so paths compose cleanly
    config["remote_url"] = config["remote_url"].rstrip("/")

    return config


# ── HTTP sender ────────────────────────────────────────────────────────────────


def send_event(config: dict, file_path: str, version: int = 1) -> bool:
    """
    POST a new_track_detected event to the remote API.
    Retries up to MAX_RETRIES times with RETRY_BACKOFF_SECONDS between attempts.
    Returns True on success, False on permanent failure.
    """
    title = Path(file_path).stem
    url = f"{config['remote_url']}/event"
    headers = {
        "Authorization": f"Bearer {config['api_token']}",
        "Content-Type": "application/json",
    }
    payload = {
        "event": "new_track_detected",
        "payload": {
            "file_path": file_path,
            "title": title,
            "version": version,
        },
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log.info(
                "Sending event (attempt %d/%d): %s → %s",
                attempt,
                MAX_RETRIES,
                title,
                url,
            )
            response = requests.post(url, json=payload, headers=headers, timeout=15)
            response.raise_for_status()
            log.info(
                "Event accepted  [HTTP %d]  title=%r  file=%s",
                response.status_code,
                title,
                file_path,
            )
            return True

        except requests.exceptions.ConnectionError as exc:
            log.warning("Connection error (attempt %d/%d): %s", attempt, MAX_RETRIES, exc)
        except requests.exceptions.Timeout:
            log.warning("Request timed out (attempt %d/%d)", attempt, MAX_RETRIES)
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            log.warning(
                "HTTP error %s (attempt %d/%d): %s", status, attempt, MAX_RETRIES, exc
            )
            # 4xx errors (bad token, bad payload) won't improve with retries
            if exc.response is not None and 400 <= exc.response.status_code < 500:
                log.error(
                    "Giving up — server rejected the request (HTTP %d). "
                    "Check your api_token and remote_url.",
                    exc.response.status_code,
                )
                return False
        except requests.exceptions.RequestException as exc:
            log.warning("Unexpected request error (attempt %d/%d): %s", attempt, MAX_RETRIES, exc)

        if attempt < MAX_RETRIES:
            log.info("Retrying in %d seconds …", RETRY_BACKOFF_SECONDS)
            time.sleep(RETRY_BACKOFF_SECONDS)

    log.error(
        "Failed to deliver event after %d attempts: %s", MAX_RETRIES, file_path
    )
    return False


# ── File-system event handler ──────────────────────────────────────────────────


class AudioEventHandler(FileSystemEventHandler):
    """Handles filesystem events and filters down to audio files."""

    def __init__(self, config: dict) -> None:
        super().__init__()
        self.config = config
        # Track files we've already processed so rapid duplicate events
        # (e.g. Ableton writing in multiple passes) don't flood the API.
        self._seen: dict[str, float] = {}
        self._debounce_seconds = 3.0

    def _is_audio_file(self, path: str) -> bool:
        return Path(path).suffix.lower() in AUDIO_EXTENSIONS

    def _is_debounced(self, path: str) -> bool:
        """Return True if we already handled this file recently."""
        last = self._seen.get(path)
        if last is None:
            return False
        return (time.monotonic() - last) < self._debounce_seconds

    def _record(self, path: str) -> None:
        self._seen[path] = time.monotonic()

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        src = event.src_path
        if not self._is_audio_file(src):
            return
        if self._is_debounced(src):
            log.debug("Debounced (created): %s", src)
            return
        self._record(src)
        log.info("New audio file detected: %s", src)
        send_event(self.config, src, version=1)

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        src = event.src_path
        if not self._is_audio_file(src):
            return
        if self._is_debounced(src):
            log.debug("Debounced (modified): %s", src)
            return
        self._record(src)
        log.info("Audio file modified: %s", src)
        send_event(self.config, src, version=1)


# ── Main ───────────────────────────────────────────────────────────────────────


def main() -> None:
    config = load_config()
    watch_folder = config["watch_folder"]

    if not os.path.isdir(watch_folder):
        log.error(
            "watch_folder does not exist or is not a directory: %s\n"
            "  Update the watch_folder value in config.json.",
            watch_folder,
        )
        sys.exit(1)

    print()
    print("=" * 60)
    print("  AI Record Label — File Watcher")
    print("=" * 60)
    print(f"  Watching : {watch_folder}")
    print(f"  API URL  : {config['remote_url']}")
    print(f"  Token    : {config['api_token'][:8]}{'*' * (len(config['api_token']) - 8)}")
    print(f"  Started  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    print("  Press Ctrl+C to stop.")
    print()

    event_handler = AudioEventHandler(config)
    observer = Observer()
    observer.schedule(event_handler, watch_folder, recursive=True)
    observer.start()
    log.info("Observer started — watching for audio files …")

    try:
        while observer.is_alive():
            observer.join(timeout=1)
    except KeyboardInterrupt:
        log.info("Shutdown requested (Ctrl+C).")
    finally:
        observer.stop()
        observer.join()
        log.info("Watcher stopped. Goodbye.")


if __name__ == "__main__":
    main()
