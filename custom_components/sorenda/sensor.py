"""The next Sorenda reminder HA will speak."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .actions import SorendaConfigEntry
from .const import SIGNAL_REMINDERS_UPDATED
from .todo import device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SorendaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the next-reminder sensor."""
    async_add_entities([SorendaNextReminder(entry)])


class SorendaNextReminder(SensorEntity):
    """When the next reminder fires, and what it says."""

    _attr_has_entity_name = True
    _attr_translation_key = "next_reminder"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_should_poll = False

    def __init__(self, entry: SorendaConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_next_reminder"
        self._attr_device_info = device_info(entry)

    async def async_added_to_hass(self) -> None:
        """Follow the scheduler."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_REMINDERS_UPDATED.format(self._entry.entry_id),
                self._updated,
            )
        )

    @callback
    def _updated(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> datetime | None:
        """Return the moment of the next fire."""
        fire = self._entry.runtime_data.reminders.next_fire
        return dt_util.parse_datetime(fire["fireAt"]) if fire else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return what the next reminder is about."""
        fire = self._entry.runtime_data.reminders.next_fire
        if not fire:
            return {}
        return {"text": fire.get("text") or fire.get("title") or "", "note_id": fire.get("noteId")}
