"""
Infrastructure / environment smoke tests.

These verify that all installed dependencies, binaries, and credentials
are present and configured correctly. Run these first when something
is broken to quickly locate the problem.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest


DATA_DIR = Path.home() / "Library" / "Application Support" / "ai-record-label"
HERMES_GOOGLE = Path.home() / ".hermes" / "google"
HERMES_ENV = Path.home() / ".hermes" / ".env"
LAUNCH_AGENT_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.ai-record-label.cloudflared.plist"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Binaries
# ---------------------------------------------------------------------------

class TestBinaries:
    def test_cloudflared_installed(self) -> None:
        assert shutil.which("cloudflared") or Path("/opt/homebrew/bin/cloudflared").exists(), \
            "cloudflared not found — run: brew install cloudflared"

    def test_fpcalc_installed(self) -> None:
        assert shutil.which("fpcalc") or Path("/opt/homebrew/bin/fpcalc").exists(), \
            "fpcalc not found — run: brew install chromaprint"

    def test_hermes_installed(self) -> None:
        assert shutil.which("hermes") or Path(Path.home() / ".local" / "bin" / "hermes").exists(), \
            "hermes not found in PATH"

    def test_mac_messages_mcp_installed(self) -> None:
        assert Path(Path.home() / ".local" / "bin" / "mac-messages-mcp").exists(), \
            "mac-messages-mcp not installed"

    def test_mcp_google_calendar_installed(self) -> None:
        assert Path("/opt/homebrew/bin/mcp-google-calendar").exists(), \
            "mcp-google-calendar not installed"

    def test_venv_python_exists(self) -> None:
        venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
        assert venv_python.exists(), f"venv not found at {venv_python}"


# ---------------------------------------------------------------------------
# Python packages
# ---------------------------------------------------------------------------

class TestPythonPackages:
    def test_mutagen_importable(self) -> None:
        import mutagen  # noqa: F401

    def test_acoustid_importable(self) -> None:
        import acoustid  # noqa: F401

    def test_watchdog_importable(self) -> None:
        import watchdog  # noqa: F401

    def test_google_api_client_importable(self) -> None:
        pytest.importorskip("googleapiclient", reason="google-api-python-client is an optional dev dependency for Calendar sync")

    def test_google_auth_importable(self) -> None:
        import google.oauth2.credentials  # noqa: F401


# ---------------------------------------------------------------------------
# Google Calendar OAuth
# ---------------------------------------------------------------------------

class TestGoogleCalendarAuth:
    def test_credentials_json_exists(self) -> None:
        creds = HERMES_GOOGLE / "credentials.json"
        assert creds.exists(), \
            f"credentials.json not found at {creds} — download from GCP Console"

    def test_credentials_json_is_valid_json(self) -> None:
        creds = HERMES_GOOGLE / "credentials.json"
        if not creds.exists():
            pytest.skip("credentials.json missing")
        data = json.loads(creds.read_text())
        installed = data.get("installed", data.get("web", {}))
        assert "client_id" in installed
        assert "client_secret" in installed

    def test_token_json_exists(self) -> None:
        token = HERMES_GOOGLE / "mcp-google-calendar-token.json"
        assert token.exists(), \
            f"Token not found at {token} — run mcp-google-calendar to authenticate"

    def test_token_has_refresh_token(self) -> None:
        token = HERMES_GOOGLE / "mcp-google-calendar-token.json"
        if not token.exists():
            pytest.skip("token missing")
        data = json.loads(token.read_text())
        assert data.get("refresh_token"), "refresh_token missing from token file"

    def test_calendar_service_builds(self) -> None:
        """Verify the service object can be constructed (no network call)."""
        from session_intelligence.calendar_sync import _build_service
        # This may return None if token is expired, but should not raise
        try:
            service = _build_service()
            # If it returns something, it should have an events() method
            if service is not None:
                assert hasattr(service, "events")
        except Exception as exc:
            pytest.fail(f"_build_service raised unexpectedly: {exc}")


# ---------------------------------------------------------------------------
# Perplexity
# ---------------------------------------------------------------------------

class TestPerplexity:
    def test_perplexity_key_in_env(self) -> None:
        if HERMES_ENV.exists():
            content = HERMES_ENV.read_text()
            assert "PERPLEXITY_API_KEY=pplx-" in content, \
                "PERPLEXITY_API_KEY not set in ~/.hermes/.env"
        else:
            pytest.skip(".env file not found")

    def test_perplexity_mcp_responds_to_tools_list(self) -> None:
        """Smoke test: perplexity-mcp returns tools/list without error."""
        env = os.environ.copy()
        if HERMES_ENV.exists():
            for line in HERMES_ENV.read_text().splitlines():
                if line.startswith("PERPLEXITY_API_KEY="):
                    env["PERPLEXITY_API_KEY"] = line.split("=", 1)[1]

        if "PERPLEXITY_API_KEY" not in env:
            pytest.skip("PERPLEXITY_API_KEY not set")

        msg = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        result = subprocess.run(
            ["npx", "--yes", "perplexity-mcp"],
            input=msg,
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        assert result.returncode == 0 or result.stdout
        data = json.loads(result.stdout)
        assert "result" in data
        assert len(data["result"]["tools"]) >= 2


# ---------------------------------------------------------------------------
# Cloudflare tunnel
# ---------------------------------------------------------------------------

class TestCloudflareTunnel:
    def test_launchagent_plist_exists(self) -> None:
        assert LAUNCH_AGENT_PLIST.exists(), \
            f"LaunchAgent plist not found at {LAUNCH_AGENT_PLIST}"

    def test_launchagent_plist_is_valid(self) -> None:
        if not LAUNCH_AGENT_PLIST.exists():
            pytest.skip("plist missing")
        result = subprocess.run(
            ["plutil", "-lint", str(LAUNCH_AGENT_PLIST)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"plist invalid: {result.stderr}"

    def test_cloudflared_wrapper_exists(self) -> None:
        wrapper = DATA_DIR / "cloudflared-wrapper.sh"
        assert wrapper.exists(), f"Wrapper script missing at {wrapper}"

    def test_url_file_exists_and_is_nonempty(self) -> None:
        url_file = DATA_DIR / ".cloudflared.url"
        assert url_file.exists(), "Tunnel URL file missing — is cloudflared running?"
        url = url_file.read_text().strip()
        assert url.startswith("https://"), f"Unexpected URL format: {url!r}"

    def test_tunnel_health_endpoint(self) -> None:
        """Live HTTP test — only runs if tunnel is active."""
        url_file = DATA_DIR / ".cloudflared.url"
        if not url_file.exists():
            pytest.skip("URL file missing")
        import urllib.request
        url = url_file.read_text().strip() + "/health"
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                body = resp.read().decode()
            assert body.strip() == "ok", f"Unexpected health response: {body!r}"
        except Exception as exc:
            pytest.fail(f"Tunnel health check failed: {exc}")


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

class TestDatabase:
    def test_db_exists(self) -> None:
        db = DATA_DIR / "hermes.db"
        assert db.exists(), f"Database not found at {db} — run launch.sh first"

    def test_all_migrations_applied(self) -> None:
        db = DATA_DIR / "hermes.db"
        if not db.exists():
            pytest.skip("DB missing")
        conn = sqlite3.connect(str(db))
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        required = {"tracks", "ableton_sessions", "export_events", "feedback"}
        missing = required - tables
        assert not missing, f"Missing DB tables: {missing}"

    def test_db_wal_mode_enabled(self) -> None:
        db = DATA_DIR / "hermes.db"
        if not db.exists():
            pytest.skip("DB missing")
        conn = sqlite3.connect(str(db))
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "wal", f"Expected WAL mode, got: {mode}"


# ---------------------------------------------------------------------------
# File watcher
# ---------------------------------------------------------------------------

class TestFileWatcher:
    def test_inbox_directory_exists(self) -> None:
        inbox = DATA_DIR / "inbox"
        assert inbox.exists(), f"Inbox not found at {inbox}"

    def test_watcher_pid_file_and_process(self) -> None:
        pid_file = DATA_DIR / ".watcher.pid"
        if not pid_file.exists():
            pytest.skip("Watcher not running (no pid file)")
        pid = int(pid_file.read_text().strip())
        # Check process is alive
        result = subprocess.run(["kill", "-0", str(pid)], capture_output=True)
        assert result.returncode == 0, f"Watcher PID {pid} is not running"
