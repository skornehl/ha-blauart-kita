"""Fixtures for the Home Assistant tests (need pytest-homeassistant-custom-component).

tests/test_attendance.py does not use them and still runs with plain pytest.
"""
from __future__ import annotations

import asyncio
from collections.abc import Generator
import copy
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.blauart_kita.api import BlauArtApiError
from custom_components.blauart_kita.attendance import build_attendance_forms, owned_days
from custom_components.blauart_kita.const import CONF_AUTO_DEREGISTER, DOMAIN
from portal_data import month_tab, render_page, set_day

# A Monday. Tests pin the clock: known days older than 120 days are
# forgotten, which would otherwise catch up with the fixed test months.
NOW = "2026-11-16 07:00:00+01:00"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def frozen_now(freezer):
    freezer.move_to(NOW)
    yield freezer


class FakePortal:
    """The portal's state, one tab per shown month.

    fetch() hands out a copy, as a new page load would; submit() goes
    through the integration's own form building and applies the forms
    like the portal would - a radio missing from a form loses its value.
    """

    def __init__(self, *months: tuple[int, int]) -> None:
        self.tabs: dict[str, dict[str, Any]] = {}
        self.ignore_submits = False
        self.fail_submits: Exception | None = None
        self.show(*months)

    def show(self, *months: tuple[int, int]) -> None:
        """Switch the visible months; months already shown keep their state."""
        self.tabs = {
            f"{y:04d}-{m:02d}": self.tabs.get(f"{y:04d}-{m:02d}") or month_tab(y, m)
            for y, m in months
        }

    def click(self, day: str, attending: bool) -> None:
        """A change made in the portal itself (or by a switch earlier)."""
        set_day(self.tabs[day[:7]], day, attending)

    def attending(self, day: str) -> bool | None:
        return owned_days(self._calendars())[day]["attending"]

    def _calendars(self) -> dict[str, dict[str, Any]]:
        return {f"calendar-{i}": tab for i, tab in enumerate(self.tabs.values(), 1)}

    async def fetch(self) -> dict[str, Any]:
        # The page shows the state at request time but arrives a moment
        # later - other tasks run in between, as with a real page load.
        page = {"customer_name": "Max Mustermann", "calendars": copy.deepcopy(self._calendars())}
        await asyncio.sleep(0)
        return page

    async def submit(self, changes: dict[str, bool]) -> None:
        if self.fail_submits is not None:
            raise self.fail_submits
        try:
            forms = build_attendance_forms((await self.fetch())["calendars"], changes)
        except ValueError as err:  # wrapped the same way BlauArtClient does
            raise BlauArtApiError(str(err)) from err
        if self.ignore_submits:
            return
        for form in forms:
            self.apply(form)

    def apply(self, form: dict[str, str]) -> None:
        """A submitted month form replaces that whole month's choices."""
        tab = self.tabs[form["month"]]
        for name in tab["radios"]:
            value = form.get(name)
            day = name[len("meals[") : -1]
            tab["radios"][name] = {
                "angemeldet": value == "angemeldet",
                "abgemeldet": value == "abgemeldet",
            }
            tab["days"][day]["attending"] = value == "angemeldet"

    def page(self) -> str:
        return render_page(self._calendars())


@pytest.fixture
def portal() -> Generator[FakePortal]:
    fake = FakePortal((2026, 10), (2026, 11), (2026, 12))
    with (
        patch(
            "custom_components.blauart_kita.api.BlauArtClient.async_fetch_plan",
            new=AsyncMock(side_effect=fake.fetch),
        ) as fetch,
        patch(
            "custom_components.blauart_kita.api.BlauArtClient.async_set_attendances",
            new=AsyncMock(side_effect=fake.submit),
        ) as submit,
    ):
        fake.fetch_mock = fetch
        fake.submit_mock = submit
        yield fake


@pytest.fixture
def notifications() -> Generator[list[dict[str, Any]]]:
    """Persistent notifications the coordinator raised, newest last."""
    raised: list[dict[str, Any]] = []

    def _create(hass, message, title=None, notification_id=None):
        raised.append({"message": message, "title": title, "notification_id": notification_id})

    with patch(
        "custom_components.blauart_kita.coordinator.persistent_notification.async_create",
        side_effect=_create,
    ):
        yield raised


def make_entry(*, auto_deregister: bool | None = True) -> MockConfigEntry:
    options = {} if auto_deregister is None else {CONF_AUTO_DEREGISTER: auto_deregister}
    return MockConfigEntry(
        domain=DOMAIN,
        title="Max Mustermann",
        unique_id="12345",
        data={"customer_number": "12345", "password": "secret"},
        options=options,
    )
