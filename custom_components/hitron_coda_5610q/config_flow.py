"""Config flow for Hitron CODA-5610Q."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import HitronAuthError, HitronConnectionError, HitronCodaAPI
from .const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, DEFAULT_USERNAME, DOMAIN

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): TextSelector(
            TextSelectorConfig(type=TextSelectorType.TEXT)
        ),
        vol.Required(CONF_USERNAME, default=DEFAULT_USERNAME): TextSelector(),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


class HitronCodaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Hitron CODA-5610Q."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            session = async_get_clientsession(self.hass)
            api = HitronCodaAPI(
                session,
                user_input[CONF_HOST],
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
            )
            try:
                await api.login()
                info = await api.get_system_info()
            except HitronAuthError:
                errors["base"] = "invalid_auth"
            except HitronConnectionError:
                errors["base"] = "cannot_connect"
            else:
                # Use the serial number as the unique id
                await self.async_set_unique_id(info["SerialNum"])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Hitron CODA-5610Q ({user_input[CONF_HOST]})",
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )