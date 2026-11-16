"""Regression: several days switched off in quick succession all stay off.

Reported on 0.4.2 (2026-11-16): after deregistering every meal by hand
with the day switches, some days kept jumping back to "angemeldet". Each
switch syncs in the background, and each sync fetches the whole month and
re-submits it with only its own day changed. Without the client's write
lock the syncs overlap: all fetch before any submits, and every submit
sends the others' days back as they were - the last one wins.

This test runs the real client against a portal faked at the HTTP level
(tests/ha/conftest.py FakePortal behind aioclient_mock), so nothing in
between is patched away.
"""
from __future__ import annotations

import asyncio

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

from custom_components.blauart_kita.api import PORTAL_URL
from custom_components.blauart_kita.const import DOMAIN

from .conftest import FakePortal, make_entry

DAYS = 10  # the first ten weekday switches: 2026-11-16 to 2026-11-27


async def test_quick_switch_offs_all_stick(hass: HomeAssistant, aioclient_mock) -> None:
    portal = FakePortal((2026, 10), (2026, 11), (2026, 12))
    # Closed while tapping: a page load answers with the state at request
    # time but only arrives once all taps are in - taps a second apart
    # are faster than a fetch-and-resubmit round trip to the portal.
    loaded = asyncio.Event()
    loaded.set()

    async def get(method, url, data):
        page = portal.page()
        await loaded.wait()
        return AiohttpClientMockResponse(method, url, text=page)

    async def post(method, url, data):
        portal.apply(dict(data))
        return AiohttpClientMockResponse(method, url, text=portal.page())

    aioclient_mock.get(PORTAL_URL, side_effect=get)
    aioclient_mock.post(PORTAL_URL, side_effect=post)

    entry = make_entry(auto_deregister=False)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    switches = [
        registry.async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_day_{offset}")
        for offset in range(DAYS)
    ]
    days = [hass.states.get(entity_id).name for entity_id in switches]
    assert days[0] == "Montag, 16.11." and days[-1] == "Freitag, 27.11."
    assert all(hass.states.get(entity_id).state == "on" for entity_id in switches)

    shown: dict[str, list[str]] = {entity_id: [] for entity_id in switches}

    @callback
    def record(event) -> None:
        if event.data["new_state"] is not None:
            shown[event.data["entity_id"]].append(event.data["new_state"].state)

    async_track_state_change_event(hass, switches, record)

    loaded.clear()
    for entity_id in switches:
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": entity_id}, blocking=True
        )
        assert hass.states.get(entity_id).state == "off"  # optimistic, at once
    loaded.set()
    await hass.async_block_till_done()

    november = [f"2026-11-{day:02d}" for day in (16, 17, 18, 19, 20, 23, 24, 25, 26, 27)]
    back_on = [day for day in november if portal.attending(day)]
    assert back_on == []
    assert portal.attending("2026-11-30") is True  # not touched
    # And no switch showed "on" again in between: each one keeps showing
    # what was submitted, not the old state until the debounced refresh
    # (up to 10 s after the last change) catches up.
    assert {entity_id: set(states) for entity_id, states in shown.items()} == {
        entity_id: {"off"} for entity_id in switches
    }
