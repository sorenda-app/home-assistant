"""Sorenda's open tasks as a Home Assistant to-do list.

The built-in Assist intents (HassListAddItem, HassListCompleteItem) work on
any to-do entity, so "add buy batteries to Sorenda" works even without
an LLM. Deleting is not offered: a task is a line in a note, and removing
notes by voice is too easy to get wrong.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .actions import SorendaConfigEntry
from .api import SorendaError
from .const import DOMAIN
from .coordinator import SorendaTasksCoordinator
from .util import due_string, due_value


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SorendaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the to-do list."""
    async_add_entities([SorendaTodoList(entry.runtime_data.tasks, entry)])


def device_info(entry: SorendaConfigEntry) -> DeviceInfo:
    """One service device per account."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Sorenda",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=entry.runtime_data.client.base_url + "/app",
    )


def item_uid(task: dict[str, Any]) -> str:
    """A task is addressed by its note and its line."""
    return f"{task['noteId']}/{task['taskId']}"


class SorendaTodoList(CoordinatorEntity[SorendaTasksCoordinator], TodoListEntity):
    """Open Sorenda tasks."""

    _attr_has_entity_name = True
    _attr_translation_key = "tasks"
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
        | TodoListEntityFeature.SET_DUE_DATETIME_ON_ITEM
    )

    def __init__(self, coordinator: SorendaTasksCoordinator, entry: SorendaConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_tasks"
        self._attr_device_info = device_info(entry)

    @property
    def todo_items(self) -> list[TodoItem]:
        """Return the open tasks, soonest first as Sorenda sorts them."""
        items = []
        for task in self.coordinator.data or []:
            title = (task.get("title") or "").strip()
            text = task.get("text") or ""
            items.append(
                TodoItem(
                    uid=item_uid(task),
                    summary=text,
                    status=TodoItemStatus.COMPLETED
                    if task.get("status") == "done"
                    else TodoItemStatus.NEEDS_ACTION,
                    due=due_value(task.get("due")),
                    description=title if title and title != text else None,
                )
            )
        return items

    def _find(self, uid: str | None) -> dict[str, Any]:
        for task in self.coordinator.data or []:
            if item_uid(task) == uid:
                return task
        raise HomeAssistantError(f"Sorenda task {uid} not found")

    async def async_create_todo_item(self, item: TodoItem) -> None:
        """Add a task."""
        try:
            await self.coordinator.client.create_task(
                item.summary or "", due=due_string(item.due)
            )
        except SorendaError as err:
            raise HomeAssistantError(f"Sorenda: {err}") from err
        await self.coordinator.async_refresh()

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Tick a task or move its deadline. Its wording lives in the note."""
        task = self._find(item.uid)
        client = self.coordinator.client
        try:
            if item.status == TodoItemStatus.COMPLETED and task.get("status") != "done":
                await client.complete_task(task["noteId"], task["taskId"], task.get("due") or "")
            else:
                new_due = due_string(item.due)
                if new_due != (task.get("due") or None):
                    await client.patch_task(task["noteId"], task["taskId"], {"due": new_due})
        except SorendaError as err:
            raise HomeAssistantError(f"Sorenda: {err}") from err
        await self.coordinator.async_refresh()
