"""Sorenda: notes, tasks and reminders from the Home Assistant voice assistant."""

from __future__ import annotations

import logging
from pathlib import Path
import shutil

import probatio as vol

from homeassistant.const import CONF_API_KEY, CONF_URL, Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv, llm
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from . import actions
from .actions import SorendaConfigEntry, SorendaData
from .api import SorendaAuthError, SorendaClient, SorendaError
from .const import (
    ATTR_ACCOUNT,
    DOMAIN,
    SERVICE_ADD_REMINDER,
    SERVICE_ADD_TASK,
    SERVICE_CREATE_NOTE,
)
from .coordinator import SorendaTasksCoordinator
from .llm_api import SorendaLLMAPI
from .reminders import ReminderScheduler
from .util import TimeError

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.TODO]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

CREATE_NOTE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ACCOUNT): cv.string,
        vol.Required("text"): cv.string,
        vol.Optional("title"): cv.string,
        vol.Optional("tags"): vol.All(cv.ensure_list, [cv.string]),
    }
)
ADD_TASK_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ACCOUNT): cv.string,
        vol.Required("text"): cv.string,
        vol.Optional("due"): cv.string,
        vol.Optional("remind_minutes_before"): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=525600)
        ),
    }
)
ADD_REMINDER_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ACCOUNT): cv.string,
        vol.Required("text"): cv.string,
        vol.Required("at"): cv.string,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the LLM API and the services, once for every account."""
    llm.async_register_api(hass, SorendaLLMAPI(hass))

    def _data(call: ServiceCall) -> SorendaData:
        """The account a call names (entry id or title), else the first one."""
        entries = list(hass.config_entries.async_loaded_entries(DOMAIN))
        if not entries:
            raise ServiceValidationError("Sorenda is not set up")
        wanted = call.data.get(ATTR_ACCOUNT)
        if not wanted:
            return entries[0].runtime_data
        for entry in entries:
            if wanted in (entry.entry_id, entry.title):
                return entry.runtime_data
        raise ServiceValidationError(f"No Sorenda account {wanted!r}")

    async def _run(coro) -> ServiceResponse:
        try:
            return await coro
        except (TimeError, ValueError) as err:
            raise ServiceValidationError(str(err)) from err
        except SorendaError as err:
            raise HomeAssistantError(f"Sorenda: {err}") from err

    async def _create_note(call: ServiceCall) -> ServiceResponse:
        return await _run(
            actions.create_note(
                _data(call), call.data["text"], call.data.get("title"), call.data.get("tags")
            )
        )

    async def _add_task(call: ServiceCall) -> ServiceResponse:
        return await _run(
            actions.add_task(
                _data(call),
                call.data["text"],
                call.data.get("due"),
                call.data.get("remind_minutes_before"),
            )
        )

    async def _add_reminder(call: ServiceCall) -> ServiceResponse:
        return await _run(actions.add_reminder(_data(call), call.data["text"], call.data["at"]))

    for name, handler, schema in (
        (SERVICE_CREATE_NOTE, _create_note, CREATE_NOTE_SCHEMA),
        (SERVICE_ADD_TASK, _add_task, ADD_TASK_SCHEMA),
        (SERVICE_ADD_REMINDER, _add_reminder, ADD_REMINDER_SCHEMA),
    ):
        hass.services.async_register(
            DOMAIN, name, handler, schema=schema, supports_response=SupportsResponse.OPTIONAL
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SorendaConfigEntry) -> bool:
    """Connect to Sorenda, start the task list, reminders and LLM API."""
    client = SorendaClient(
        async_get_clientsession(hass),
        entry.data[CONF_URL],
        entry.data[CONF_API_KEY],
        str(hass.config.time_zone),
    )
    try:
        await client.token_info()
    except SorendaAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except SorendaError as err:
        raise ConfigEntryNotReady(str(err)) from err

    tasks = SorendaTasksCoordinator(hass, entry, client)
    await tasks.async_config_entry_first_refresh()
    reminders = ReminderScheduler(hass, entry, client)
    entry.runtime_data = SorendaData(client=client, tasks=tasks, reminders=reminders)

    await hass.async_add_executor_job(_install_blueprints, hass.config.path())
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await reminders.async_start()
    entry.async_on_unload(reminders.async_stop)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


def _install_blueprints(config_dir: str) -> None:
    """Copy the bundled blueprints to /config/blueprints/automation/sorenda/.

    Only missing files are copied: a blueprint the user edited stays theirs.
    """
    source = Path(__file__).parent / "blueprints"
    target = Path(config_dir) / "blueprints" / "automation" / DOMAIN
    for blueprint in source.glob("*.yaml"):
        dest = target / blueprint.name
        if dest.exists():
            continue
        try:
            target.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(blueprint, dest)
        except OSError as err:
            _LOGGER.warning("Could not install blueprint %s: %s", blueprint.name, err)


async def _async_reload(hass: HomeAssistant, entry: SorendaConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: SorendaConfigEntry) -> bool:
    """Unload the platforms; the rest goes through async_on_unload."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
