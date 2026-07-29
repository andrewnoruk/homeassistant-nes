"""API client for Nashville Electric Service."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

import aiohttp
from homeassistant.util import dt as dt_util

from .const import (
    API_BASE_URL,
    API_ENDPOINT_CUSTOMER,
    B2C_AUTHORIZE_URL,
    B2C_CLIENT_ID,
    B2C_CONFIRMED_URL,
    B2C_REDIRECT_URI,
    B2C_SCOPE,
    B2C_SELF_ASSERTED_URL,
    B2C_TOKEN_URL,
    LOGGER,
)


class NESAuthError(Exception):
    """Authentication error."""


def _urlencode(value: str) -> str:
    """URL-encode a string for form data."""
    return quote(value, safe="")


class NESConnectionError(Exception):
    """Connection error."""


class NESApiError(Exception):
    """General API error."""


class NESApiClient:
    """Async client for the NES customer portal API."""

    def __init__(
        self,
        username: str,
        password: str,
        session: aiohttp.ClientSession,
    ) -> None:
        """Initialize the API client."""
        self._username = username
        self._password = password
        self._session = session
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._token_expiry: datetime | None = None
        self._customer_id: str | None = None
        self._account_number: str | None = None
        self._service_id: str | None = None
        self._service_type: str | None = None
        self._payment_due_date: str | None = None
        self._token_lock = asyncio.Lock()

    @property
    def customer_id(self) -> str | None:
        """Return the NES customer ID."""
        return self._customer_id

    async def async_authenticate(self) -> None:
        """Authenticate with NES via B2C SSO + OAuth2 token exchange.

        Three-step flow:
        1. Azure AD B2C headless login → get id_token
        2. GET /rest/auth/jwt?id_token=... → get SSO session token (UUID)
        3. POST /rest/oauth/token with logintype=sso → get NES API token
        """
        try:
            # Step 1: Get B2C id_token
            id_token = await self._async_b2c_login()
            LOGGER.debug("B2C login complete, exchanging for SSO token")

            # Step 2: Exchange id_token for SSO session token
            # The /rest/auth/jwt endpoint creates a server-side session
            # and redirects to /#/ssohome/<sso_token>.
            # Note: the NES API requires the id_token as a query parameter
            # (this is how their Angular app sends it via browser redirect).
            # The id_token is short-lived and single-use.
            jwt_url = f"{API_BASE_URL}/rest/auth/jwt?id_token={id_token}"
            browser_headers = {
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            }

            async with self._session.get(
                jwt_url,
                headers=browser_headers,
                allow_redirects=False,
            ) as resp:
                if resp.status not in (302, 303):
                    raise NESAuthError(f"JWT exchange failed: HTTP {resp.status}")

                location = resp.headers.get("Location", "")
                sso_match = re.search(r"/ssohome/([a-f0-9-]+)", location)
                if not sso_match:
                    raise NESAuthError("No SSO token in JWT redirect")
                sso_token = sso_match.group(1)

            LOGGER.debug("Got SSO token, exchanging for API token")

            # Step 3: Exchange SSO token for NES API token
            url = f"{API_BASE_URL}/rest/oauth/token"
            async with self._session.post(
                url,
                data={
                    "grant_type": "password",
                    "logintype": "sso",
                    "usertoken": sso_token,
                    "username": sso_token,
                    "password": "guest",
                },
                headers={
                    "Authorization": "Basic d2ViQ2xpZW50SWRQYXNzd29yZDpzZWNyZXQ=",
                    "User-Agent": browser_headers["User-Agent"],
                },
            ) as resp:
                if resp.status == 400:
                    error_body = await resp.json()
                    error_desc = error_body.get("error_description", "Unknown error")
                    raise NESAuthError(f"NES token exchange failed: {error_desc}")
                if resp.status != 200:
                    raise NESAuthError(f"NES token exchange failed: HTTP {resp.status}")

                result = await resp.json()
                self._access_token = result.get("access_token")
                if not self._access_token:
                    raise NESAuthError("Token response missing access_token")
                self._refresh_token = result.get("refresh_token")
                expires_in = result.get("expires_in", 3600)
                self._token_expiry = dt_util.utcnow() + timedelta(seconds=expires_in)
                LOGGER.debug("Successfully authenticated with NES")

        except aiohttp.ClientError as err:
            raise NESConnectionError(
                f"Connection error during authentication: {err}"
            ) from err

    async def _async_b2c_login(self) -> str:
        """Perform headless B2C login and return the id_token.

        Cookies are managed manually because aiohttp quotes cookie
        values containing +/=/; characters, which B2C cannot parse.
        """
        code_verifier, code_challenge = self._generate_pkce()
        state = base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode()
        nonce = base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode()

        async with aiohttp.ClientSession(
            cookie_jar=aiohttp.DummyCookieJar()
        ) as auth_session:
            # Step 1a: GET /authorize → login page with CSRF + cookies
            auth_params = {
                "client_id": B2C_CLIENT_ID,
                "redirect_uri": B2C_REDIRECT_URI,
                "response_type": "code",
                "scope": B2C_SCOPE,
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "response_mode": "query",
            }

            async with auth_session.get(
                B2C_AUTHORIZE_URL,
                params=auth_params,
            ) as resp:
                if resp.status != 200:
                    raise NESAuthError(f"B2C auth page failed: HTTP {resp.status}")
                page_html = await resp.text()

                # Capture raw cookies (unquoted)
                raw_cookies: dict[str, str] = {}
                for hdr_key, hdr_val in resp.raw_headers:
                    if hdr_key.lower() == b"set-cookie":
                        cs = hdr_val.decode()
                        raw_cookies[cs.split("=", 1)[0]] = cs.split("=", 1)[1].split(
                            ";"
                        )[0]

                csrf_match = re.search(r'"csrf"\s*:\s*"([^"]+)"', page_html)
                trans_match = re.search(r'"transId"\s*:\s*"([^"]+)"', page_html)
                if not csrf_match or not trans_match:
                    raise NESAuthError("Failed to extract B2C auth parameters")
                csrf_token = csrf_match.group(1)
                trans_id = trans_match.group(1)

            cookie_header = "; ".join(f"{k}={v}" for k, v in raw_cookies.items())

            # Step 1b: POST /SelfAsserted → submit credentials
            from yarl import URL

            sa_url = (
                f"{B2C_SELF_ASSERTED_URL}?tx={trans_id}&p=B2C_1A_NES_SignUpOrSignIn"
            )
            login_data = (
                f"request_type=RESPONSE"
                f"&signInName={_urlencode(self._username)}"
                f"&password={_urlencode(self._password)}"
            )
            headers = {
                "X-CSRF-TOKEN": csrf_token,
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Cookie": cookie_header,
            }

            async with auth_session.post(
                URL(sa_url, encoded=True),
                data=login_data,
                headers=headers,
                allow_redirects=False,
            ) as resp:
                resp_text = await resp.text()
                if resp.status == 200 and resp_text.startswith("{"):
                    import json as json_mod

                    result = json_mod.loads(resp_text)
                    if str(result.get("status", "")) != "200":
                        raise NESAuthError("Invalid email or password")
                else:
                    raise NESAuthError("Unexpected B2C login response")

                # Capture new cookies
                for hdr_key, hdr_val in resp.raw_headers:
                    if hdr_key.lower() == b"set-cookie":
                        cs = hdr_val.decode()
                        raw_cookies[cs.split("=", 1)[0]] = cs.split("=", 1)[1].split(
                            ";"
                        )[0]

            cookie_header = "; ".join(f"{k}={v}" for k, v in raw_cookies.items())

            # Step 1c: GET /confirmed → redirect with auth code
            confirmed_url = (
                f"{B2C_CONFIRMED_URL}"
                f"?rememberMe=false"
                f"&csrf_token={csrf_token}"
                f"&tx={trans_id}"
                f"&p=B2C_1A_NES_SignUpOrSignIn"
            )

            async with auth_session.get(
                URL(confirmed_url, encoded=True),
                headers={"Cookie": cookie_header},
                allow_redirects=False,
            ) as resp:
                if resp.status not in (302, 303):
                    raise NESAuthError(f"B2C confirm failed: HTTP {resp.status}")
                location = resp.headers.get("Location", "")
                query_params = parse_qs(urlparse(location).query)
                if "error" in query_params:
                    raise NESAuthError(
                        f"B2C error: {query_params.get('error_description', ['Unknown'])[0]}"
                    )
                if "code" not in query_params:
                    raise NESAuthError("No auth code in B2C redirect")
                auth_code = query_params["code"][0]

            # Step 1d: Exchange auth code for id_token
            token_data = {
                "grant_type": "authorization_code",
                "client_id": B2C_CLIENT_ID,
                "code": auth_code,
                "redirect_uri": B2C_REDIRECT_URI,
                "code_verifier": code_verifier,
                "scope": B2C_SCOPE,
            }

            async with auth_session.post(B2C_TOKEN_URL, data=token_data) as resp:
                if resp.status != 200:
                    raise NESAuthError(f"B2C token exchange failed: HTTP {resp.status}")
                result = await resp.json()
                id_token = result.get("id_token")
                if not id_token:
                    raise NESAuthError("B2C response missing id_token")
                return id_token

    @staticmethod
    def _generate_pkce() -> tuple[str, str]:
        """Generate PKCE code verifier and challenge."""
        verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        return verifier, challenge

    async def _async_refresh_token(self) -> None:
        """Refresh the access token."""
        if not self._refresh_token:
            await self.async_authenticate()
            return

        url = f"{API_BASE_URL}/rest/oauth/token"
        data = f"grant_type=refresh_token&refresh_token={self._refresh_token}"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": "Basic d2ViQ2xpZW50SWRQYXNzd29yZDpzZWNyZXQ=",
        }

        try:
            async with self._session.post(url, data=data, headers=headers) as resp:
                if resp.status != 200:
                    LOGGER.debug("Token refresh failed, re-authenticating")
                    self._refresh_token = None
                    await self.async_authenticate()
                    return

                result = await resp.json()
                self._access_token = result["access_token"]
                self._refresh_token = result.get("refresh_token", self._refresh_token)
                expires_in = result.get("expires_in", 3600)
                self._token_expiry = dt_util.utcnow() + timedelta(seconds=expires_in)
                LOGGER.debug("Successfully refreshed token")

        except aiohttp.ClientError:
            LOGGER.debug("Token refresh connection error, re-authenticating")
            self._refresh_token = None
            await self.async_authenticate()

    async def _async_ensure_token(self) -> None:
        """Ensure we have a valid access token."""
        async with self._token_lock:
            if self._access_token is None:
                await self.async_authenticate()
            elif self._token_expiry and dt_util.utcnow() >= self._token_expiry:
                await self._async_refresh_token()

    def _auth_headers(self) -> dict[str, str]:
        """Return authorization headers."""
        if not self._access_token:
            raise NESAuthError("No access token available")
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        }

    async def async_get_customer(self) -> dict[str, Any]:
        """Fetch customer and service information.

        Calls three endpoints to build the full account context:
        1. /rest/account/customer/ → account number, customer ID
        2. /rest/account/summary → payment due date (used as billCycleCode)
        3. /rest/account/services → service ID, service type
        """
        await self._async_ensure_token()

        url = f"{API_BASE_URL}{API_ENDPOINT_CUSTOMER}"

        try:
            # 1. Get basic customer info
            async with self._session.post(
                url, headers=self._auth_headers(), json={}
            ) as resp:
                if resp.status == 401:
                    await self.async_authenticate()
                    async with self._session.post(
                        url, headers=self._auth_headers(), json={}
                    ) as retry_resp:
                        self._verify_response(retry_resp)
                        result = await retry_resp.json()
                else:
                    self._verify_response(resp)
                    result = await resp.json()

            self._customer_id = result.get("accountContext", {}).get(
                "userID"
            ) or result.get("customerId")
            acct_ctx = result.get("accountContext", {})
            acct_num = acct_ctx.get("accountNumber")
            self._account_number = acct_num

            # 2. Get account summary for paymentDueDate
            req = {
                "customerId": self._customer_id,
                "accountContext": {"accountNumber": acct_num},
            }
            async with self._session.post(
                f"{API_BASE_URL}/rest/account/summary",
                headers=self._auth_headers(),
                json=req,
            ) as resp:
                self._verify_response(resp)
                summary = await resp.json()
                summary_type = summary.get("accountSummaryType", {})
                self._payment_due_date = summary_type.get("paymentDueDate")

            # 3. Get services for serviceId and serviceType
            async with self._session.post(
                f"{API_BASE_URL}/rest/account/services",
                headers=self._auth_headers(),
                json=req,
            ) as resp:
                self._verify_response(resp)
                svc_data = await resp.json()
                services = svc_data.get("accountSummaryType", {}).get("services") or []
                if services:
                    svc = services[0]
                    self._service_id = svc.get("serviceId")
                    self._service_type = svc.get("serviceType")
                else:
                    self._service_id = None
                    self._service_type = None

            # Use customerId from the token (numeric ID)
            token_payload = self._access_token.split(".")[1] + "=="
            import base64 as b64
            import json as json_mod

            token_user = json_mod.loads(b64.b64decode(token_payload)).get("user", {})
            self._customer_id = token_user.get("customerId", self._customer_id)

            return result

        except aiohttp.ClientError as err:
            raise NESConnectionError(
                f"Connection error fetching customer info: {err}"
            ) from err

    async def async_get_usage(self) -> list[dict[str, Any]]:
        """Fetch 13-month usage history."""
        await self._async_ensure_token()

        if not self._service_id:
            LOGGER.warning("No serviceId available, cannot fetch usage")
            return []

        url = f"{API_BASE_URL}/rest/usage"
        payload = {
            "customerId": self._customer_id,
            "accountContext": {
                "accountNumber": self._account_number,
                "serviceId": self._service_id,
                "billCycleCode": self._payment_due_date,
                "serviceType": self._service_type,
            },
            "direction": "current",
            "page": "1",
            "maxPerPage": "13",
        }

        try:
            async with self._session.post(
                url, headers=self._auth_headers(), json=payload
            ) as resp:
                if resp.status == 401:
                    await self.async_authenticate()
                    async with self._session.post(
                        url, headers=self._auth_headers(), json=payload
                    ) as retry_resp:
                        self._verify_response(retry_resp)
                        result = await retry_resp.json()
                else:
                    self._verify_response(resp)
                    result = await resp.json()

            return result.get("history", [])

        except aiohttp.ClientError as err:
            raise NESConnectionError(
                f"Connection error fetching usage data: {err}"
            ) from err

    @staticmethod
    def _verify_response(resp: aiohttp.ClientResponse) -> None:
        """Verify the API response status."""
        if resp.status == 401:
            raise NESAuthError("Authentication failed")
        if resp.status == 403:
            raise NESAuthError("Access forbidden")
        if resp.status >= 400:
            raise NESApiError(f"API error: HTTP {resp.status}")
