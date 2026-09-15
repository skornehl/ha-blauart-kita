"""Constants for the BlauArt Kita-Verpflegung integration."""
from __future__ import annotations

DOMAIN = "blauart_kita"

CONF_CUSTOMER_NUMBER = "customer_number"
CONF_PASSWORD = "password"

DEFAULT_SCAN_INTERVAL_SECONDS = 6 * 60 * 60  # 6h - the menu/plan changes at
# most a few times a day (new month unlocked, occasional edits), no need to
# poll more aggressively than that.

# The portal shows exactly three tabs: previous, current, and next calendar
# month relative to whenever it's loaded - not configurable, just how the
# site itself works (see BlauArtClient.async_get_plan).
CALENDAR_TABS = 3
