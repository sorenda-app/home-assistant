"""Keeps the user's open Sorenda tasks in memory."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SorendaAuthError, SorendaClient, SorendaError
from .const import DOMAIN, TASKS_INTERVAL
from .util import today_local

_LOGGER = logging.getLogger(__name__)


class SorendaTasksCoordinator(DataUpdateCoordinator[list[dict[str, Any]]]):
    """Open tasks, refreshed every few minutes and after every change made here."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: SorendaClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} tasks",
            update_interval=TASKS_INTERVAL,
        )
        self.client = client

    async def _async_update_data(self) -> list[dict[str, Any]]:
        try:
            result = await self.client.list_tasks(today_local())
        except SorendaAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SorendaError as err:
            raise UpdateFailed(str(err)) from err
        return list(result.get("tasks") or [])
