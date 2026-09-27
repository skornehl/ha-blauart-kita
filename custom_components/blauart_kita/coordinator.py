"""DataUpdateCoordinator for the BlauArt Kita-Verpflegung integration."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_type, datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import BlauArtApiError, BlauArtAuthError, BlauArtClient
from .const import DEFAULT_SCAN_INTERVAL_SECONDS, DOMAIN

_LOGGER = logging.getLogger(__name__)


@dataclass
class BlauArtDay:
    """A single day's meal-plan state, flattened out of the raw parser
    output into whatever shape the platforms actually want to consume."""

    date: date_type
    editable: bool
    meals: list[str]
    attending: bool | None


class BlauArtCoordinator(DataUpdateCoordinator[dict[date_type, BlauArtDay]]):
    """Polls the portal and flattens all visible days (across all three
    calendar-N month blocks) into one date-keyed dict, sorted by date."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL_SECONDS),
        )
        self.entry = entry
        self.customer_name: str | None = None
        self.client = BlauArtClient(
            async_get_clientsession(hass),
            entry.data["customer_number"],
            entry.data["password"],
        )
        # One device per config entry, so all of a child's entities
        # (calendar, today/tomorrow sensors+switches) group together
        # under a single clean name instead of each getting its own
        # unique_id-derived entity_id.
        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="BlauArt",
            model="Kita-/Schulverpflegung Kundenportal",
            configuration_url="https://www.blauart.de/kita-schulverpflegung/kundenportal/",
        )

    async def _async_update_data(self) -> dict[date_type, BlauArtDay]:
        try:
            plan = await self.client.async_fetch_plan()
        except BlauArtAuthError as err:
            raise UpdateFailed(f"Login fehlgeschlagen: {err}") from err
        except BlauArtApiError as err:
            raise UpdateFailed(f"Portal nicht erreichbar: {err}") from err

        self.customer_name = plan["customer_name"]
        days: dict[date_type, BlauArtDay] = {}
        for cal in plan["calendars"].values():
            for date_str, info in cal["days"].items():
                day = datetime.strptime(date_str, "%Y-%m-%d").date()
                meals = [m for m in info["meals"] if m not in ("angemeldet", "abgemeldet")]
                existing = days.get(day)
                if existing is not None and existing.meals and not meals:
                    # The portal's three calendar-N tabs overlap at month
                    # boundaries - the same date can appear in two of them.
                    # Confirmed in practice (2026-09-27): a date shown as a
                    # trailing/leading preview cell in the *other* tab can
                    # render with no meal data at all, even though the tab
                    # that actually "owns" that month has the real menu.
                    # Tabs are processed in document order, so whichever one
                    # comes later would otherwise silently clobber a good
                    # entry with an empty one. Never let that happen - once
                    # a date has real meals, only ever replace it with
                    # another entry that also has real meals.
                    continue
                days[day] = BlauArtDay(
                    date=day,
                    editable=info["editable"],
                    meals=meals,
                    attending=info["attending"],
                )
        return dict(sorted(days.items()))

    async def async_set_attendance(self, target_date: date_type, attending: bool) -> None:
        """Set attendance for a given day and refresh so every entity
        picks up the new state right away instead of waiting up to
        DEFAULT_SCAN_INTERVAL_SECONDS for the next poll."""
        await self.client.async_set_attendance(target_date.isoformat(), attending)
        await self.async_request_refresh()
