"""HTTP client wrapping the Bandcamp FastAPI sidecar.

All methods are async and use httpx with proper error handling, retries,
and timeouts.  The bridge translates between the coordination engine's
domain model and the Bandcamp agent's REST API.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class BandcampError(Exception):
    """Raised when the Bandcamp API returns an error."""

    def __init__(self, status_code: int, detail: str, endpoint: str):
        self.status_code = status_code
        self.detail = detail
        self.endpoint = endpoint
        super().__init__(f"Bandcamp API error [{status_code}] on {endpoint}: {detail}")


class BandcampBridge:
    """Async HTTP client for the Bandcamp FastAPI sidecar.

    Parameters
    ----------
    base_url:
        Base URL of the Bandcamp agent (default: ``http://localhost:8000``).
    api_token:
        Optional bearer token for authentication.
    max_retries:
        Number of retries for transient failures.
    timeout:
        Request timeout in seconds.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_token: str | None = None,
        max_retries: int = 3,
        timeout: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_token = api_token
        self._max_retries = max_retries
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if self._api_token:
                headers["Authorization"] = f"Bearer {self._api_token}"

            transport = httpx.AsyncHTTPTransport(retries=self._max_retries)
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers=headers,
                timeout=httpx.Timeout(self._timeout, connect=10.0),
                transport=transport,
            )
        return self._client

    async def _request(
        self,
        method: str,
        endpoint: str,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an HTTP request with error handling."""
        client = await self._get_client()

        try:
            response = await client.request(method, endpoint, json=json, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            detail = ""
            try:
                detail = exc.response.json().get("detail", exc.response.text)
            except Exception:
                detail = exc.response.text
            raise BandcampError(exc.response.status_code, str(detail), endpoint) from exc
        except httpx.RequestError as exc:
            raise BandcampError(0, str(exc), endpoint) from exc

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def check_readiness(self) -> dict[str, Any]:
        """Check Bandcamp agent health and readiness.

        Returns dict with keys like ``cookies_valid``, ``artist_url``,
        ``upload_capable``.
        """
        return await self._request("GET", "/system/readiness")

    async def preflight(self, album_paths: list[str]) -> dict[str, Any]:
        """Run preflight validation on album directories.

        Checks cover art, metadata completeness, track numbering, etc.

        Returns dict with ``passed`` (bool) and ``issues`` (list).
        """
        return await self._request(
            "POST",
            "/library/preflight-check",
            json={"album_paths": album_paths},
        )

    async def approve_for_publish(
        self,
        album_paths: list[str],
        reviewer: str,
        note: str = "",
    ) -> dict[str, Any]:
        """Approve albums for publishing on Bandcamp.

        This sets the ``_is_publish_approved`` gate in the Bandcamp agent.

        Parameters
        ----------
        album_paths:
            List of album directory paths.
        reviewer:
            Agent identifier (e.g. ``a_and_r``, ``manager``).
        note:
            Optional approval note.
        """
        return await self._request(
            "POST",
            "/library/review/decision",
            json={
                "album_paths": album_paths,
                "approved": True,
                "reviewer": reviewer,
                "note": note,
            },
        )

    async def queue_upload(
        self,
        album_paths: list[str],
        publish: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Queue an async upload job for albums.

        Parameters
        ----------
        album_paths:
            Directories to upload.
        publish:
            If True, publish immediately after upload.
        dry_run:
            If True, validate without actually uploading.

        Returns dict with ``job_id`` for polling.
        """
        return await self._request(
            "POST",
            "/jobs/upload",
            json={
                "album_paths": album_paths,
                "publish": publish,
                "dry_run": dry_run,
            },
        )

    async def get_job_status(self, job_id: str) -> dict[str, Any]:
        """Poll status of an upload job.

        Returns dict with ``status`` (queued, running, completed,
        completed_with_failures, failed) and ``progress`` details.
        """
        return await self._request("GET", f"/jobs/{job_id}")

    async def generate_art(
        self,
        album_path: str,
        api_key: str,
        vibe: str = "default",
        prompt: str | None = None,
    ) -> dict[str, Any]:
        """Generate AI cover art via Gemini.

        Parameters
        ----------
        album_path:
            Album directory path.
        api_key:
            Gemini API key.
        vibe:
            Art style preset.
        prompt:
            Custom generation prompt (overrides vibe).

        Returns dict with ``image_path`` and ``metadata``.
        """
        body: dict[str, Any] = {
            "album_path": album_path,
            "api_key": api_key,
            "vibe": vibe,
        }
        if prompt is not None:
            body["prompt"] = prompt
        return await self._request("POST", "/generate-art", json=body)

    async def scan_library(self, path: str) -> dict[str, Any]:
        """Scan a directory for albums and extract metadata.

        Returns dict with ``albums`` (list of album info dicts).
        """
        return await self._request("POST", "/scan", json={"path": path})

    async def update_metadata(self, file_path: str, **tags: Any) -> dict[str, Any]:
        """Write/update audio file metadata (ID3 tags).

        Parameters
        ----------
        file_path:
            Path to the audio file.
        **tags:
            Tag key-value pairs (e.g. ``title="My Song"``, ``artist="Me"``).
        """
        return await self._request(
            "POST",
            "/metadata",
            json={"file_path": file_path, **tags},
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> BandcampBridge:
        await self._get_client()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()
