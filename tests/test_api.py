"""Tests for the NES API client."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.nes.api import (
    NESApiClient,
    NESApiError,
    NESAuthError,
)


def _make_response(
    status: int,
    json_data: dict | list | None = None,
    text: str = "",
    headers: dict | None = None,
) -> MagicMock:
    """Create a mock aiohttp response."""
    import json as json_mod

    resp = MagicMock(spec=aiohttp.ClientResponse)
    resp.status = status
    resp.json = AsyncMock(return_value=json_data if json_data is not None else {})
    if json_data is not None and not text:
        text = json_mod.dumps(json_data)
    resp.text = AsyncMock(return_value=text)
    resp.headers = headers or {}
    resp.raw_headers = []
    resp.url = "https://example.com"
    return resp


def _make_ctx(resp: MagicMock) -> MagicMock:
    """Wrap a response in an async context manager."""
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


class TestAuthHeaders:
    """Test auth header generation."""

    def test_auth_headers_with_valid_token(self) -> None:
        """Test headers with a valid token."""
        session = MagicMock()
        client = NESApiClient("user@example.com", "pass", session)
        client._access_token = "my-token"

        headers = client._auth_headers()
        assert headers["Authorization"] == "Bearer my-token"
        assert "User-Agent" in headers

    def test_auth_headers_without_token_raises(self) -> None:
        """Test headers raise when no token is available."""
        session = MagicMock()
        client = NESApiClient("user@example.com", "pass", session)

        with pytest.raises(NESAuthError, match="No access token"):
            client._auth_headers()


class TestTokenRefresh:
    """Test token refresh logic."""

    async def test_refresh_success(self) -> None:
        """Test successful token refresh."""
        refresh_resp = _make_response(
            200,
            {
                "access_token": "new-token",
                "refresh_token": "new-refresh",
                "expires_in": 3600,
            },
        )
        session = MagicMock()
        session.post = MagicMock(return_value=_make_ctx(refresh_resp))

        client = NESApiClient("user@example.com", "pass", session)
        client._refresh_token = "old-refresh"
        await client._async_refresh_token()

        assert client._access_token == "new-token"
        assert client._refresh_token == "new-refresh"


class TestVerifyResponse:
    """Test response verification."""

    def test_401_raises_auth_error(self) -> None:
        resp = MagicMock()
        resp.status = 401
        with pytest.raises(NESAuthError):
            NESApiClient._verify_response(resp)

    def test_403_raises_auth_error(self) -> None:
        resp = MagicMock()
        resp.status = 403
        with pytest.raises(NESAuthError):
            NESApiClient._verify_response(resp)

    def test_500_raises_api_error(self) -> None:
        resp = MagicMock()
        resp.status = 500
        with pytest.raises(NESApiError):
            NESApiClient._verify_response(resp)

    def test_200_passes(self) -> None:
        resp = MagicMock()
        resp.status = 200
        NESApiClient._verify_response(resp)  # Should not raise
