"""What the services and the LLM tools both do: create notes, tasks and reminders."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry

from .api import SorendaClient
from .coordinator import SorendaTasksCoordinator
from .reminders import ReminderScheduler
from .util import future_moment, normalize_due


@dataclass
class SorendaData:
    """Runtime objects of one config entry."""

    client: SorendaClient
    tasks: SorendaTasksCoordinator
    reminders: ReminderScheduler


type SorendaConfigEntry = ConfigEntry[SorendaData]


async def create_note(
    data: SorendaData, text: str, title: str | None = None, tags: list[str] | None = None
) -> dict[str, Any]:
    """Save a note; return its id and address."""
    note = await data.client.create_note(text.strip(), title=title, tags=tags)
    return {"id": note.get("id"), "url": data.client.note_url(str(note.get("id")))}


async def add_task(
    data: SorendaData,
    text: str,
    due: str | None = None,
    remind_minutes_before: int | None = None,
) -> dict[str, Any]:
    """Add a task; a reminder offset needs a deadline to count back from."""
    due_norm = normalize_due(due)
    remind = None
    if remind_minutes_before is not None:
        if due_norm is None:
            raise ValueError("a reminder before a task needs a due date")
        remind = [int(remind_minutes_before)]
    note, task_id = await data.client.create_task(text, due=due_norm, remind=remind)
    await data.tasks.async_request_refresh()
    return {
        "id": note.get("id"),
        "task_id": task_id,
        "due": due_norm,
        "url": data.client.note_url(str(note.get("id"))),
    }


async def add_reminder(data: SorendaData, text: str, at: str) -> dict[str, Any]:
    """Save a note that reminds at a moment; HA speaks it and the phone shows it."""
    moment = future_moment(at)
    note = await data.client.create_note(text.strip(), reminders=[{"at": moment}])
    await data.reminders.async_poll()
    return {"id": note.get("id"), "at": moment, "url": data.client.note_url(str(note.get("id")))}
