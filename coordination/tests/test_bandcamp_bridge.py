"""Tests for the Bandcamp bridge HTTP client."""

from __future__ import annotations

import httpx
import pytest
import respx

from coordination.bandcamp_bridge import BandcampBridge, BandcampError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

BASE_URL = "http://localhost:8000"


@pytest.fixture
def bridge() -> BandcampBridge:
    return BandcampBridge(
        base_url=BASE_URL,
        api_token="test-token",
        max_retries=0,  # No retries in tests.
        timeout=5.0,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCheckReadiness:
    @pytest.mark.asyncio
    @respx.mock
    async def test_healthy(self, bridge: BandcampBridge) -> None:
        respx.get(f"{BASE_URL}/system/readiness").mock(
            return_value=httpx.Response(200, json={
                "cookies_valid": True,
                "artist_url": "https://artist.bandcamp.com",
                "upload_capable": True,
            })
        )

        result = await bridge.check_readiness()
        assert result["cookies_valid"] is True
        assert result["upload_capable"] is True

        await bridge.close()


class TestPreflight:
    @pytest.mark.asyncio
    @respx.mock
    async def test_preflight_passes(self, bridge: BandcampBridge) -> None:
        respx.post(f"{BASE_URL}/library/preflight-check").mock(
            return_value=httpx.Response(200, json={"passed": True, "issues": []})
        )

        result = await bridge.preflight(["/music/album1"])
        assert result["passed"] is True
        assert result["issues"] == []

        await bridge.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_preflight_fails(self, bridge: BandcampBridge) -> None:
        respx.post(f"{BASE_URL}/library/preflight-check").mock(
            return_value=httpx.Response(200, json={
                "passed": False,
                "issues": ["Missing cover art", "No track numbers"],
            })
        )

        result = await bridge.preflight(["/music/album1"])
        assert result["passed"] is False
        assert len(result["issues"]) == 2

        await bridge.close()


class TestApproveForPublish:
    @pytest.mark.asyncio
    @respx.mock
    async def test_approval(self, bridge: BandcampBridge) -> None:
        respx.post(f"{BASE_URL}/library/review/decision").mock(
            return_value=httpx.Response(200, json={"approved": True, "reviewer": "a_and_r"})
        )

        result = await bridge.approve_for_publish(
            album_paths=["/music/album1"],
            reviewer="a_and_r",
            note="All clear",
        )
        assert result["approved"] is True

        await bridge.close()


class TestQueueUpload:
    @pytest.mark.asyncio
    @respx.mock
    async def test_queue(self, bridge: BandcampBridge) -> None:
        respx.post(f"{BASE_URL}/jobs/upload").mock(
            return_value=httpx.Response(200, json={"job_id": "job-123", "status": "queued"})
        )

        result = await bridge.queue_upload(["/music/album1"], publish=True)
        assert result["job_id"] == "job-123"
        assert result["status"] == "queued"

        await bridge.close()


class TestGetJobStatus:
    @pytest.mark.asyncio
    @respx.mock
    async def test_completed(self, bridge: BandcampBridge) -> None:
        respx.get(f"{BASE_URL}/jobs/job-123").mock(
            return_value=httpx.Response(200, json={"status": "completed", "progress": {"uploaded": 5, "total": 5}})
        )

        result = await bridge.get_job_status("job-123")
        assert result["status"] == "completed"

        await bridge.close()


class TestGenerateArt:
    @pytest.mark.asyncio
    @respx.mock
    async def test_generate(self, bridge: BandcampBridge) -> None:
        respx.post(f"{BASE_URL}/generate-art").mock(
            return_value=httpx.Response(200, json={"image_path": "/art/cover.png", "metadata": {"vibe": "dark"}})
        )

        result = await bridge.generate_art("/music/album1", api_key="key", vibe="dark")
        assert result["image_path"] == "/art/cover.png"

        await bridge.close()


class TestScanLibrary:
    @pytest.mark.asyncio
    @respx.mock
    async def test_scan(self, bridge: BandcampBridge) -> None:
        respx.post(f"{BASE_URL}/scan").mock(
            return_value=httpx.Response(200, json={"albums": [{"path": "/music/album1", "tracks": 8}]})
        )

        result = await bridge.scan_library("/music")
        assert len(result["albums"]) == 1

        await bridge.close()


class TestUpdateMetadata:
    @pytest.mark.asyncio
    @respx.mock
    async def test_update(self, bridge: BandcampBridge) -> None:
        respx.post(f"{BASE_URL}/metadata").mock(
            return_value=httpx.Response(200, json={"updated": True})
        )

        result = await bridge.update_metadata("/music/track.wav", title="New Title", artist="Me")
        assert result["updated"] is True

        await bridge.close()


class TestErrorHandling:
    @pytest.mark.asyncio
    @respx.mock
    async def test_404_raises_bandcamp_error(self, bridge: BandcampBridge) -> None:
        respx.get(f"{BASE_URL}/system/readiness").mock(
            return_value=httpx.Response(404, json={"detail": "Not found"})
        )

        with pytest.raises(BandcampError) as exc_info:
            await bridge.check_readiness()
        assert exc_info.value.status_code == 404

        await bridge.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_500_raises_bandcamp_error(self, bridge: BandcampBridge) -> None:
        respx.post(f"{BASE_URL}/jobs/upload").mock(
            return_value=httpx.Response(500, json={"detail": "Internal server error"})
        )

        with pytest.raises(BandcampError) as exc_info:
            await bridge.queue_upload(["/music/album1"])
        assert exc_info.value.status_code == 500

        await bridge.close()


class TestContextManager:
    @pytest.mark.asyncio
    @respx.mock
    async def test_async_context_manager(self) -> None:
        respx.get(f"{BASE_URL}/system/readiness").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )

        async with BandcampBridge(base_url=BASE_URL, max_retries=0) as bridge:
            result = await bridge.check_readiness()
            assert result["ok"] is True


class TestAuthHeader:
    @pytest.mark.asyncio
    @respx.mock
    async def test_bearer_token_sent(self, bridge: BandcampBridge) -> None:
        route = respx.get(f"{BASE_URL}/system/readiness").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )

        await bridge.check_readiness()

        assert route.called
        request = route.calls[0].request
        assert request.headers["Authorization"] == "Bearer test-token"

        await bridge.close()
