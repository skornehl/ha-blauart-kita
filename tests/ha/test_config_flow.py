"""Config flow (account) and options flow ('Automatisch abmelden')."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.blauart_kita.api import BlauArtApiError, BlauArtAuthError
from custom_components.blauart_kita.const import CONF_AUTO_DEREGISTER, DOMAIN

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


async def test_user_step_creates_the_entry(hass: HomeAssistant) -> None:
    with (
        patch(
            "custom_components.blauart_kita.config_flow.BlauArtClient.async_login",
            return_value="Max Mustermann",
        ),
        patch("custom_components.blauart_kita.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"customer_number": "12345", "password": "secret"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Max Mustermann"
    assert result["data"] == {"customer_number": "12345", "password": "secret"}
    assert result["result"].unique_id == "12345"


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (BlauArtAuthError("Login failed"), "invalid_auth"),
        (BlauArtApiError("HTTP 503"), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_user_step_errors(hass: HomeAssistant, error, reason) -> None:
    with patch(
        "custom_components.blauart_kita.config_flow.BlauArtClient.async_login", side_effect=error
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"customer_number": "12345", "password": "secret"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": reason}


async def test_user_step_same_account_twice(hass: HomeAssistant) -> None:
    make_entry().add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"customer_number": "12345", "password": "secret"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
