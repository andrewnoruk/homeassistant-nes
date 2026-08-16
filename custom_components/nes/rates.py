"""Parse residential rates published by Nashville Electric Service."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

_RATES_ORIGIN = "https://www.nespower.com/"


def _normalize_text(parts: list[str]) -> str:
    """Collapse HTML text fragments into a single normalized string."""
    return " ".join(" ".join(parts).split())


def _normalize_document_url(url: str | None) -> str | None:
    """Return an absolute HTTPS URL only for an NES-hosted document."""
    if url is None:
        return None
    absolute_url = urljoin(_RATES_ORIGIN, url)
    parsed = urlparse(absolute_url)
    if parsed.scheme != "https" or parsed.hostname not in {
        "nespower.com",
        "www.nespower.com",
    }:
        return None
    return absolute_url


class _RatesPageParser(HTMLParser):
    """Collect page text and tables associated with section headings."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.tables: list[dict[str, Any]] = []
        self.fuel_adjustment_url: str | None = None
        self.base_rate_url: str | None = None
        self._current_heading = ""
        self._heading_depth = 0
        self._heading_parts: list[str] = []
        self._table: dict[str, Any] | None = None
        self._row: list[str] | None = None
        self._cell_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Track headings, tables, and source document links."""
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()

        if tag == "div" and self._heading_depth:
            self._heading_depth += 1
        elif tag == "div" and "field-heading" in classes:
            self._heading_depth = 1
            self._heading_parts = []

        if tag == "a" and (href := attributes.get("href")):
            href_lower = href.lower()
            if self.base_rate_url is None and href_lower.endswith("/residential.pdf"):
                self.base_rate_url = href
            if (
                self.fuel_adjustment_url is None
                and "total-monthly-fuel-cost" in href_lower
            ):
                self.fuel_adjustment_url = href

        if tag == "table":
            self._table = {"heading": self._current_heading, "rows": []}
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell_parts = []

    def handle_endtag(self, tag: str) -> None:
        """Finish the current heading, cell, row, or table."""
        if tag == "div" and self._heading_depth:
            self._heading_depth -= 1
            if self._heading_depth == 0:
                self._current_heading = _normalize_text(self._heading_parts)

        if tag in ("td", "th") and self._cell_parts is not None:
            if self._row is not None:
                self._row.append(_normalize_text(self._cell_parts))
            self._cell_parts = None
        elif tag == "tr" and self._row is not None:
            if self._table is not None and any(self._row):
                self._table["rows"].append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None

    def handle_data(self, data: str) -> None:
        """Collect visible text for rate extraction."""
        self.text_parts.append(data)
        if self._heading_depth:
            self._heading_parts.append(data)
        if self._cell_parts is not None:
            self._cell_parts.append(data)


def _parse_charge_tiers(
    tables: list[dict[str, Any]], heading: str
) -> list[dict[str, float | int | None]]:
    """Extract standard residential charges from a named table."""
    table = next(
        (
            table
            for table in tables
            if table["heading"].casefold() == heading.casefold()
        ),
        None,
    )
    if table is None:
        raise ValueError(f"Missing {heading} table")

    tiers: list[dict[str, float | int | None]] = []
    for row in table["rows"]:
        if len(row) < 3 or not (tier_match := re.search(r"Tier\s+(\d+)", row[0], re.I)):
            continue

        usage_text = row[-3]
        charge_text = row[-2]
        charge_match = re.search(r"\$\s*(\d[\d,]*(?:\.\d+)?)", charge_text)
        if charge_match is None:
            continue

        usage_limits = re.findall(r"[\d,]+", usage_text)
        if not usage_limits:
            continue
        max_kwh = (
            None
            if "more than" in usage_text.casefold()
            else float(usage_limits[-1].replace(",", ""))
        )
        tiers.append(
            {
                "tier": int(tier_match.group(1)),
                "max_kwh": max_kwh,
                "charge": float(charge_match.group(1).replace(",", "")),
            }
        )

    if not tiers:
        raise ValueError(f"No residential charges found in {heading} table")
    return tiers


def parse_rates_page(page_html: str) -> dict[str, Any]:
    """Parse current residential rate components from the NES rates page."""
    parser = _RatesPageParser()
    parser.feed(page_html)
    page_text = _normalize_text(parser.text_parts)

    base_match = re.search(
        r"Residential Base Rate\s+is\s+(\d+(?:\.\d+)?)\s+"
        r"cents per kilowatt hour",
        page_text,
        re.I,
    )
    fuel_match = re.search(
        r"current\s+Fuel Cost Adjustment\s*\(FCA\)\s+rate\s+for\s+"
        r"([A-Za-z]+)\s+is\s+(-?\d+(?:\.\d+)?)\s+cents per kWh",
        page_text,
        re.I,
    )
    if base_match is None or fuel_match is None:
        raise ValueError("Current residential rate text was not found")

    base_rate = float(base_match.group(1)) / 100
    fuel_adjustment = float(fuel_match.group(2)) / 100
    effective_month = fuel_match.group(1).title()
    base_rate_url = _normalize_document_url(parser.base_rate_url)
    fuel_adjustment_url = _normalize_document_url(parser.fuel_adjustment_url)
    if fuel_adjustment_url and (
        document_date := re.search(
            r"fuel-cost-([a-z]+)-(\d{4})\.pdf",
            fuel_adjustment_url,
            re.I,
        )
    ):
        effective_month = f"{document_date.group(1).title()} {document_date.group(2)}"

    return {
        "base_rate": round(base_rate, 6),
        "fuel_cost_adjustment": round(fuel_adjustment, 6),
        "variable_rate": round(base_rate + fuel_adjustment, 6),
        "effective_month": effective_month,
        "base_rate_url": base_rate_url,
        "fuel_adjustment_url": fuel_adjustment_url,
        "service_charge_tiers": _parse_charge_tiers(
            parser.tables, "NES Service Charge"
        ),
        "grid_access_charge_tiers": _parse_charge_tiers(
            parser.tables, "Grid Access Charge"
        ),
    }
