"""DataUpdateCoordinator for the BlauArt Kita-Verpflegung integration."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_type, datetime, timedelta
import logging
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import BlauArtApiError, BlauArtAuthError, BlauArtClient
from .attendance import owned_days, plan_new_days
from .const import (
    CONF_AUTO_DEREGISTER,
    DEFAULT_AUTO_DEREGISTER,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

NOTIFICATION_TITLE = "BlauArt Kita-Verpflegung"


def known_days_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """Where an entry's already-seen dates live (.storage/blauart_kita.<entry_id>)."""
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")


def _date_span(days: list[str]) -> str:
    """A sorted run of ISO dates as "01.12.-31.12." (or "01.12." for one)."""
    first, last = (
        datetime.strptime(day, "%Y-%m-%d").strftime("%d.%m.") for day in (days[0], days[-1])
    )
    return first if first == last else f"{first}-{last}"


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
        self._store = known_days_store(hass, entry.entry_id)
        self._known: frozenset[str] | None = None
        self._known_loaded = False

    @property
    def auto_deregister(self) -> bool:
        """Read on every poll, so changing the option needs no reload."""
        return self.entry.options.get(CONF_AUTO_DEREGISTER, DEFAULT_AUTO_DEREGISTER)

    async def _async_fetch_plan(self) -> dict[str, Any]:
        try:
            return await self.client.async_fetch_plan()
        except BlauArtAuthError as err:
            raise UpdateFailed(f"Login fehlgeschlagen: {err}") from err
        except BlauArtApiError as err:
            raise UpdateFailed(f"Portal nicht erreichbar: {err}") from err

    async def _async_update_data(self) -> dict[date_type, BlauArtDay]:
        plan = await self._async_fetch_plan()
        plan = await self._async_handle_new_days(plan)
        self.customer_name = plan["customer_name"]
        return self._flatten(plan)

    @staticmethod
    def _flatten(plan: dict[str, Any]) -> dict[date_type, BlauArtDay]:
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

    async def _async_remember(self, known: frozenset[str] | None) -> None:
        if known is None or known == self._known:
            return
        self._known = known
        await self._store.async_save({"known_days": sorted(known)})

    async def _async_handle_new_days(self, plan: dict[str, Any]) -> dict[str, Any]:
        """Remember every day seen so far and, with "Automatisch abmelden"
        on, deregister the ones showing up for the first time. Returns the
        plan to show - a fresh fetch if anything was sent.

        The rules (first poll, which days count, option off) are in
        attendance.plan_new_days. Days are remembered before anything is
        sent, and the deregistered ones once the portal took the change,
        so a restart never makes a seen day new again and a failed
        attempt is retried on the next poll.
        """
        if not self._known_loaded:
            stored = await self._store.async_load()
            if stored is not None:
                self._known = frozenset(stored.get("known_days", []))
            self._known_loaded = True

        decision = plan_new_days(
            owned_days(plan["calendars"]),
            self._known,
            enabled=self.auto_deregister,
            today=dt_util.now().date(),
        )
        await self._async_remember(decision.known)
        if decision.new:
            _LOGGER.debug("BlauArt: %d neue Tage im Portal: %s", len(decision.new), decision.new)
        if not decision.deregister:
            return plan

        dates = list(decision.deregister)
        _LOGGER.info("BlauArt: melde %d neue Tage automatisch ab: %s", len(dates), dates)
        error: BlauArtApiError | None = None
        try:
            await self.client.async_set_attendances({day: False for day in dates})
        except BlauArtApiError as err:
            error = err

        plan = await self._async_fetch_plan()
        current = owned_days(plan["calendars"])
        done = [day for day in dates if current.get(day, {}).get("attending") is False]
        stuck = [day for day in dates if day not in done]
        # After an error only what demonstrably landed is handled - the
        # rest counts as new again on the next poll. After a submit the
        # portal accepted, don't keep re-sending one it silently ignored.
        handled = set(done) if error is not None else set(dates)
        await self._async_remember((self._known or frozenset()) | handled)

        notification_id = f"{DOMAIN}_auto_deregister_{self.entry.entry_id}"
        if done:
            persistent_notification.async_create(
                self.hass,
                f"{self.entry.title}: {len(done)} neue Tage im Speiseplan "
                f"({_date_span(done)}) wurden automatisch abgemeldet. "
                "Gewünschte Essen bitte selbst anmelden.",
                title=NOTIFICATION_TITLE,
                notification_id=notification_id,
            )
        if stuck:
            if error is not None:
                _LOGGER.warning(
                    "BlauArt: automatisches Abmelden fehlgeschlagen (%s), "
                    "neuer Versuch beim nächsten Abruf: %s",
                    error,
                    stuck,
                )
                reason = f"Fehler: {error}. Neuer Versuch beim nächsten Abruf."
            else:
                _LOGGER.warning("BlauArt: Portal hat das Abmelden nicht übernommen: %s", stuck)
                reason = "Das Portal hat die Änderung nicht übernommen."
            persistent_notification.async_create(
                self.hass,
                f"{self.entry.title}: {len(stuck)} neue Tage ({_date_span(stuck)}) "
                f"konnten nicht automatisch abgemeldet werden. {reason} "
                "Bitte im Portal prüfen.",
                title=NOTIFICATION_TITLE,
                notification_id=f"{notification_id}_failed",
            )
        return plan

    async def async_set_attendance(self, target_date: date_type, attending: bool) -> None:
        """Set attendance for a given day and refresh so every entity
        picks up the new state right away instead of waiting up to
        DEFAULT_SCAN_INTERVAL_SECONDS for the next poll."""
        await self.client.async_set_attendance(target_date.isoformat(), attending)
        await self.async_request_refresh()
