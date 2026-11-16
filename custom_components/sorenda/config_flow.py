"""Config flow: Sorenda address and a personal API key."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

import probatio as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_API_KEY, CONF_NAME, CONF_URL
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SorendaAuthError, SorendaClient, SorendaError
from .const import (
    CONF_HA_USERS,
    CONF_SPEAKER_ENTITY,
    CONF_SPEAKERS,
    CONF_TTS_ENGINE,
    DEFAULT_SPEAKER_ENTITY,
    DEFAULT_TTS_ENGINE,
    DEFAULT_URL,
    DOMAIN,
)

# The voice assistant reads, creates and ticks tasks. "write" covers creating
# too; "create" alone cannot tick or move a task, so a key without "write" is
# refused up front rather than failing on the first "mark it done".
REQUIRED_SCOPES = {"read", "write"}


def _schema(url: str = DEFAULT_URL, name: str = "Sorenda") -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=name): str,
            vol.Required(CONF_URL, default=url): str,
            vol.Required(CONF_API_KEY): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
        }
    )


class SorendaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a Sorenda account."""

    VERSION = 1

    async def _validate(self, url: str, key: str) -> str | None:
        client = SorendaClient(
            async_get_clientsession(self.hass), url, key, str(self.hass.config.time_zone)
        )
        try:
            scopes = set(await client.token_info())
        except SorendaAuthError:
            return "invalid_auth"
        except SorendaError:
            return "cannot_connect"
        if not REQUIRED_SCOPES <= scopes:
            return "missing_scope"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the address and the key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].strip().rstrip("/")
            key = user_input[CONF_API_KEY].strip()
            if not key.startswith("srnd_"):
                errors[CONF_API_KEY] = "invalid_format"
            elif error := await self._validate(url, key):
                errors["base"] = error
            else:
                await self.async_set_unique_id(hashlib.sha256(key.encode()).hexdigest()[:16])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_NAME].strip() or "Sorenda",
                    data={CONF_URL: url, CONF_API_KEY: key},
                )
        return self.async_show_form(step_id="user", data_schema=_schema(), errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The key stopped working: ask for a new one."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Take a new key for the same address."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            key = user_input[CONF_API_KEY].strip()
            if error := await self._validate(entry.data[CONF_URL], key):
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_API_KEY: key}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): str}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Options: which TTS speaks the reminders."""
        return SorendaOptionsFlow()


class SorendaOptionsFlow(OptionsFlow):
    """Who may use this account, and which TTS speaks its reminders."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the options."""
        if user_input is not None:
            user_input[CONF_SPEAKERS] = [
                n.strip() for n in user_input.get(CONF_SPEAKERS) or [] if n.strip()
            ]
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        users = [
            selector.SelectOptionDict(value=user.id, label=user.name or user.id)
            for user in await self.hass.auth.async_get_users()
            if not user.system_generated and user.is_active
        ]
        tts = options.get(CONF_TTS_ENGINE, DEFAULT_TTS_ENGINE)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    # Names exactly as the speaker sensor reports them. Empty,
                    # together with the users below: open to everyone.
                    vol.Optional(
                        CONF_SPEAKERS, default=options.get(CONF_SPEAKERS, [])
                    ): selector.TextSelector(selector.TextSelectorConfig(multiple=True)),
                    vol.Optional(
                        CONF_HA_USERS, default=options.get(CONF_HA_USERS, [])
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=users, multiple=True)
                    ),
                    vol.Optional(
                        CONF_SPEAKER_ENTITY,
                        default=options.get(CONF_SPEAKER_ENTITY, DEFAULT_SPEAKER_ENTITY),
                    ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                    # Left empty, reminders use Home Assistant's default speech engine.
                    vol.Optional(
                        CONF_TTS_ENGINE, description={"suggested_value": tts or None}
                    ): selector.EntitySelector(selector.EntitySelectorConfig(domain="tts")),
                }
            ),
        )
