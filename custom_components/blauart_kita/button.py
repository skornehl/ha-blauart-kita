"""Button platform for the BlauArt Kita-Verpflegung integration.

Just one button: force an immediate portal refresh instead of waiting
out DEFAULT_SCAN_INTERVAL_SECONDS. Needed because the obvious native
choice - a dashboard button calling homeassistant.update_entity - turns
out to be a no-op for coordinator-backed (push, should_poll=False)
entities on this HA version: it never reaches CoordinatorEntity's own
async_update(), so nothing here would ever actually refresh without an
integration-owned button doing it explicitly.
"""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BlauArtCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: BlauArtCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([BlauArtRefreshButton(coordinator, entry)])


class BlauArtRefreshButton(CoordinatorEntity[BlauArtCoordinator], ButtonEntity):
    """Presses trigger an immediate coordinator refresh (a real portal
    fetch), same as would otherwise only happen every 6h or after a
    day-switch toggle."""

    _attr_has_entity_name = True
    _attr_translation_key = "refresh"
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator: BlauArtCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_refresh"
        self._attr_device_info = coordinator.device_info

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()
