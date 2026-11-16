"""'Automatisch abmelden': new days are deregistered, nothing else is touched."""
from __future__ import annotations

from datetime import date

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.blauart_kita.api import BlauArtApiError
from custom_components.blauart_kita.const import CONF_AUTO_DEREGISTER, DOMAIN
from portal_data import weekdays

from .conftest import FakePortal, make_entry

AUTUMN = weekdays(2026, 10) + weekdays(2026, 11) + weekdays(2026, 12)
JANUARY = weekdays(2027, 1)


def stored(hass_storage, entry) -> list[str] | None:
    data = hass_storage.get(f"{DOMAIN}.{entry.entry_id}")
    return None if data is None else data["data"]["known_days"]


async def setup(hass: HomeAssistant, entry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def refresh(hass: HomeAssistant, entry) -> None:
    await hass.data[DOMAIN][entry.entry_id].async_refresh()
    await hass.async_block_till_done()


async def test_first_poll_only_remembers(
    hass: HomeAssistant, portal: FakePortal, notifications, hass_storage
) -> None:
    entry = make_entry()
    await setup(hass, entry)

    portal.submit_mock.assert_not_called()
    assert all(portal.attending(day) for day in AUTUMN)
    assert stored(hass_storage, entry) == AUTUMN
    assert notifications == []


async def test_new_month_is_deregistered(
    hass: HomeAssistant, portal: FakePortal, notifications, hass_storage
) -> None:
    entry = make_entry()
    await setup(hass, entry)
    # Sebastian's own changes on known days, in the portal or via a switch.
    portal.click("2026-12-07", False)
    portal.click("2026-12-08", False)
    portal.click("2026-12-08", True)

    portal.show((2026, 11), (2026, 12), (2027, 1))
    await refresh(hass, entry)

    portal.submit_mock.assert_awaited_once_with({day: False for day in JANUARY})
    assert not any(portal.attending(day) for day in JANUARY)
    assert portal.attending("2026-12-07") is False
    assert portal.attending("2026-12-08") is True
    assert all(portal.attending(day) for day in weekdays(2026, 12) if day != "2026-12-07")
    # Entities show the state after the change, without another poll.
    data = hass.data[DOMAIN][entry.entry_id].data
    assert all(data[date.fromisoformat(day)].attending is False for day in JANUARY)

    assert set(JANUARY) <= set(stored(hass_storage, entry))
    [note] = notifications
    assert note["notification_id"] == f"{DOMAIN}_auto_deregister_{entry.entry_id}"
    assert f"{len(JANUARY)} neue Tage" in note["message"]
    assert "01.01.-29.01." in note["message"]

    # Re-registering a deregistered day afterwards is the user's call.
    portal.click("2027-01-05", True)
    await refresh(hass, entry)
    portal.submit_mock.assert_awaited_once()
    assert portal.attending("2027-01-05") is True


async def test_restart_does_not_deregister_again(
    hass: HomeAssistant, portal: FakePortal, notifications, hass_storage
) -> None:
    entry = make_entry()
    await setup(hass, entry)
    portal.show((2026, 11), (2026, 12), (2027, 1))
    await refresh(hass, entry)
    portal.click("2027-01-05", True)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    portal.submit_mock.assert_awaited_once()
    assert portal.attending("2027-01-05") is True


async def test_option_off_remembers_without_changing(
    hass: HomeAssistant, portal: FakePortal, notifications, hass_storage
) -> None:
    entry = make_entry(auto_deregister=None)  # never set: off
    await setup(hass, entry)
    portal.show((2026, 11), (2026, 12), (2027, 1))
    await refresh(hass, entry)

    portal.submit_mock.assert_not_called()
    assert set(JANUARY) <= set(stored(hass_storage, entry))

    # Switching it on now reaches back to nothing; only the next new month counts.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_AUTO_DEREGISTER: True}
    )
    await hass.async_block_till_done()
    await refresh(hass, entry)
    portal.submit_mock.assert_not_called()

    portal.show((2026, 12), (2027, 1), (2027, 2))
    await refresh(hass, entry)
    portal.submit_mock.assert_awaited_once_with({day: False for day in weekdays(2027, 2)})
    assert all(portal.attending(day) for day in JANUARY)


async def test_failed_attempt_is_retried(
    hass: HomeAssistant, portal: FakePortal, notifications, hass_storage
) -> None:
    entry = make_entry()
    await setup(hass, entry)
    portal.show((2026, 11), (2026, 12), (2027, 1))
    portal.fail_submits = BlauArtApiError("Could not submit attendance change: HTTP 502")
    await refresh(hass, entry)

    assert all(portal.attending(day) for day in JANUARY)
    assert not set(JANUARY) & set(stored(hass_storage, entry))
    [note] = notifications
    assert note["notification_id"].endswith("_failed")
    assert "HTTP 502" in note["message"] and "Neuer Versuch" in note["message"]
    # The poll itself went fine - entities stay available.
    assert hass.data[DOMAIN][entry.entry_id].last_update_success

    portal.fail_submits = None
    await refresh(hass, entry)
    assert portal.submit_mock.await_count == 2
    assert not any(portal.attending(day) for day in JANUARY)
    assert set(JANUARY) <= set(stored(hass_storage, entry))


async def test_ignored_submit_is_not_repeated(
    hass: HomeAssistant, portal: FakePortal, notifications, hass_storage
) -> None:
    entry = make_entry()
    await setup(hass, entry)
    portal.show((2026, 11), (2026, 12), (2027, 1))
    portal.ignore_submits = True
    await refresh(hass, entry)

    [note] = notifications
    assert note["notification_id"].endswith("_failed")
    assert "nicht übernommen" in note["message"]
    assert set(JANUARY) <= set(stored(hass_storage, entry))

    await refresh(hass, entry)
    portal.submit_mock.assert_awaited_once()


async def test_empty_page_does_not_count_as_start(
    hass: HomeAssistant, portal: FakePortal, notifications, hass_storage
) -> None:
    entry = make_entry()
    portal.show()  # e.g. a maintenance page that still has the logout link
    await setup(hass, entry)
    assert stored(hass_storage, entry) is None

    portal.show((2026, 10), (2026, 11), (2026, 12))
    await refresh(hass, entry)
    portal.submit_mock.assert_not_called()
    assert stored(hass_storage, entry) == AUTUMN


async def test_removing_the_entry_forgets_the_days(
    hass: HomeAssistant, portal: FakePortal, hass_storage
) -> None:
    entry = make_entry()
    await setup(hass, entry)
    assert stored(hass_storage, entry) is not None

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert stored(hass_storage, entry) is None
