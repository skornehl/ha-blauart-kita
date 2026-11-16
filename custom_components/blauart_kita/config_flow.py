"""Config flow for the BlauArt Kita-Verpflegung integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BlauArtApiError, BlauArtAuthError, BlauArtClient
from .const import (
    CONF_AUTO_DEREGISTER,
    CONF_CUSTOMER_NUMBER,
    CONF_PASSWORD,
    DEFAULT_AUTO_DEREGISTER,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CUSTOMER_NUMBER): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class BlauArtConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for BlauArt Kita-Verpflegung."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return BlauArtOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_CUSTOMER_NUMBER])
            self._abort_if_unique_id_configured()

            client = BlauArtClient(
                async_get_clientsession(self.hass),
                user_input[CONF_CUSTOMER_NUMBER],
                user_input[CONF_PASSWORD],
            )
            try:
                customer_name = await client.async_login()
            except BlauArtAuthError:
                errors["base"] = "invalid_auth"
            except BlauArtApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during BlauArt login")
                errors["base"] = "unknown"
            else:
                title = customer_name or f"BlauArt {user_input[CONF_CUSTOMER_NUMBER]}"
                return self.async_create_entry(title=title, data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )


class BlauArtOptionsFlow(OptionsFlow):
    """The integration's "Konfigurieren" dialog: automatic deregistration
    of new days. The coordinator reads it on every poll, so no reload."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options.get(CONF_AUTO_DEREGISTER, DEFAULT_AUTO_DEREGISTER)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {vol.Required(CONF_AUTO_DEREGISTER, default=current): bool}
            ),
        )
