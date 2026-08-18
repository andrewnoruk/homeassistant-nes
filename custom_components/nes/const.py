"""Constants for the Nashville Electric Service (NES) integration."""

import logging

DOMAIN = "nes"
LOGGER = logging.getLogger(__package__)

CONF_ACCOUNT_NUMBER = "account_number"
CONF_SERVICE_ADDRESS = "service_address"
CONF_SERVICE_ID = "service_id"
CONF_SERVICE_LOCATION = "service_location"
CONF_SERVICE_TYPE = "service_type"

ATTRIBUTION = "Data provided by Nashville Electric Service"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Azure AD B2C configuration
B2C_TENANT = "pdnesb2c"
B2C_POLICY = "b2c_1a_nes_signuporsignin"
B2C_CLIENT_ID = "1414bb49-913f-48f8-851c-f14718104471"
B2C_REDIRECT_URI = "https://myaccount.nespower.com/eportal"
B2C_SCOPE = "openid profile offline_access"

B2C_BASE_URL = (
    f"https://{B2C_TENANT}.b2clogin.com/{B2C_TENANT}.onmicrosoft.com/{B2C_POLICY}"
)
B2C_AUTHORIZE_URL = f"{B2C_BASE_URL}/oauth2/v2.0/authorize"
B2C_TOKEN_URL = f"{B2C_BASE_URL}/oauth2/v2.0/token"
B2C_SELF_ASSERTED_URL = f"{B2C_BASE_URL}/SelfAsserted"
B2C_CONFIRMED_URL = f"{B2C_BASE_URL}/api/CombinedSigninAndSignup/confirmed"

# NES API
API_BASE_URL = "https://myaccount.nespower.com"
API_ENDPOINT_CUSTOMER = "/rest/account/customer/"
RATES_URL = "https://www.nespower.com/rates/"

# Poll at the same cadence as NES's half-hour interval meter data.
UPDATE_INTERVAL_MINUTES = 30
