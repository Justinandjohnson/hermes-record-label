"""Main file-watcher loop using watchdog.

Watches a configurable directory for new audio files, debounces rapid
events, validates files, registers tracks, and emits Hermes events.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from watchdog.events import FileCreatedEvent, FileMovedEvent, FileSystemEvent, FileSystemEventHandler

from file_watcher.track_registry import (
    RegistrationResult,
    compute_file_hash,
    ensure_schema,
    register_track,
)
from file_watcher.validator import validate_audio_file

logger = logging.getLogger(__name__)

# How long to wait after the last event for a given file before processing.
# DAWs write audio incrementally, so we wait for the dust to settle.
SETTLE_DELAY_SECONDS: float = 2.0

# Minimum interval between processing the same file path again.
DEBOUNCE_WINDOW_SECONDS: float = 5.0

# Cloud-synced folders do not always emit reliable filesystem events on macOS.
# The watcher periodically scans the canonical inbox and feeds discovered audio
# files through the same validation/registration path as event-driven files.
SCAN_INTERVAL_SECONDS: float = 2.0
SCAN_TIMEOUT_SECONDS: float = 5.0
_AUDIO_SUFFIXES = {".wav", ".flac", ".mp3", ".aiff", ".aif", ".ogg", ".m4a"}


# ---------------------------------------------------------------------------
# Hermes event emitter (pluggable)
# ---------------------------------------------------------------------------

EventEmitter = Callable[[str, dict[str, Any]], None]


def _default_emitter(event_name: str, payload: dict[str, Any]) -> None:
    """Default event emitter -- just logs.  Replaced by Hermes integration."""
    logger.info("Hermes event: %s  payload=%s", event_name, payload)


# ---------------------------------------------------------------------------
# Event handler
# ---------------------------------------------------------------------------


class _AudioFileHandler(FileSystemEventHandler):
    """Watchdog handler that debounces events and queues files for processing."""

    def __init__(self) -> None:
        super().__init__()
        # Maps file path → timestamp of the last raw event we received.
        self._pending: dict[str, float] = {}
        self._lock = threading.Lock()

    def on_created(self, event: FileSystemEvent) -> None:
        if not isinstance(event, FileCreatedEvent) or event.is_directory:
            return
        # src_path is bytes | str depending on watchdog version; normalise to str
        self._record_event(os.fsdecode(event.src_path))

    def on_moved(self, event: FileSystemEvent) -> None:
        """Catch files moved *into* the watched directory."""
        if not isinstance(event, FileMovedEvent) or event.is_directory:
            return
        self._record_event(os.fsdecode(event.dest_path))

    def _record_event(self, path: str) -> None:
        if Path(path).suffix.lower() not in _AUDIO_SUFFIXES:
            return
        with self._lock:
            self._pending[path] = time.monotonic()

    def drain_settled(self, now: float | None = None) -> list[str]:
        """Return paths whose last event is older than SETTLE_DELAY_SECONDS.

        Removes them from the pending map so they won't be returned again.
        """
        if now is None:
            now = time.monotonic()
        settled: list[str] = []
        with self._lock:
            for path, ts in list(self._pending.items()):
                if now - ts >= SETTLE_DELAY_SECONDS:
                    settled.append(path)
            for path in settled:
                del self._pending[path]
        return settled

    def has_pending(self, path: str) -> bool:
        with self._lock:
            return path in self._pending


# ---------------------------------------------------------------------------
# Watcher service
# ---------------------------------------------------------------------------


class FileWatcherService:
    """Watches a directory for new audio files and processes them.

    Usage::

        svc = FileWatcherService(watch_dir="/path/to/daw/exports", db_path="label.db")
        svc.start()      # non-blocking -- spawns background threads
        ...
        svc.stop()        # clean shutdown
    """

    def __init__(
        self,
        watch_dir: str | Path,
        db_path: str | Path = "label.db",
        emit: EventEmitter | None = None,
        poll_interval: float = 0.5,
        scan_interval: float = SCAN_INTERVAL_SECONDS,
        sync_destinations: list[Path] | None = None,
        b2_sync_script: Path | None = None,
    ) -> None:
        self.watch_dir = Path(watch_dir)
        self.db_path = Path(db_path)
        self._emit = emit or _default_emitter
        self._poll_interval = poll_interval
        self._scan_interval = scan_interval
        # Folders to mirror new files into (e.g. Google Drive inbox, B2 staging)
        self._sync_destinations: list[Path] = sync_destinations or []
        # Optional script to run for B2 sync after a file is copied
        self._b2_sync_script: Path | None = b2_sync_script

        # File intake is scan-based because macOS CloudStorage folders can hang
        # or miss native file events under launchd.
        self._observer: Any = None
        self._handler = _AudioFileHandler()
        self._stop_event = threading.Event()
        self._processor_thread: threading.Thread | None = None

        # Track last-processed times for debouncing.
        self._last_processed: dict[str, float] = {}
        self._last_scan: float = 0.0
        self._observed_files: dict[str, tuple[int, int, float]] = {}
        self._processed_signatures: dict[str, tuple[int, int]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start watching the directory (non-blocking)."""
        if not self.watch_dir.is_dir():
            raise FileNotFoundError(f"Watch directory does not exist: {self.watch_dir}")

        # Ensure DB schema.
        conn = self._connect_db()
        ensure_schema(conn)
        conn.close()

        self._stop_event.clear()

        self._processor_thread = threading.Thread(
            target=self._processing_loop,
            name="file-watcher-processor",
            daemon=True,
        )
        self._processor_thread.start()

        logger.info("File watcher started on %s", self.watch_dir)

    def stop(self, timeout: float = 5.0) -> None:
        """Cleanly stop the watcher and processing thread."""
        self._stop_event.set()

        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=timeout)
            self._observer = None

        if self._processor_thread is not None:
            self._processor_thread.join(timeout=timeout)
            self._processor_thread = None

        logger.info("File watcher stopped")

    @property
    def is_running(self) -> bool:
        return (
            self._processor_thread is not None
            and self._processor_thread.is_alive()
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _connect_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=60.0)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _processing_loop(self) -> None:
        """Background loop that polls for settled files and processes them."""
        conn = self._connect_db()
        try:
            while not self._stop_event.is_set():
                try:
                    settled = self._handler.drain_settled()
                    now = time.monotonic()
                    if now - self._last_scan >= self._scan_interval:
                        settled.extend(self._scan_audio_files(now))
                        self._last_scan = now
                    for path in settled:
                        # Debounce: skip if we processed this path very recently.
                        last = self._last_processed.get(path, 0.0)
                        if now - last < DEBOUNCE_WINDOW_SECONDS:
                            logger.debug("Debounce skip: %s", path)
                            continue
                        if not self._is_stably_settled(path, now):
                            logger.debug("Waiting for stable file state: %s", path)
                            self._handler._record_event(path)
                            continue
                        self._process_file(path, conn)
                        self._last_processed[path] = time.monotonic()
                        self._mark_processed_signature(path)
                    self._stop_event.wait(self._poll_interval)
                except Exception:
                    logger.exception("File watcher processing loop failed")
                    self._stop_event.wait(self._poll_interval)
        finally:
            conn.close()

    def _scan_audio_files(self, now: float) -> list[str]:
        """Return settled audio files present in the watch directory."""
        paths: list[str] = []
        try:
            scan = subprocess.run(
                ["/usr/bin/find", str(self.watch_dir), "-type", "f", "-print"],
                check=False,
                capture_output=True,
                text=True,
                timeout=SCAN_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            logger.exception("Timed out scanning watch directory %s", self.watch_dir)
            return paths
        except OSError:
            logger.exception("Failed to scan watch directory %s", self.watch_dir)
            return paths

        if scan.returncode != 0:
            logger.error(
                "Scan command failed for %s: %s",
                self.watch_dir,
                scan.stderr.strip(),
            )
            return paths

        candidates = [Path(line) for line in scan.stdout.splitlines() if line]
        for path in candidates:
            if not path.is_file():
                continue
            if path.name.startswith(".") or path.suffix.lower() not in _AUDIO_SUFFIXES:
                continue
            str_path = str(path)
            if self._handler.has_pending(str_path):
                continue
            last = self._last_processed.get(str_path, 0.0)
            if now - last < DEBOUNCE_WINDOW_SECONDS:
                continue
            if not self._is_stably_settled(str_path, now):
                continue
            paths.append(str_path)
        return paths

    def _is_stably_settled(self, path: str, now: float) -> bool:
        """Return True after a file's size and mtime are unchanged across checks."""
        file_path = Path(path)
        try:
            stat = file_path.stat()
        except OSError:
            self._observed_files.pop(path, None)
            return False

        signature = (stat.st_size, stat.st_mtime_ns)
        if self._processed_signatures.get(path) == signature:
            return False

        observed = self._observed_files.get(path)
        if observed is None or observed[:2] != signature:
            self._observed_files[path] = (signature[0], signature[1], now)
            return False

        first_seen = observed[2]
        if now - first_seen < SETTLE_DELAY_SECONDS:
            return False
        return True

    def _mark_processed_signature(self, path: str) -> None:
        try:
            stat = Path(path).stat()
        except OSError:
            self._processed_signatures.pop(path, None)
            return
        self._processed_signatures[path] = (stat.st_size, stat.st_mtime_ns)

    def _process_file(self, path: str, conn: sqlite3.Connection) -> None:
        """Validate, register, and emit an event for a single file."""
        logger.debug("Processing %s", path)

        # 1. Validate
        result = validate_audio_file(path)
        if not result.is_valid:
            logger.warning("Rejected %s: %s", Path(path).name, result.rejection_reason)
            return

        # 2. Register
        try:
            reg: RegistrationResult = register_track(
                conn,
                file_path=path,
                fmt=result.format,
                file_size=result.file_size,
            )
        except Exception:
            logger.exception("Failed to register track for %s", path)
            return

        if reg.duplicate:
            logger.debug("Duplicate hash for %s -- skipping event", Path(path).name)
            return

        if not reg.registered or reg.track_id is None:
            return

        src = Path(path)

        # 3. Emit Hermes event
        self._emit(
            "new_track_detected",
            {
                "track_id": reg.track_id,
                "title": reg.track.title if reg.track else None,
                "file_path": path,
                "version": reg.track.version if reg.track else 1,
                "parent_track_id": reg.track.parent_track_id if reg.track else None,
            },
        )

        self._refresh_registered_signature(conn, reg.track_id, src)

        # 4. Mirror to sync destinations (Google Drive, B2 staging, etc.)
        for dest_dir in self._sync_destinations:
            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / src.name
                if not dest.exists():
                    shutil.copy2(src, dest)
                    logger.info("Copied to %s: %s", dest_dir.name, src.name)
                else:
                    logger.debug("Already in %s: %s", dest_dir.name, src.name)
            except Exception:
                logger.exception("Failed to copy %s → %s", src.name, dest_dir)

        # 5. Trigger B2 sync in background (non-blocking)
        if self._b2_sync_script and self._b2_sync_script.exists():
            try:
                subprocess.Popen(
                    ["bash", str(self._b2_sync_script)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                logger.info("B2 sync triggered for %s", src.name)
            except Exception:
                logger.exception("Failed to launch B2 sync script")

    def _refresh_registered_signature(
        self, conn: sqlite3.Connection, track_id: int, path: Path
    ) -> None:
        """Persist final file size/hash after metadata writers mutate the audio file."""
        try:
            stat = path.stat()
            file_hash = compute_file_hash(path)
            conn.execute(
                """
                UPDATE tracks
                SET file_hash = ?, file_size = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (file_hash, stat.st_size, track_id),
            )
            conn.commit()
            self._observed_files[str(path)] = (
                stat.st_size,
                stat.st_mtime_ns,
                time.monotonic(),
            )
            self._processed_signatures[str(path)] = (stat.st_size, stat.st_mtime_ns)
        except Exception:
            logger.exception("Failed to refresh stored signature for track #%s", track_id)


# ---------------------------------------------------------------------------
# Convenience: run as a standalone script
# ---------------------------------------------------------------------------


def run(
    watch_dir: str,
    db_path: str = "label.db",
    emit: EventEmitter | None = None,
) -> None:
    """Block the calling thread, watching *watch_dir* until interrupted."""
    svc = FileWatcherService(watch_dir=watch_dir, db_path=db_path, emit=emit)
    svc.start()
    try:
        while svc.is_running:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        svc.stop()


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")

    # Positional args: watch_dir [db_path] [project_folder]
    # Named args:      --sync-dest <dir>  (repeatable)
    #                  --b2-sync-script <path>
    _args = sys.argv[1:]
    _positional: list[str] = []
    _sync_dests: list[Path] = []
    _b2_script: Path | None = None

    i = 0
    while i < len(_args):
        if _args[i] == "--sync-dest" and i + 1 < len(_args):
            _sync_dests.append(Path(_args[i + 1]))
            i += 2
        elif _args[i] == "--b2-sync-script" and i + 1 < len(_args):
            _b2_script = Path(_args[i + 1])
            i += 2
        else:
            _positional.append(_args[i])
            i += 1

    directory = _positional[0] if len(_positional) > 0 else "."
    _db = _positional[1] if len(_positional) > 1 else "label.db"
    _project = _positional[2] if len(_positional) > 2 else None

    _emitters: list[EventEmitter] = []
    try:
        from session_intelligence.watcher_integration import SessionIntelligenceEmitter  # type: ignore[import-untyped]
        _emitter = SessionIntelligenceEmitter(db_path=_db, project_folder=_project)
        if _project:
            logger.info("Backfilling session history from %s", _project)
            _emitter.scan_project_folder(_project)
        _emitters.append(_emitter)
        logger.info("Session intelligence active")
    except ImportError:
        logger.warning("session_intelligence not available; using default emitter")

    try:
        from coordination.dispatcher import TrackPipelineDispatcher
        _emitters.append(TrackPipelineDispatcher(db_path=_db))
        logger.info("Track coordination pipeline active")
    except ImportError:
        logger.warning("coordination dispatcher not available; track pipeline disabled")

    def _emit_all(event: str, payload: dict[str, Any]) -> None:
        for emitter in _emitters:
            try:
                emitter(event, payload)
            except Exception:
                logger.exception("Emitter failed for event=%s payload=%s", event, payload)

    svc = FileWatcherService(
        watch_dir=directory,
        db_path=_db,
        emit=_emit_all if _emitters else None,
        sync_destinations=_sync_dests if _sync_dests else None,
        b2_sync_script=_b2_script,
    )
    svc.start()
    try:
        while svc.is_running:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        svc.stop()
