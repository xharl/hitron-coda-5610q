"""Config flow for Hitron CODA-5610Q."""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.helpers.selector import (
    BooleanSelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import HitronAuthError, HitronConnectionError, HitronCodaAPI
from .const import (
    CONF_EXPOSE_DIAGNOSTICS,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    DEFAULT_USERNAME,
    DOMAIN,
)

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
            # Use a fresh session to avoid cookie-jar interference from
            # HA's shared session (see __init__.py for details).
            session = aiohttp.ClientSession()
            try:
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
                    # Use the serial number as the unique id. ``info`` is a
                    # SystemInfo dataclass (not a dict), so use attribute access.
                    await self.async_set_unique_id(info.serial_number)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"Hitron CODA-5610Q ({user_input[CONF_HOST]})",
                        data=user_input,
                    )
            finally:
                await session.close()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> "HitronCodaOptionsFlow":
        """Return the options flow handler."""
        return HitronCodaOptionsFlow(config_entry)


class HitronCodaOptionsFlow(OptionsFlow):
    """Handle options for Hitron CODA-5610Q.

    v0.2.14: only one option right now — expose_diagnostics. When True,
    the per-channel DOCSIS power/SNR sensors are created. Default False.
    """

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Store the config entry so we can read its current options."""
        # ``OptionsFlow`` already exposes a `config_entry` property, so
        # use a private attribute to avoid clobbering it.
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_EXPOSE_DIAGNOSTICS,
                        default=self._entry.options.get(
                            CONF_EXPOSE_DIAGNOSTICS, False
                        ),
                    ): BooleanSelector(),
                }
            ),
        )
