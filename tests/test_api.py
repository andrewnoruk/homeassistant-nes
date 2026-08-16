"""Tests for the NES API client."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from custom_components.nes.api import (
    NESApiClient,
    NESApiError,
    NESAuthError,
    NESConnectionError,
    NESServiceLocation,
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


class TestServiceLocations:
    """Test linked account and service discovery."""

    async def test_discovers_services_for_every_linked_account(self) -> None:
        """Account list entries are flattened into selectable services."""
        account_list = _make_response(
            200,
            {
                "account": [
                    {
                        "accountNumber": "7013678056",
                        "serviceAddress": "123 Main St",
                    },
                    {
                        "accountNumber": "7013672147",
                        "serviceAddress": "456 Oak Ave",
                    },
                ]
            },
        )
        first_services = _make_response(
            200,
            {
                "accountSummaryType": {
                    "services": [{"serviceId": "service-1", "serviceType": "Electric"}]
                }
            },
        )
        second_services = _make_response(
            200,
            {
                "accountSummaryType": {
                    "services": [
                        {
                            "serviceId": "service-2",
                            "serviceType": "Electric",
                            "serviceDesc": "Residential Electric",
                            "serviceAddress": {
                                "addressLine1": "456 Oak Ave",
                                "city": "Nashville",
                                "state": "TN",
                                "zip": "37201",
                            },
                        }
                    ]
                }
            },
        )
        session = MagicMock()
        session.post = MagicMock(
            side_effect=[
                _make_ctx(account_list),
                _make_ctx(first_services),
                _make_ctx(second_services),
            ]
        )
        client = NESApiClient("user@example.com", "pass", session)
        client._access_token = "token"
        client._customer_id = "105112"

        locations = await client.async_get_service_locations()

        assert locations == [
            NESServiceLocation("7013678056", "service-1", "Electric", "123 Main St"),
            NESServiceLocation(
                "7013672147",
                "service-2",
                "Electric",
                "456 Oak Ave, Nashville, TN, 37201",
                "Residential Electric",
            ),
        ]
        assert locations[1].display_name.endswith("Residential Electric")
        assert session.post.call_args_list[0].args[0].endswith("/rest/account/list")
        assert session.post.call_args_list[1].kwargs["json"] == {
            "customerId": "105112",
            "accountContext": {"accountNumber": "7013678056"},
        }
        assert session.post.call_args_list[2].kwargs["json"] == {
            "customerId": "105112",
            "accountContext": {"accountNumber": "7013672147"},
        }

    async def test_select_service_uses_requested_service_not_first(self) -> None:
        """Explicit selection is retained when an account has several services."""
        summary = _make_response(
            200, {"accountSummaryType": {"paymentDueDate": "2026-08-21"}}
        )
        services = _make_response(
            200,
            {
                "accountSummaryType": {
                    "services": [
                        {"serviceId": "service-1", "serviceType": "Electric"},
                        {"serviceId": "service-2", "serviceType": "Lighting"},
                    ]
                }
            },
        )
        session = MagicMock()
        session.post = MagicMock(side_effect=[_make_ctx(summary), _make_ctx(services)])
        client = NESApiClient("user@example.com", "pass", session)
        client._access_token = "token"
        client._customer_id = "105112"

        await client.async_select_service("7013678056", "service-2")

        assert client._account_number == "7013678056"
        assert client._service_id == "service-2"
        assert client._service_type == "Lighting"
        assert client._payment_due_date == "2026-08-21"

    async def test_discovers_meter_facing_graph_service(self) -> None:
        """Current Usage Dashboard services take precedence over display rows."""
        account_list = _make_response(
            200,
            {
                "account": [
                    {
                        "accountNumber": "7013678056",
                        "serviceAddress": "123 Main St",
                    }
                ]
            },
        )
        services = _make_response(
            200,
            {
                "accountSummaryType": {
                    "services": [
                        {
                            "serviceId": "contract-1",
                            "serviceType": "Electric Residential, Active",
                        }
                    ],
                    "servicesForGraph": [
                        {
                            "serviceId": "meter-service-1",
                            "serviceContract": "contract-1",
                            "serviceType": "P",
                            "serviceDesc": "Electric",
                        }
                    ],
                }
            },
        )
        session = MagicMock()
        session.post = MagicMock(
            side_effect=[_make_ctx(account_list), _make_ctx(services)]
        )
        client = NESApiClient("user@example.com", "pass", session)
        client._access_token = "token"
        client._customer_id = "105112"

        locations = await client.async_get_service_locations()

        assert locations == [
            NESServiceLocation(
                "7013678056", "meter-service-1", "P", "123 Main St", "Electric"
            )
        ]

    async def test_legacy_display_service_maps_to_graph_service(self) -> None:
        """An existing entry is upgraded to the meter-facing service at runtime."""
        summary = _make_response(
            200,
            {
                "accountContext": {"accountNumber": "7013678056"},
                "accountSummaryType": {"paymentDueDate": "2026-08-21"},
            },
        )
        services = _make_response(
            200,
            {
                "accountSummaryType": {
                    "services": [
                        {
                            "serviceId": "contract-1",
                            "serviceType": "Electric Residential, Active",
                        }
                    ],
                    "servicesForGraph": [
                        {
                            "serviceId": "meter-service-1",
                            "serviceContract": "contract-1",
                            "serviceType": "P",
                        }
                    ],
                }
            },
        )
        usage = _make_response(
            200,
            {"history": [{"billedConsumption": "900", "billedCharge": "120"}]},
        )
        session = MagicMock()
        session.post = MagicMock(
            side_effect=[_make_ctx(summary), _make_ctx(services), _make_ctx(usage)]
        )
        client = NESApiClient("user@example.com", "pass", session)
        client._access_token = "token"
        client._customer_id = "105112"

        await client.async_select_service("7013678056", "contract-1")
        await client.async_get_usage()

        usage_context = session.post.call_args_list[2].kwargs["json"]["accountContext"]
        assert usage_context["serviceId"] == "meter-service-1"
        assert usage_context["serviceType"] == "P"

    async def test_selected_account_context_and_id_types_reach_usage(self) -> None:
        """Linked-account routing context and native IDs reach the usage API."""
        summary = _make_response(
            200,
            {
                "accountContext": {
                    "accountNumber": 7013678056,
                    "accessLevel": "1",
                    "personId": 42,
                },
                "accountSummaryType": {"paymentDueDate": "2026-08-21"},
            },
        )
        services = _make_response(
            200,
            {
                "accountSummaryType": {
                    "services": [{"serviceId": 987654, "serviceType": "E"}]
                }
            },
        )
        usage = _make_response(
            200,
            {"history": [{"billedConsumption": "900", "billedCharge": "120.00"}]},
        )
        session = MagicMock()
        session.post = MagicMock(
            side_effect=[_make_ctx(summary), _make_ctx(services), _make_ctx(usage)]
        )
        client = NESApiClient("user@example.com", "pass", session)
        client._access_token = "token"
        client._customer_id = "105112"
        client._guid = "customer-guid"

        await client.async_select_service("7013678056", "987654")
        history = await client.async_get_usage()

        assert history[0]["billedConsumption"] == "900"
        assert session.post.call_args_list[1].kwargs["json"] == {
            "customerId": "105112",
            "guid": "customer-guid",
            "accountContext": {
                "accountNumber": 7013678056,
                "accessLevel": "1",
                "personId": 42,
            },
        }
        assert session.post.call_args_list[2].kwargs["json"] == {
            "customerId": "105112",
            "accountContext": {
                "accountNumber": 7013678056,
                "serviceId": 987654,
                "billCycleCode": "2026-08-21",
                "serviceType": "E",
            },
            "direction": "current",
            "page": "1",
            "maxPerPage": "13",
        }

    async def test_current_graph_service_uses_detail_and_billing_apis(self) -> None:
        """Current portal responses are combined into legacy billed rows."""
        account_context = {
            "accountNumber": 7013678056,
            "access": "P",
            "personId": 42,
        }
        summary = _make_response(
            200,
            {
                "accountContext": account_context,
                "accountSummaryType": {"paymentDueDate": "Aug 12, 2026"},
            },
        )
        services = _make_response(
            200,
            {
                "accountSummaryType": {
                    "servicesForGraph": [
                        {
                            "billDate": "2026-07-22",
                            "meterNumber": "meter-1",
                            "serviceCat": "Dev",
                            "serviceContract": "contract-1",
                            "serviceId": 258032872526,
                            "serviceNumber": "service-number-1",
                            "serviceType": "P",
                        }
                    ]
                }
            },
        )
        detail = _make_response(
            200,
            {
                "history": [
                    {
                        "usageDate": "2026-06-21",
                        "usageCategory": "M",
                        "usageConsumptionValue": None,
                    },
                    {
                        "usageDate": "2026-06-22",
                        "usageCategory": "D",
                        "usageConsumptionValue": 0.0,
                    },
                    {
                        "usageDate": "2026-06-23",
                        "usageCategory": "D",
                        "usageConsumptionValue": 10.5,
                    },
                    {
                        "usageDate": "2026-07-21",
                        "usageCategory": "D",
                        "usageConsumptionValue": 20.25,
                    },
                    {
                        "usageDate": "2026-07-22",
                        "usageCategory": "D",
                        "usageConsumptionValue": 30.0,
                    },
                ]
            },
        )
        billing = _make_response(
            200,
            {
                "billingData": [
                    {"billingDate": "07/22/2026", "paymentAmount": "$306.93"},
                    {"billingDate": "06/22/2026", "paymentAmount": "$20.00"},
                ]
            },
        )
        session = MagicMock()
        session.post = MagicMock(
            side_effect=[
                _make_ctx(summary),
                _make_ctx(services),
                _make_ctx(detail),
                _make_ctx(billing),
            ]
        )
        client = NESApiClient("user@example.com", "pass", session)
        client._access_token = "token"
        client._customer_id = "105112"
        client._guid = "customer-guid"

        await client.async_select_service("7013678056", "258032872526")
        with patch(
            "custom_components.nes.api.dt_util.now",
            return_value=datetime(2026, 8, 16, 12, 0),
        ):
            history = await client.async_get_usage()

        assert history == [
            {
                "chargeDate": "Jun 2026",
                "chargeDateRaw": "22-Jun-2026",
                "billStartDate": "2026-05-23",
                "billEndDate": "2026-06-22",
                "billedConsumption": None,
                "billedCharge": 20.0,
                "daysOfService": 30,
                "uom": "KWH",
            },
            {
                "chargeDate": "Jul 2026",
                "chargeDateRaw": "22-Jul-2026",
                "billStartDate": "2026-06-22",
                "billEndDate": "2026-07-22",
                "billedConsumption": 30.75,
                "billedCharge": 306.93,
                "daysOfService": 30,
                "uom": "KWH",
            },
        ]

        detail_call = next(
            call
            for call in session.post.call_args_list
            if call.args[0].endswith("/rest/usage/detail/month")
        )
        assert detail_call.kwargs["json"] == {
            "customerId": "105112",
            "fromDate": "2025-07-01 12:00",
            "toDate": "2026-08-15 11:59",
            "billDate": "2026-07-22",
            "meterNumber": "meter-1",
            "serviceNumber": "service-number-1",
            "serviceId": 258032872526,
            "serviceType": "P",
            "accountContext": account_context,
            "contractNum": "contract-1",
            "netContractNum": None,
        }
        billing_call = next(
            call
            for call in session.post.call_args_list
            if call.args[0].endswith("/rest/billing/history")
        )
        assert billing_call.kwargs["json"] == {
            "customerId": "105112",
            "guid": "customer-guid",
            "accountContext": account_context,
        }

    def test_billing_aggregation_handles_currency_and_malformed_rows(self) -> None:
        """Only valid daily usage contributes to statement-period totals."""
        history = NESApiClient._aggregate_billing_history(
            [
                {"usageDate": "2026-07-01", "usageConsumptionValue": "12.5"},
                {"usageDate": "2026-07-02", "usageConsumptionValue": "bad"},
                {"usageDate": "bad", "usageConsumptionValue": 100},
            ],
            [
                {"billingDate": "07/22/2026", "paymentAmount": "($1,234.56)"},
                {"billingDate": "bad", "paymentAmount": "$99.00"},
            ],
        )

        assert history[0]["billedConsumption"] == pytest.approx(12.5)
        assert history[0]["billedCharge"] == pytest.approx(-1234.56)

    async def test_usage_rejects_response_without_history(self) -> None:
        """A changed or failed usage response is not mistaken for no usage."""
        session = MagicMock()
        session.post = MagicMock(return_value=_make_ctx(_make_response(200, {})))
        client = NESApiClient("user@example.com", "pass", session)
        client._access_token = "token"
        client._service_id = "service-1"

        with pytest.raises(NESApiError, match="history list"):
            await client.async_get_usage()

    async def test_select_service_rejects_stale_selection(self) -> None:
        """A removed service cannot silently fall back to the first service."""
        summary = _make_response(200, {"accountSummaryType": {}})
        services = _make_response(
            200,
            {
                "accountSummaryType": {
                    "services": [{"serviceId": "service-1", "serviceType": "Electric"}]
                }
            },
        )
        session = MagicMock()
        session.post = MagicMock(side_effect=[_make_ctx(summary), _make_ctx(services)])
        client = NESApiClient("user@example.com", "pass", session)
        client._access_token = "token"

        with pytest.raises(NESApiError, match="no longer available"):
            await client.async_select_service("7013678056", "missing")


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


class TestRates:
    """Test public residential rate retrieval."""

    async def test_get_rates_does_not_require_account_token(self) -> None:
        """Test public rates can be fetched independently of authentication."""
        rates_resp = _make_response(200, text="<html>rates</html>")
        session = MagicMock()
        session.get = MagicMock(return_value=_make_ctx(rates_resp))
        client = NESApiClient("user@example.com", "pass", session)

        with patch(
            "custom_components.nes.api.parse_rates_page",
            return_value={"variable_rate": 0.11864},
        ):
            rates = await client.async_get_rates()

        assert rates["variable_rate"] == pytest.approx(0.11864)
        assert rates["source_url"] == "https://www.nespower.com/rates/"

    async def test_get_rates_wraps_timeout(self) -> None:
        """Test a public page timeout is a non-auth connection error."""
        session = MagicMock()
        session.get = MagicMock(side_effect=TimeoutError)
        client = NESApiClient("user@example.com", "pass", session)

        with pytest.raises(NESConnectionError):
            await client.async_get_rates()
