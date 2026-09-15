"""Switch platform for the BlauArt Kita-Verpflegung integration.

One switch per upcoming day (today + the next FEED_DAYS-1 days), each
toggling attendance for that day directly - this is the "kann sich
selbständig an- und abmelden" surface: a plain vertical stack of tile
cards over these switches gives a simple, scrollable feed (see the
ha-verpflegung dashboard) instead of the original today/tomorrow-only
pair, which turned out to be too limited/complicated to actually use.

Unavailable once the portal no longer allows editing that day, or once
no meal is offered at all (weekend/holiday) - so a stale toggle can't
silently fail against the real portal, and there's nothing to tap on a
day with nothing to decide.
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
from .const import DOMAIN, FEED_DAYS, GERMAN_WEEKDAYS
from .coordinator import BlauArtCoordinator, BlauArtDay

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: BlauArtCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [BlauArtDaySwitch(coordinator, entry, offset) for offset in range(FEED_DAYS)]
    )


class BlauArtDaySwitch(CoordinatorEntity[BlauArtCoordinator], SwitchEntity):
    """On = angemeldet (attending), off = abgemeldet. `offset` (days from
    today) is the stable identity - the entity's *name* is recomputed
    from today's date every time, so entity_id stays put while the
    displayed weekday/date rolls forward a day at a time."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:silverware-fork-knife"

    def __init__(self, coordinator: BlauArtCoordinator, entry: ConfigEntry, offset: int) -> None:
        super().__init__(coordinator)
        self._offset = offset
        self._attr_unique_id = f"{entry.entry_id}_day_{offset}"
        self._attr_device_info = coordinator.device_info

    @property
    def _day(self) -> BlauArtDay | None:
        return self.coordinator.data.get(date.today() + timedelta(days=self._offset))

    @property
    def name(self) -> str:
        day = self._day
        target = day.date if day is not None else date.today() + timedelta(days=self._offset)
        return f"{GERMAN_WEEKDAYS[target.weekday()]}, {target.strftime('%d.%m.')}"

    @property
    def available(self) -> bool:
        day = self._day
        return super().available and day is not None and day.editable and bool(day.meals)

    @property
    def is_on(self) -> bool | None:
        day = self._day
        return day.attending if day is not None else None

    @property
    def extra_state_attributes(self) -> dict:
        day = self._day
        if day is None or not day.meals:
            return {"meals": ""}
        return {"meals": " · ".join(day.meals)}

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
