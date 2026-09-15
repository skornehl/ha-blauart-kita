"""Sensor platform for the BlauArt Kita-Verpflegung integration.

Just two convenience sensors (today / tomorrow) so a dashboard can show
"what's for lunch" without needing the full calendar view - the calendar
entity remains the source of truth for every other visible day.
"""
from __future__ import annotations

from datetime import date, timedelta

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BlauArtCoordinator, BlauArtDay


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: BlauArtCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            BlauArtDaySensor(coordinator, entry, "today", 0),
            BlauArtDaySensor(coordinator, entry, "tomorrow", 1),
        ]
    )


class BlauArtDaySensor(CoordinatorEntity[BlauArtCoordinator], SensorEntity):
    """State is the meal name(s) for that day; attendance/editable/full
    meal list are exposed as attributes."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: BlauArtCoordinator, entry: ConfigEntry, key: str, offset: int
    ) -> None:
        super().__init__(coordinator)
        self._offset = offset
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = coordinator.device_info
        self._attr_icon = "mdi:food-fork-drink" if key == "today" else "mdi:food"

    @property
    def _day(self) -> BlauArtDay | None:
        return self.coordinator.data.get(date.today() + timedelta(days=self._offset))

    @property
    def native_value(self) -> str | None:
        day = self._day
        if day is None or not day.meals:
            return "Kein Essen"
        return day.meals[0]

    @property
    def extra_state_attributes(self) -> dict:
        day = self._day
        if day is None:
            return {}
        return {
            "date": day.date.isoformat(),
            "meals": day.meals,
            "attending": day.attending,
            "editable": day.editable,
        }
