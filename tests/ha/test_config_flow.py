"""Options flow: the 'Automatisch abmelden' switch."""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.blauart_kita.const import CONF_AUTO_DEREGISTER

from .conftest import FakePortal, make_entry


async def test_options_default_off_and_saved(hass: HomeAssistant, portal: FakePortal) -> None:
    entry = make_entry(auto_deregister=None)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    fetches = portal.fetch_mock.await_count

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    [key] = result["data_schema"].schema
    assert key == CONF_AUTO_DEREGISTER and key.default() is False

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_AUTO_DEREGISTER: True}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_AUTO_DEREGISTER: True}
    # Read on the next poll - no reload, so no extra portal request.
    assert portal.fetch_mock.await_count == fetches

    result = await hass.config_entries.options.async_init(entry.entry_id)
    [key] = result["data_schema"].schema
    assert key.default() is True
