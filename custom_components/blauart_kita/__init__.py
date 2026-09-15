"""The BlauArt Kita-Verpflegung integration."""
from __future__ import annotations

from datetime import date
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv

from .api import BlauArtApiError
from .const import DOMAIN
from .coordinator import BlauArtCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["calendar", "sensor", "switch"]

ATTR_DATE = "date"
ATTR_ATTENDING = "attending"

SERVICE_SET_ATTENDANCE = "set_attendance"
SET_ATTENDANCE_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): str,
        vol.Required(ATTR_DATE): cv.date,
        vol.Required(ATTR_ATTENDING): cv.boolean,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = BlauArtCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _async_set_attendance(call: ServiceCall) -> None:
        target: BlauArtCoordinator | None = hass.data[DOMAIN].get(
            call.data["config_entry_id"]
        )
        if target is None:
            raise HomeAssistantError(
                f"Unbekannte BlauArt config_entry_id: {call.data['config_entry_id']}"
            )
        target_date: date = call.data[ATTR_DATE]
        try:
            await target.async_set_attendance(target_date, call.data[ATTR_ATTENDING])
        except BlauArtApiError as err:
            raise HomeAssistantError(str(err)) from err

    if not hass.services.has_service(DOMAIN, SERVICE_SET_ATTENDANCE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_ATTENDANCE,
            _async_set_attendance,
            schema=SET_ATTENDANCE_SCHEMA,
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_SET_ATTENDANCE)
    return unload_ok
