"""Calendar platform for the BlauArt Kita-Verpflegung integration.

One all-day event per visible day, so the built-in Lovelace calendar card
gives exactly the "per Tag gelistet welche Essen es gibt" view the
integration exists for - no custom frontend needed for that part.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
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
    async_add_entities([BlauArtCalendar(coordinator, entry)])


def _event_for_day(day: BlauArtDay) -> CalendarEvent | None:
    if not day.meals:
        return None
    if day.attending is None:
        status = ""
    elif day.attending:
        status = "✅ Angemeldet"
    else:
        status = "❌ Abgemeldet"
    summary = " / ".join(day.meals[:1]) if day.meals else "Essen"
    # Keep the summary short (calendar cards truncate anyway) - full meal
    # text (all "Essen 1/2/3" options) goes into the description instead.
    return CalendarEvent(
        start=day.date,
        end=day.date + timedelta(days=1),
        summary=f"{status} {summary}".strip() if status else summary,
        description="\n\n".join(day.meals),
    )


class BlauArtCalendar(CoordinatorEntity[BlauArtCoordinator], CalendarEntity):
    """Exposes every currently-visible portal day (previous/current/next
    calendar month) as one all-day calendar event."""

    _attr_has_entity_name = True
    _attr_translation_key = "plan"
    _attr_icon = "mdi:food-fork-drink"

    def __init__(self, coordinator: BlauArtCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_calendar"
        self._attr_device_info = coordinator.device_info
        self._entry = entry

    @property
    def event(self) -> CalendarEvent | None:
        today = date.today()
        for day in self.coordinator.data.values():
            if day.date >= today:
                event = _event_for_day(day)
                if event is not None:
                    return event
        return None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        events = []
        for day in self.coordinator.data.values():
            if start_date.date() <= day.date < end_date.date():
                event = _event_for_day(day)
                if event is not None:
                    events.append(event)
        return events
