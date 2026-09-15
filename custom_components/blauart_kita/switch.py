"""Switch platform for the BlauArt Kita-Verpflegung integration.

Two switches (today / tomorrow) that mirror and toggle attendance
directly from a dashboard - the actual "beim an- und abmelden
synchronisieren" the integration was built for. Anything further out
than tomorrow still goes through the blauart_kita.set_attendance
service (arbitrary dates change too rarely to warrant one switch per
visible day).
"""
from __future__ import annotations

from datetime import date, timedelta
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import BlauArtApiError
from .const import DOMAIN
from .coordinator import BlauArtCoordinator, BlauArtDay

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: BlauArtCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            BlauArtAttendanceSwitch(coordinator, entry, "today", 0),
            BlauArtAttendanceSwitch(coordinator, entry, "tomorrow", 1),
        ]
    )


class BlauArtAttendanceSwitch(CoordinatorEntity[BlauArtCoordinator], SwitchEntity):
    """On = angemeldet (attending), off = abgemeldet. Unavailable once
    the portal no longer allows editing that day (noteditables), so a
    stale toggle can't silently fail against the real portal."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: BlauArtCoordinator, entry: ConfigEntry, key: str, offset: int
    ) -> None:
        super().__init__(coordinator)
        self._offset = offset
        self._attr_unique_id = f"{entry.entry_id}_{key}_attending"
        self._attr_translation_key = f"{key}_attending"
        self._attr_device_info = coordinator.device_info
        self._attr_icon = "mdi:account-check"

    @property
    def _day(self) -> BlauArtDay | None:
        return self.coordinator.data.get(date.today() + timedelta(days=self._offset))

    @property
    def available(self) -> bool:
        day = self._day
        return super().available and day is not None and day.editable and bool(day.meals)

    @property
    def is_on(self) -> bool | None:
        day = self._day
        return day.attending if day is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)

    async def _async_set(self, attending: bool) -> None:
        day = self._day
        if day is None:
            raise HomeAssistantError("Für diesen Tag liegen keine Portaldaten vor")
        try:
            await self.coordinator.async_set_attendance(day.date, attending)
        except BlauArtApiError as err:
            raise HomeAssistantError(str(err)) from err
