"""API client for Nashville Electric Service."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
from dataclasses import dataclass
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
    BROWSER_USER_AGENT,
    LOGGER,
    RATES_URL,
)
from .rates import parse_rates_page


class NESAuthError(Exception):
    """Authentication error."""


def _urlencode(value: str) -> str:
    """URL-encode a string for form data."""
    return quote(value, safe="")


class NESConnectionError(Exception):
    """Connection error."""


class NESApiError(Exception):
    """General API error."""


@dataclass(frozen=True, slots=True)
class NESServiceLocation:
    """A selectable NES account service location."""

    account_number: str
    service_id: str
    service_type: str
    service_address: str
    service_description: str = ""

    @property
    def unique_id(self) -> str:
        """Return a stable identifier for this service location."""
        return f"{self.account_number}:{self.service_id}"

    @property
    def display_name(self) -> str:
        """Return a human-readable config-flow label."""
        account_label = f"Account ••••{self.account_number[-4:]}"
        if self.service_address:
            label = f"{self.service_address} — {account_label}"
            if self.service_description:
                return f"{label} — {self.service_description}"
            return label
        if self.service_description:
            return f"{account_label} — {self.service_description}"
        if self.service_type:
            return f"{account_label} — {self.service_type}"
        return account_label


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
        self._guid: str | None = None
        self._account_number: str | int | None = None
        self._account_context: dict[str, Any] | None = None
        self._service_id: str | int | None = None
        self._service_type: str | None = None
        self._usage_service: dict[str, Any] | None = None
        self._daily_usage: list[dict[str, Any]] | None = None
        self._payment_due_date: str | None = None
        self._token_lock = asyncio.Lock()

    @property
    def customer_id(self) -> str | None:
        """Return the NES customer ID."""
        return self._customer_id

    @property
    def daily_usage(self) -> list[dict[str, Any]] | None:
        """Return normalized daily usage for current Usage Dashboard services."""
        if self._daily_usage is None:
            return None
        return [dict(item) for item in self._daily_usage]

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
                "User-Agent": BROWSER_USER_AGENT,
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
                self._update_identity_from_token()
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
                self._update_identity_from_token()
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
            "User-Agent": BROWSER_USER_AGENT,
        }

    def _update_identity_from_token(self) -> None:
        """Populate the customer identity embedded in the NES access token."""
        if not self._access_token:
            return

        try:
            encoded_payload = self._access_token.split(".")[1]
            encoded_payload += "=" * (-len(encoded_payload) % 4)
            token_payload = json.loads(
                base64.urlsafe_b64decode(encoded_payload).decode()
            )
        except (IndexError, UnicodeDecodeError, ValueError):
            LOGGER.debug("Unable to decode NES access token identity")
            return

        token_user = token_payload.get("user", {})
        if not isinstance(token_user, dict):
            return
        self._customer_id = token_user.get("customerId", self._customer_id)
        self._guid = token_user.get("guid", self._guid)

    def _account_request(
        self,
        account_number: str | int | None = None,
        account_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build the account request shape used by the NES portal."""
        request: dict[str, Any] = {"customerId": self._customer_id}
        if self._guid:
            request["guid"] = self._guid
        if account_context is not None:
            request["accountContext"] = account_context
        elif account_number is not None:
            request["accountContext"] = {"accountNumber": account_number}
        return request

    async def _async_post_json(
        self, path: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Post to an authenticated NES endpoint and return its JSON object."""
        await self._async_ensure_token()
        url = f"{API_BASE_URL}{path}"

        try:
            async with self._session.post(
                url, headers=self._auth_headers(), json=payload
            ) as resp:
                if resp.status != 401:
                    self._verify_response(resp)
                    result = await resp.json()
                else:
                    await self.async_authenticate()
                    async with self._session.post(
                        url, headers=self._auth_headers(), json=payload
                    ) as retry_resp:
                        self._verify_response(retry_resp)
                        result = await retry_resp.json()
        except aiohttp.ClientError as err:
            raise NESConnectionError(
                f"Connection error calling NES endpoint {path}: {err}"
            ) from err

        if not isinstance(result, dict):
            raise NESApiError(f"Invalid response from NES endpoint {path}")
        return result

    @staticmethod
    def _service_list_from_response(
        result: dict[str, Any], key: str
    ) -> list[dict[str, Any]]:
        """Extract one service list from an NES account response."""
        account_summary = result.get("accountSummaryType")
        services = (
            account_summary.get(key) if isinstance(account_summary, dict) else None
        )
        if services is None:
            services = result.get(key)
        if not isinstance(services, list):
            return []
        return [service for service in services if isinstance(service, dict)]

    @classmethod
    def _services_from_response(cls, result: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract billing/display services from an NES account response."""
        return cls._service_list_from_response(result, "services")

    @classmethod
    def _usage_services_from_response(
        cls, result: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Extract meter-facing services used by the current Usage Dashboard."""
        graph_services = cls._service_list_from_response(result, "servicesForGraph")
        return graph_services or cls._services_from_response(result)

    @staticmethod
    def _service_matches_id(service: dict[str, Any], service_id: str | int) -> bool:
        """Match current and legacy identifiers for an NES service."""
        return any(
            value is not None and str(value) == str(service_id)
            for value in (
                service.get("serviceId"),
                service.get("serviceContract"),
                service.get("contractNum"),
            )
        )

    @staticmethod
    def _preferred_usage_service(
        services: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Select the first electric meter, matching the current NES portal."""
        return next(
            (service for service in services if service.get("serviceType") == "P"),
            services[0] if services else None,
        )

    @staticmethod
    def _format_service_address(value: Any) -> str:
        """Normalize NES string or structured service addresses for display."""
        if isinstance(value, str):
            return " ".join(value.split())
        if not isinstance(value, dict):
            return ""

        lines = [
            value.get("addressLine1"),
            value.get("addressLine2"),
            value.get("city"),
            value.get("state"),
            value.get("zip") or value.get("zipCode"),
        ]
        return ", ".join(str(part).strip() for part in lines if part)

    async def async_get_service_locations(self) -> list[NESServiceLocation]:
        """Return every usage service available to the authenticated login."""
        result = await self._async_post_json(
            "/rest/account/list",
            {**self._account_request(), "multiAcctLimit": 10},
        )
        accounts = result.get("account") or result.get("accounts") or []
        if not isinstance(accounts, list):
            return []
        locations: list[NESServiceLocation] = []

        for account in accounts:
            if not isinstance(account, dict):
                continue
            account_number = str(account.get("accountNumber") or "").strip()
            if not account_number:
                continue

            service_result = await self._async_post_json(
                "/rest/account/services",
                self._account_request(account_number),
            )
            account_address = self._format_service_address(
                account.get("serviceAddress")
            )
            for service in self._usage_services_from_response(service_result):
                service_id = str(service.get("serviceId") or "").strip()
                if not service_id:
                    continue
                service_type = str(
                    service.get("serviceType") or service.get("serviceCat") or ""
                ).strip()
                service_address = self._format_service_address(
                    service.get("serviceAddress") or service.get("address")
                )
                service_description = str(service.get("serviceDesc") or "").strip()
                if not service_description:
                    service_description = str(service.get("servDescr") or "").strip()
                locations.append(
                    NESServiceLocation(
                        account_number=account_number,
                        service_id=service_id,
                        service_type=service_type,
                        service_address=service_address or account_address,
                        service_description=service_description,
                    )
                )

        return locations

    async def async_select_service(
        self, account_number: str | int, service_id: str | int | None = None
    ) -> None:
        """Load and select one account service for subsequent usage calls."""
        request = self._account_request(account_number)
        summary = await self._async_post_json("/rest/account/summary", request)

        # Selecting an account in the NES portal replaces its minimal context with
        # the canonical context returned by the summary endpoint. Preserve that
        # behavior instead of continuing with a reconstructed account-number-only
        # context; linked accounts can carry additional routing information here.
        account_context = summary.get("accountContext")
        if not isinstance(account_context, dict) or account_context.get(
            "accountNumber"
        ) in (None, ""):
            account_context = {"accountNumber": account_number}

        service_result = await self._async_post_json(
            "/rest/account/services",
            self._account_request(account_context=account_context),
        )
        display_services = [
            service
            for service in self._services_from_response(service_result)
            if service.get("serviceId") is not None
        ]
        usage_services = [
            service
            for service in self._usage_services_from_response(service_result)
            if service.get("serviceId") is not None
        ]

        selected_service = next(
            (
                service
                for service in usage_services
                if service_id is not None
                and self._service_matches_id(service, service_id)
            ),
            None,
        )
        if selected_service is None and service_id is None:
            selected_service = self._preferred_usage_service(usage_services)
        if (
            selected_service is None
            and service_id is not None
            and usage_services != display_services
            and any(
                self._service_matches_id(service, service_id)
                for service in display_services
            )
        ):
            # Entries created before graph services were used store the ID of the
            # billing/display service. Map those entries to the meter service so
            # users do not have to delete and recreate their configuration.
            selected_service = self._preferred_usage_service(usage_services)
        if selected_service is None:
            raise NESApiError("Selected NES service is no longer available")

        summary_type = summary.get("accountSummaryType")
        if not isinstance(summary_type, dict):
            summary_type = {}
        self._account_number = account_context["accountNumber"]
        self._account_context = account_context
        # Keep NES identifiers in their native JSON type. The original integration
        # passed serviceId through unchanged, and the portal does the same.
        self._service_id = selected_service["serviceId"]
        self._service_type = selected_service.get(
            "serviceType"
        ) or selected_service.get("serviceCat")
        self._usage_service = selected_service
        self._daily_usage = None
        self._payment_due_date = summary_type.get("paymentDueDate")

    async def async_get_customer(
        self,
        account_number: str | int | None = None,
        service_id: str | int | None = None,
    ) -> dict[str, Any]:
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

            if not self._customer_id:
                self._customer_id = result.get("customerId") or result.get(
                    "accountContext", {}
                ).get("userID")
            acct_ctx = result.get("accountContext", {})
            selected_account = account_number or acct_ctx.get("accountNumber")
            if not selected_account:
                raise NESApiError("NES response did not include an account number")
            await self.async_select_service(selected_account, service_id)

            return result

        except aiohttp.ClientError as err:
            raise NESConnectionError(
                f"Connection error fetching customer info: {err}"
            ) from err

    async def async_get_usage(self) -> list[dict[str, Any]]:
        """Fetch 13-month usage history."""
        if self._service_id is None:
            LOGGER.warning("No serviceId available, cannot fetch usage")
            return []

        if self._supports_detailed_usage():
            return await self._async_get_detailed_usage()

        return await self._async_get_legacy_usage()

    def _supports_detailed_usage(self) -> bool:
        """Return whether the selected service supports the current portal API."""
        if not self._account_context or not self._usage_service:
            return False
        required_fields = (
            "billDate",
            "meterNumber",
            "serviceContract",
            "serviceId",
            "serviceNumber",
            "serviceType",
        )
        return all(
            self._usage_service.get(field) is not None for field in required_fields
        )

    async def _async_get_legacy_usage(self) -> list[dict[str, Any]]:
        """Fetch billed history from the original NES usage endpoint."""
        self._daily_usage = None

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
        result = await self._async_post_json("/rest/usage", payload)
        history = result.get("history")
        if not isinstance(history, list):
            raise NESApiError("NES usage response did not include a history list")
        if not history:
            LOGGER.warning(
                "NES returned no usage history for the selected account "
                "(service type: %s, bill cycle available: %s)",
                self._service_type,
                self._payment_due_date is not None,
            )
        return [item for item in history if isinstance(item, dict)]

    async def _async_get_detailed_usage(self) -> list[dict[str, Any]]:
        """Build billed history from current daily usage and statement APIs."""
        assert self._account_context is not None
        assert self._usage_service is not None

        end_date = dt_util.now().date() - timedelta(days=1)
        start_date = (end_date - timedelta(days=400)).replace(day=1)
        service = self._usage_service
        detail_payload = {
            "customerId": self._customer_id,
            "fromDate": f"{start_date:%Y-%m-%d} 12:00",
            "toDate": f"{end_date:%Y-%m-%d} 11:59",
            "billDate": service["billDate"],
            "meterNumber": service["meterNumber"],
            "serviceNumber": service["serviceNumber"],
            "serviceId": service["serviceId"],
            "serviceType": service["serviceType"],
            "accountContext": self._account_context,
            "contractNum": service["serviceContract"],
            "netContractNum": service.get("netContractNum"),
        }
        billing_payload = self._account_request(account_context=self._account_context)

        detail_result, billing_result = await asyncio.gather(
            self._async_post_json("/rest/usage/detail/month", detail_payload),
            self._async_post_json("/rest/billing/history", billing_payload),
        )
        daily_history = detail_result.get("history")
        if not isinstance(daily_history, list):
            raise NESApiError(
                "NES detailed usage response did not include a history list"
            )
        billing_history = billing_result.get("billingData")
        if not isinstance(billing_history, list):
            raise NESApiError("NES billing response did not include billingData")

        self._daily_usage = self._normalize_daily_usage(daily_history)
        return self._aggregate_billing_history(self._daily_usage, billing_history)

    @classmethod
    def _normalize_daily_usage(cls, daily_history: list[Any]) -> list[dict[str, Any]]:
        """Filter null padding and normalize one reading per calendar day."""
        by_date: dict[str, float] = {}
        for item in daily_history:
            if not isinstance(item, dict):
                continue
            usage_date = cls._parse_usage_date(item.get("usageDate"))
            usage_value = item.get("usageConsumptionValue")
            if usage_date is None or usage_value is None:
                continue
            try:
                by_date[usage_date.strftime("%Y-%m-%d")] = float(usage_value)
            except (TypeError, ValueError):
                continue
        return [
            {"usageDate": usage_date, "usageConsumptionValue": by_date[usage_date]}
            for usage_date in sorted(by_date)
        ]

    @staticmethod
    def _parse_usage_date(value: Any) -> datetime | None:
        """Parse a daily usage date returned by NES."""
        if not isinstance(value, str):
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return None

    @staticmethod
    def _parse_billing_date(value: Any) -> datetime | None:
        """Parse a statement date returned by NES."""
        if not isinstance(value, str):
            return None
        try:
            return datetime.strptime(value, "%m/%d/%Y")
        except ValueError:
            return None

    @staticmethod
    def _parse_currency(value: Any) -> float | None:
        """Parse NES currency strings such as '$1,234.56'."""
        if isinstance(value, (int, float)):
            return float(value)
        if not isinstance(value, str):
            return None
        normalized = value.strip().replace("$", "").replace(",", "")
        if normalized.startswith("(") and normalized.endswith(")"):
            normalized = f"-{normalized[1:-1]}"
        try:
            return float(normalized)
        except ValueError:
            return None

    @classmethod
    def _aggregate_billing_history(
        cls,
        daily_history: list[Any],
        billing_history: list[Any],
    ) -> list[dict[str, Any]]:
        """Combine daily meter readings with statement dates and amounts."""
        daily_values: list[tuple[datetime, float]] = []
        for item in daily_history:
            if not isinstance(item, dict):
                continue
            usage_date = cls._parse_usage_date(item.get("usageDate"))
            usage_value = item.get("usageConsumptionValue")
            if usage_date is None or usage_value is None:
                continue
            try:
                daily_values.append((usage_date, float(usage_value)))
            except (TypeError, ValueError):
                continue

        statements: list[tuple[datetime, dict[str, Any]]] = []
        for item in billing_history:
            if not isinstance(item, dict):
                continue
            billing_date = cls._parse_billing_date(item.get("billingDate"))
            if billing_date is not None:
                statements.append((billing_date, item))
        statements.sort(key=lambda item: item[0])

        if not statements:
            return []

        if len(statements) >= 2:
            first_period_start = statements[0][0] - (
                statements[1][0] - statements[0][0]
            )
        else:
            first_period_start = statements[0][0] - timedelta(days=31)

        result: list[dict[str, Any]] = []
        for index, (billing_date, statement) in enumerate(statements):
            period_start = statements[index - 1][0] if index else first_period_start
            period_values = [
                value
                for usage_date, value in daily_values
                if period_start <= usage_date < billing_date
            ]
            result.append(
                {
                    "chargeDate": billing_date.strftime("%b %Y"),
                    "chargeDateRaw": billing_date.strftime("%d-%b-%Y"),
                    "billStartDate": period_start.strftime("%Y-%m-%d"),
                    "billEndDate": billing_date.strftime("%Y-%m-%d"),
                    "billedConsumption": (
                        round(sum(period_values), 4) if period_values else None
                    ),
                    "billedCharge": cls._parse_currency(statement.get("paymentAmount")),
                    "daysOfService": (billing_date - period_start).days,
                    "uom": "KWH",
                }
            )
        return result

    async def async_get_rates(self) -> dict[str, Any]:
        """Fetch current residential rates from the public NES rates page."""
        try:
            async with self._session.get(
                RATES_URL,
                headers={"User-Agent": BROWSER_USER_AGENT},
            ) as resp:
                if resp.status >= 400:
                    raise NESApiError(f"Rates page error: HTTP {resp.status}")
                page_html = await resp.text()
        except (TimeoutError, aiohttp.ClientError) as err:
            raise NESConnectionError(
                f"Connection error fetching NES rates: {err}"
            ) from err

        try:
            rates = parse_rates_page(page_html)
        except ValueError as err:
            raise NESApiError(f"Unable to parse NES rates: {err}") from err

        rates["source_url"] = RATES_URL
        return rates

    @staticmethod
    def _verify_response(resp: aiohttp.ClientResponse) -> None:
        """Verify the API response status."""
        if resp.status == 401:
            raise NESAuthError("Authentication failed")
        if resp.status == 403:
            raise NESAuthError("Access forbidden")
        if resp.status >= 400:
            raise NESApiError(f"API error: HTTP {resp.status}")
