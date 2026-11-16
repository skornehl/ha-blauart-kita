"""Setup failures, the set_attendance service and the day switches."""
from __future__ import annotations

import pytest

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.blauart_kita.api import BlauArtApiError, BlauArtAuthError
from custom_components.blauart_kita.const import DOMAIN

from .conftest import FakePortal, make_entry


async def setup(hass: HomeAssistant, entry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.parametrize(
    "error",
    [BlauArtAuthError("Login failed"), BlauArtApiError("Could not load portal page: HTTP 503")],
)
async def test_portal_down_retries_setup(hass: HomeAssistant, portal: FakePortal, error) -> None:
    portal.fetch_mock.side_effect = error
    entry = make_entry()
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_set_attendance_service(hass: HomeAssistant, portal: FakePortal) -> None:
    entry = make_entry()
    await setup(hass, entry)

    await hass.services.async_call(
        DOMAIN,
        "set_attendance",
        {"config_entry_id": entry.entry_id, "date": "2026-11-20", "attending": False},
        blocking=True,
    )
    portal.submit_mock.assert_awaited_once_with({"2026-11-20": False})
    assert portal.attending("2026-11-20") is False

    with pytest.raises(HomeAssistantError, match="Unbekannte"):
        await hass.services.async_call(
            DOMAIN,
            "set_attendance",
            {"config_entry_id": "nope", "date": "2026-11-20", "attending": True},
            blocking=True,
        )
    with pytest.raises(HomeAssistantError, match="no longer editable"):
        portal.tabs["2026-11"]["days"]["2026-11-23"]["editable"] = False
        await hass.services.async_call(
            DOMAIN,
            "set_attendance",
            {"config_entry_id": entry.entry_id, "date": "2026-11-23", "attending": False},
            blocking=True,
        )

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not hass.services.has_service(DOMAIN, "set_attendance")


def switch_for_today(hass: HomeAssistant, entry) -> str:
    entity_id = er.async_get(hass).async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_day_0")
    assert entity_id is not None
    return entity_id


async def test_switch_turns_a_day_off(hass: HomeAssistant, portal: FakePortal, notifications) -> None:
    entry = make_entry()
    await setup(hass, entry)
    entity_id = switch_for_today(hass, entry)  # Monday 2026-11-16, the frozen today
    assert hass.states.get(entity_id).state == "on"

    await hass.services.async_call("switch", "turn_off", {"entity_id": entity_id}, blocking=True)
    await hass.async_block_till_done()
    portal.submit_mock.assert_awaited_once_with({"2026-11-16": False})
    assert hass.states.get(entity_id).state == "off"
    assert notifications == []


async def test_switch_failure_notifies_and_reverts(
    hass: HomeAssistant, portal: FakePortal, notifications
) -> None:
    entry = make_entry()
    await setup(hass, entry)
    entity_id = switch_for_today(hass, entry)
    # What the client now raises for a dropped connection, too.
    portal.fail_submits = BlauArtApiError("Portal not reachable: connection reset")

    await hass.services.async_call("switch", "turn_off", {"entity_id": entity_id}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "on"
    [note] = notifications
    assert note["notification_id"] == "blauart_kita_sync_failed_" + f"{entry.entry_id}_day_0"
    assert "connection reset" in note["message"]
