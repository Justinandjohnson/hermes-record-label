"""
Tests for session_intelligence.calendar_sync.

All Google API calls are mocked — no real network traffic.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open
import json

import pytest

from session_intelligence.calendar_sync import create_export_event


# Minimal fake credentials.json and token.json content
_FAKE_CREDS = json.dumps({
    "installed": {
        "client_id": "fake-client-id.apps.googleusercontent.com",
        "client_secret": "fake-secret",
        "redirect_uris": ["http://localhost"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
})

_FAKE_TOKEN = json.dumps({
    "access_token": "fake-access-token",
    "refresh_token": "fake-refresh-token",
    "scope": "https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/calendar.readonly",
    "token_type": "Bearer",
    "expiry_date": 9999999999000,
})


def _mock_service(event_link: str = "https://calendar.google.com/event?eid=abc123"):
    """Build a fully mocked Google Calendar service object."""
    event_result = {"htmlLink": event_link}
    insert_mock = MagicMock()
    insert_mock.execute.return_value = event_result

    events_mock = MagicMock()
    events_mock.insert.return_value = insert_mock

    service = MagicMock()
    service.events.return_value = events_mock
    return service


class TestCreateExportEvent:
    """Unit tests for the create_export_event public API."""

    @patch("session_intelligence.calendar_sync._build_service")
    def test_returns_html_link_on_success(self, mock_build, tmp_path: Path) -> None:
        mock_build.return_value = _mock_service("https://calendar.google.com/event?eid=xyz")
        result = create_export_event(
            project_name="Late Night Drive",
            file_path=tmp_path / "bounce.wav",
            bpm=90.0,
            version=1,
            changed=True,
        )
        assert result == "https://calendar.google.com/event?eid=xyz"

    @patch("session_intelligence.calendar_sync._build_service")
    def test_returns_none_when_service_unavailable(self, mock_build, tmp_path: Path) -> None:
        mock_build.return_value = None
        result = create_export_event(
            project_name="Test",
            file_path=tmp_path / "test.wav",
        )
        assert result is None

    @patch("session_intelligence.calendar_sync._build_service")
    def test_event_summary_contains_project_name(self, mock_build, tmp_path: Path) -> None:
        service = _mock_service()
        mock_build.return_value = service
        create_export_event(
            project_name="IAMI",
            file_path=tmp_path / "bounce.wav",
            version=2,
            changed=True,
        )
        call_kwargs = service.events().insert.call_args.kwargs
        assert "IAMI" in call_kwargs["body"]["summary"]

    @patch("session_intelligence.calendar_sync._build_service")
    def test_changed_event_has_delta_in_summary(self, mock_build, tmp_path: Path) -> None:
        service = _mock_service()
        mock_build.return_value = service
        create_export_event(
            project_name="Proj",
            file_path=tmp_path / "f.wav",
            version=3,
            changed=True,
        )
        body = service.events().insert.call_args.kwargs["body"]
        assert "Δ" in body["summary"] or "v3" in body["summary"]

    @patch("session_intelligence.calendar_sync._build_service")
    def test_no_change_event_says_no_change(self, mock_build, tmp_path: Path) -> None:
        service = _mock_service()
        mock_build.return_value = service
        create_export_event(
            project_name="Proj",
            file_path=tmp_path / "f.wav",
            version=2,
            changed=False,
        )
        body = service.events().insert.call_args.kwargs["body"]
        assert "no change" in body["summary"].lower()

    @patch("session_intelligence.calendar_sync._build_service")
    def test_event_has_timezone_set(self, mock_build, tmp_path: Path) -> None:
        """Both start and end must have a timeZone key (fixes the 400 error)."""
        service = _mock_service()
        mock_build.return_value = service
        create_export_event(
            project_name="P",
            file_path=tmp_path / "f.wav",
        )
        body = service.events().insert.call_args.kwargs["body"]
        assert "timeZone" in body["start"]
        assert "timeZone" in body["end"]

    @patch("session_intelligence.calendar_sync._build_service")
    def test_event_end_is_after_start(self, mock_build, tmp_path: Path) -> None:
        """End time must be strictly after start time."""
        service = _mock_service()
        mock_build.return_value = service
        now = datetime(2026, 5, 16, 0, 0, 0, tzinfo=timezone.utc)
        create_export_event(
            project_name="P",
            file_path=tmp_path / "f.wav",
            when=now,
        )
        body = service.events().insert.call_args.kwargs["body"]
        start_dt = datetime.fromisoformat(body["start"]["dateTime"])
        end_dt = datetime.fromisoformat(body["end"]["dateTime"])
        assert end_dt > start_dt

    @patch("session_intelligence.calendar_sync._build_service")
    def test_bpm_appears_in_description(self, mock_build, tmp_path: Path) -> None:
        service = _mock_service()
        mock_build.return_value = service
        create_export_event(
            project_name="P",
            file_path=tmp_path / "f.wav",
            bpm=93.5,
        )
        body = service.events().insert.call_args.kwargs["body"]
        assert "94" in body["description"] or "93" in body["description"]

    @patch("session_intelligence.calendar_sync._build_service")
    def test_similarity_appears_in_description(self, mock_build, tmp_path: Path) -> None:
        service = _mock_service()
        mock_build.return_value = service
        create_export_event(
            project_name="P",
            file_path=tmp_path / "f.wav",
            similarity=0.87,
        )
        body = service.events().insert.call_args.kwargs["body"]
        assert "87%" in body["description"]

    @patch("session_intelligence.calendar_sync._build_service")
    def test_google_api_error_returns_none(self, mock_build, tmp_path: Path) -> None:
        """If the Calendar API raises, returns None (never propagates)."""
        from googleapiclient.errors import HttpError
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.status = 403
        service = _mock_service()
        service.events().insert().execute.side_effect = HttpError(resp, b"Forbidden")
        mock_build.return_value = service
        result = create_export_event(
            project_name="P",
            file_path=tmp_path / "f.wav",
        )
        assert result is None

    @patch("session_intelligence.calendar_sync._build_service")
    def test_color_is_sage_green(self, mock_build, tmp_path: Path) -> None:
        """Events should use colorId '2' (sage green)."""
        service = _mock_service()
        mock_build.return_value = service
        create_export_event(project_name="P", file_path=tmp_path / "f.wav")
        body = service.events().insert.call_args.kwargs["body"]
        assert body.get("colorId") == "2"
