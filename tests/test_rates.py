"""Tests for parsing public NES residential rates."""

from __future__ import annotations

import pytest

from custom_components.nes.rates import parse_rates_page

RATE_PAGE_HTML = """
<html><body>
  <p>
    <a href="https://www.nespower.com/residential.pdf">Residential Base Rate</a>
    <strong>is 9.254 cents per kilowatt hour (kWh).</strong>
    The current
    <a href="https://www.nespower.com/total-monthly-fuel-cost-august-2026.pdf">
      Fuel Cost Adjustment (FCA) rate
    </a>
    for August is 2.610 cents per kWh.
  </p>
  <div class="field-heading">NES Service Charge</div>
  <table>
    <tr><th>Tier</th><th>Energy Usage</th><th>Residential Charge</th><th>Supplemental</th></tr>
    <tr><td>Tier 1</td><td>0-500 kWh</td><td>$12.06</td><td>$15.60</td></tr>
    <tr><td>Tier 2</td><td>501-2000 kWh</td><td>$16.96</td><td>$20.50</td></tr>
    <tr><td>Tier 3</td><td>2001-4000 kWh</td><td>$24.96</td><td>$28.50</td></tr>
    <tr><td>Tier 4</td><td>4001-6000 kWh</td><td>$30.66</td><td>$34.20</td></tr>
    <tr><td>Tier 5</td><td>More than 6000 kWh</td><td>$36.70</td><td>$40.24</td></tr>
  </table>
  <div class="field-heading">Grid Access Charge</div>
  <table>
    <tr><th>Tier</th><th>Energy Usage</th><th>Residential Charge</th><th>Supplemental</th></tr>
    <tr><td>Tier 1</td><td>0-500 kWh</td><td>$4.50</td><td>$2.25</td></tr>
    <tr><td>Tier 2</td><td>501-2000 kWh</td><td>$7.33</td><td>$5.07</td></tr>
    <tr><td>Tier 3</td><td>2001-4000 kWh</td><td>$7.33</td><td>$5.07</td></tr>
    <tr><td>Tier 4</td><td>4001-6000 kWh</td><td>$7.88</td><td>$5.63</td></tr>
    <tr><td>Tier 5</td><td>More than 6000 kWh</td><td>$7.88</td><td>$5.63</td></tr>
  </table>
</body></html>
"""


def test_parse_rates_page() -> None:
    """Test parsing current variable rates and residential fixed charges."""
    rates = parse_rates_page(RATE_PAGE_HTML)

    assert rates["base_rate"] == pytest.approx(0.09254)
    assert rates["fuel_cost_adjustment"] == pytest.approx(0.02610)
    assert rates["variable_rate"] == pytest.approx(0.11864)
    assert rates["effective_month"] == "August 2026"
    assert rates["service_charge_tiers"][1] == {
        "tier": 2,
        "max_kwh": 2000.0,
        "charge": 16.96,
    }
    assert rates["grid_access_charge_tiers"][-1] == {
        "tier": 5,
        "max_kwh": None,
        "charge": 7.88,
    }


def test_parse_rates_page_rejects_missing_current_rate() -> None:
    """Test a changed page fails rather than publishing a stale or wrong rate."""
    with pytest.raises(ValueError, match="rate text was not found"):
        parse_rates_page("<html><body>Rates unavailable</body></html>")


def test_parse_rates_page_rejects_non_nes_document_links() -> None:
    """Test untrusted document links are not exposed as entity attributes."""
    page_html = RATE_PAGE_HTML.replace(
        "https://www.nespower.com/residential.pdf",
        "javascript:alert(1)/residential.pdf",
    )

    rates = parse_rates_page(page_html)

    assert rates["base_rate_url"] is None
