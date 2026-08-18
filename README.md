# Nashville Electric Service (NES) for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)
[![License: MIT](https://img.shields.io/github/license/andrewnoruk/homeassistant-nes)](LICENSE)

A custom [Home Assistant](https://www.home-assistant.io/) integration for [Nashville Electric Service (NES)](https://www.nespower.com/) that provides energy usage and cost data from the NES customer portal.

> [!NOTE]
> This repository is a maintained fork of [Max Beizer's original integration](https://github.com/maxbeizer/homeassistant-nes). It preserves the original project's MIT license and adds current residential rate sensors and support for NES logins linked to multiple service addresses. Please report fork-specific issues in [this repository's issue tracker](https://github.com/andrewnoruk/homeassistant-nes/issues).

## Sensors

| Sensor | Unit | Device Class | Description |
|--------|------|--------------|-------------|
| Latest Billed-Period Energy Usage | kWh | `energy` | Energy on the most recent completed NES bill |
| Latest Bill Amount | USD | `monetary` | Total amount on the most recent completed NES bill |
| Calendar Month-to-Date Energy Usage | kWh | `energy` | Running calendar-month total through the latest available meter reading |
| Calendar Year-to-Date Energy Usage | kWh | `energy` | Running calendar-year total through the latest available meter reading |
| Rolling Billed Energy Usage | kWh | `energy` | Total energy over the available completed bills, up to 13 periods |
| Rolling Billed Amount | USD | `monetary` | Total amount over the available completed bills, up to 13 periods |
| Variable Energy Rate | USD/kWh | — | Current residential base rate plus TVA fuel adjustment |
| Base Energy Rate | USD/kWh | — | Current residential base energy rate |
| Fuel Cost Adjustment | USD/kWh | — | Current monthly TVA fuel cost adjustment |
| Monthly Service Charge | USD | `monetary` | Charge selected from average usage over the last 12 bills |
| Monthly Grid Access Charge | USD | `monetary` | Charge selected from average usage over the last 12 bills |

The **Latest Billed-Period Energy Usage** sensor is compatible with Home Assistant's [Energy Dashboard](https://www.home-assistant.io/docs/energy/). Calendar month-to-date and year-to-date totals combine completed daily readings with the current day's available 30-minute readings. Their attributes show the latest reading timestamp, current-day subtotal, interval count, and data source. These sensors are unavailable for legacy services that only expose billed history.

## Installation

Home Assistant 2024.11.0 or newer is required.

### HACS (Recommended)

1. Open HACS in your Home Assistant instance
2. Click the three dots menu → **Custom repositories**
3. Add `https://github.com/andrewnoruk/homeassistant-nes` with category **Integration**
4. Search for "Nashville Electric Service" and install
5. Restart Home Assistant
6. Go to **Settings → Devices & Services → Add Integration → Nashville Electric Service**

### Manual

1. Copy the `custom_components/nes` directory into your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant
3. Go to **Settings → Devices & Services → Add Integration → Nashville Electric Service**

## Configuration

You'll need your NES customer portal credentials — the same email and password you use at [myaccount.nespower.com](https://myaccount.nespower.com/).

After authentication, the integration discovers every account and service address linked to the login. If more than one service is available, setup asks which address to use. Each selected service address is stored as a separate Home Assistant integration entry; run **Add Integration** again with the same credentials to add another address.

Existing installations continue using the account NES previously returned by default. Use **Settings → Devices & Services → Nashville Electric Service → Reconfigure** to select and persist a specific service address.

## How it works

The integration authenticates with NES through a multi-step flow:

1. **Azure AD B2C** headless login (Authorization Code + PKCE)
2. **NES JWT exchange** to create a server-side session
3. **NES OAuth2** token grant with the SSO session

Usage data and the public NES residential rate schedule are polled every **30 minutes**. For services shown in NES's current Usage Dashboard, the integration combines completed daily totals, current-day interval readings, and statement history. Accounts that still expose the original billed-usage API continue using it as a fallback.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Invalid email or password | Verify your credentials work at [myaccount.nespower.com](https://myaccount.nespower.com/) |
| Wrong linked address | Reconfigure the integration and select the desired service address |
| Address is missing during setup | Confirm the address is linked to the same login in the NES customer portal |
| No data after setup | Usage data may take a few minutes to appear after initial setup |
| Integration won't load | Check Home Assistant logs: **Settings → System → Logs**, filter by `nes` |

## Development

```bash
# Clone and set up
git clone https://github.com/andrewnoruk/homeassistant-nes.git
cd homeassistant-nes
python3 -m venv .venv && source .venv/bin/activate
pip install homeassistant pytest-homeassistant-custom-component

# Run tests
pytest tests/
```

## License

This maintained fork is distributed under the [MIT license](LICENSE). The original integration was created by [Max Beizer](https://github.com/maxbeizer); subsequent fork modifications are copyright Andrew Noruk.
