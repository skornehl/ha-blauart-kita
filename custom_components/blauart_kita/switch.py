"""Switch platform for the BlauArt Kita-Verpflegung integration.

One switch per upcoming *weekday* (today + the next FEED_DAYS-1 school
days - weekends are skipped entirely, there's never a meal to decide on
those anyway), each toggling attendance for that day directly - this is
the "kann sich selbständig an- und abmelden" surface: a scrollable feed
of these over in the ha-verpflegung dashboard, instead of the original
today/tomorrow-only pair, which turned out to be too limited/complicated
to actually use.

Unavailable once the portal no longer allows editing that day, or once
no meal is offered at all (a holiday that lands on a weekday) - so a
stale toggle can't silently fail against the real portal, and there's
nothing to tap on a day with nothing to decide.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
import logging
from typing import Any

from homeassistant.components import persistent_notification
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


def _nth_weekday(offset: int) -> date:
    """The `offset`-th weekday (Mon-Fri) counting today as 0 - skips
    Saturdays/Sundays entirely so the feed never wastes a slot on a day
    that was never going to have a meal to begin with."""
    d = date.today()
    remaining = offset
    while True:
        if d.weekday() < 5:
            if remaining == 0:
                return d
            remaining -= 1
        d += timedelta(days=1)


class BlauArtDaySwitch(CoordinatorEntity[BlauArtCoordinator], SwitchEntity):
    """On = angemeldet (attending), off = abgemeldet. `offset` (the
    offset-th weekday from today, weekends skipped) is the stable
    identity - the entity's *name* is recomputed from today's date every
    time, so entity_id stays put while the displayed weekday/date rolls
    forward a day (or three, across a weekend) at a time.

    `_attr_has_entity_name` is explicitly False, and deliberately has no
    `_attr_device_info` at all - assigning a device (even with
    has_entity_name=False) still made this HA version's frontend prefix
    every name with the device name ("Sina Marie Kornehl Dienstag,
    15.09."), redundant clutter on her own dashboard since she obviously
    knows whose plan this is. Only skipping the device link entirely
    gets a plain "Dienstag, 15.09." - calendar.py/sensor.py still use the
    device grouping fine, this is switch.py-specific.
    """

    _attr_has_entity_name = False
    _attr_icon = "mdi:silverware-fork-knife"

    def __init__(self, coordinator: BlauArtCoordinator, entry: ConfigEntry, offset: int) -> None:
        super().__init__(coordinator)
        self._offset = offset
        self._attr_unique_id = f"{entry.entry_id}_day_{offset}"
        # Optimistic UI state: set the instant a tap comes in, cleared once
        # the background portal sync (see _async_set) finishes either way -
        # a toggle should feel instant, not wait out two real HTTP round
        # trips (fetch-then-resubmit-the-whole-month, see api.py) before
        # showing anything.
        self._optimistic: bool | None = None
        self._sync_lock = asyncio.Lock()

    @property
    def _day(self) -> BlauArtDay | None:
        return self.coordinator.data.get(_nth_weekday(self._offset))

    @property
    def name(self) -> str:
        day = self._day
        target = day.date if day is not None else _nth_weekday(self._offset)
        return f"{GERMAN_WEEKDAYS[target.weekday()]}, {target.strftime('%d.%m.')}"

    @property
    def available(self) -> bool:
        day = self._day
        return super().available and day is not None and day.editable and bool(day.meals)

    @property
    def is_on(self) -> bool | None:
        if self._optimistic is not None:
            return self._optimistic
        day = self._day
        return day.attending if day is not None else None

    @property
    def extra_state_attributes(self) -> dict:
        day = self._day
        if day is None or not day.meals:
            return {"meals": ""}
        # Newline-joined (not " · ") - the dashboard's markdown rendering
        # of this attribute wraps/line-breaks properly, unlike a single
        # run-on line in a tile card's truncated secondary text.
        return {"meals": "\n".join(f"- {m}" for m in day.meals)}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)

    async def _async_set(self, attending: bool) -> None:
        """Flip the tile instantly, then sync to the real portal in the
        background - the actual round trip (re-fetch the whole month,
        re-submit it, see BlauArtClient.async_set_attendance) takes a
        couple of seconds, far too slow to make someone wait on a tap."""
        day = self._day
        if day is None:
            raise HomeAssistantError("Für diesen Tag liegen keine Portaldaten vor")
        self._optimistic = attending
        self.async_write_ha_state()
        self.hass.async_create_task(
            self._async_sync_to_portal(day.date, attending),
            name=f"blauart_kita set_attendance {day.date}",
        )

    async def _async_sync_to_portal(self, target_date: date, attending: bool) -> None:
        async with self._sync_lock:
            try:
                await self.coordinator.async_set_attendance(target_date, attending)
            except BlauArtApiError as err:
                _LOGGER.error(
                    "BlauArt: %s konnte nicht auf %s geändert werden: %s",
                    target_date,
                    "angemeldet" if attending else "abgemeldet",
                    err,
                )
                persistent_notification.async_create(
                    self.hass,
                    f"{self.name}: Änderung konnte nicht gespeichert werden ({err}). "
                    "Bitte im Portal prüfen.",
                    title="BlauArt Kita-Verpflegung",
                    notification_id=f"blauart_kita_sync_failed_{self.unique_id}",
                )
                # Reconcile with whatever the portal actually has now, in
                # case the change landed despite the error (e.g. a
                # timeout after a successful submit) - don't just fall
                # back to trusting stale, possibly-pre-tap data.
                await self.coordinator.async_request_refresh()
            finally:
                # Drop the optimistic override either way and show
                # coordinator.data's real value again - on success that's
                # the fresh post-change state (async_set_attendance
                # already refreshes), on failure it's the just-reconciled
                # one from the line above.
                self._optimistic = None
                self.async_write_ha_state()
