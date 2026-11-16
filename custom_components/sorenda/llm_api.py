"""Sorenda tools for an LLM conversation agent (OpenAI, Anthropic, Gemini, OpenRouter...).

Enabled per agent: Settings → Voice assistants → the agent → "Control Home
Assistant" → tick "Sorenda" next to "Assist". The set is narrow on purpose:
create, read, tick. Nothing here deletes or rewrites a note.
"""

from __future__ import annotations

from typing import Any

import probatio as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util
from homeassistant.util.json import JsonObjectType

from . import access, actions
from .actions import SorendaData
from .api import SorendaError, SorendaSubscriptionError
from .const import DOMAIN
from .util import TimeError, match_tasks

API_PROMPT = """Sorenda is the user's notes and tasks app. Use the sorenda_* tools when the user:
- asks to write something down or remember it ("note that…", "remember…") → sorenda_create_note;
- names something to do, optionally with a deadline ("add a task…", "I need to… by Friday") → sorenda_add_task;
- asks to be reminded at a moment ("remind me in 20 minutes…", "remind me tomorrow at 9…") → sorenda_add_reminder;
- asks what they have to do ("what do I have today?") → sorenda_list_tasks;
- says a task is done ("mark … done", "I called mum") → sorenda_complete_task;
- asks what they noted about something ("what did I note about…?") → sorenda_search_notes.
The user may speak any language: match the intent, not these English phrases, and answer in their language.
Prefer these over Home Assistant's shopping/to-do lists unless the user names another list.
Now it is {now} ({tz}). Resolve relative times yourself and pass ISO 8601 with the offset, e.g. {example}.
Keep the user's own words, in their language, for the text; drop only the command ("remind me", "note that").
Confirm in one short sentence, saying the time in words."""

_READ = llm.ToolAnnotations(read_only=True, destructive=False, idempotent=True, open_world=True)
_CREATE = llm.ToolAnnotations(read_only=False, destructive=False, idempotent=False, open_world=True)
_TICK = llm.ToolAnnotations(read_only=False, destructive=False, idempotent=True, open_world=True)


UNAVAILABLE_PROMPT = """Sorenda (the user's notes and tasks app) is not available in this conversation: {reason}.
If the user asks to write something down, add a task, set a reminder or look up their notes, say in one short sentence that their Sorenda is not available for their voice, and do not save it anywhere else."""


class SorendaLLMAPI(llm.API):
    """The Sorenda tool set, for whichever account the speaker may use.

    One API serves every configured account: which one a conversation gets
    is decided per request by access.resolve (voice, then HA user), and
    checked again before every tool call.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        # The base class is a slots dataclass: its own __init__ sets every field
        # with a default (requires_admin since HA 2026.11), so it must run.
        super().__init__(hass=hass, id=DOMAIN, name="Sorenda")

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        """Return the tools of the speaker's account, with the current time."""
        granted = access.resolve(self.hass, llm_context)
        if granted.entry is None:
            return llm.APIInstance(
                api=self,
                api_prompt=UNAVAILABLE_PROMPT.format(reason=granted.reason),
                llm_context=llm_context,
                tools=[],
            )
        now = dt_util.now().replace(second=0, microsecond=0)
        prompt = API_PROMPT.format(
            now=now.strftime("%A %Y-%m-%d %H:%M"),
            tz=self.hass.config.time_zone,
            example=now.isoformat(),
        )
        if granted.speaker:
            prompt += f"\nThe speaker is {granted.speaker}; these are their notes."
        entry_id = granted.entry.entry_id
        tools: list[llm.Tool] = [
            tool(entry_id)
            for tool in (
                CreateNoteTool,
                AddTaskTool,
                AddReminderTool,
                ListTasksTool,
                CompleteTaskTool,
                SearchNotesTool,
            )
        ]
        return llm.APIInstance(api=self, api_prompt=prompt, llm_context=llm_context, tools=tools)


class _SorendaTool(llm.Tool):
    integration = DOMAIN

    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id
        self.data: SorendaData

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> llm.ToolResult:
        # The speaker may have changed since the tools were handed out (a
        # follow-up turn by someone else): the account is checked again.
        granted = access.resolve(hass, llm_context)
        if granted.entry is None or granted.entry.entry_id != self.entry_id:
            reason = granted.reason or "the speaker changed"
            return llm.ToolResult(data={"error": f"not allowed: {reason}"}, error=True)
        self.data = granted.entry.runtime_data
        try:
            return llm.ToolResult(data=await self._run(tool_input.tool_args))
        except TimeError as err:
            return llm.ToolResult(data={"error": f"bad time: {err}"}, error=True)
        except SorendaSubscriptionError:
            return llm.ToolResult(
                data={"error": "Sorenda needs an active subscription to save"}, error=True
            )
        except (SorendaError, ValueError) as err:
            return llm.ToolResult(data={"error": str(err)}, error=True)

    async def _run(self, args: dict[str, Any]) -> JsonObjectType:
        raise NotImplementedError


class CreateNoteTool(_SorendaTool):
    name = "sorenda_create_note"
    description = "Save a note in Sorenda: something the user wants written down or remembered."
    annotations = _CREATE
    parameters = vol.Schema(
        {
            vol.Required("text", description="The note, in the user's words"): str,
            vol.Optional("title", description="A short title, only if the user gave one"): str,
            vol.Optional("tags", description="Tags, only if the user named them"): [str],
        }
    )

    async def _run(self, args: dict[str, Any]) -> JsonObjectType:
        note = await actions.create_note(self.data, args["text"], args.get("title"), args.get("tags"))
        return {"saved": True, "id": note["id"]}


class AddTaskTool(_SorendaTool):
    name = "sorenda_add_task"
    description = (
        "Add a task to Sorenda. due is a day (YYYY-MM-DD) for 'by Friday', or a moment "
        "(ISO 8601 with offset) for 'tomorrow at 18'. remind_minutes_before needs due."
    )
    annotations = _CREATE
    parameters = vol.Schema(
        {
            vol.Required("text", description="What to do, in the user's words"): str,
            vol.Optional("due", description="Deadline: YYYY-MM-DD or ISO 8601 with offset"): str,
            vol.Optional(
                "remind_minutes_before", description="Remind this many minutes before due"
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=525600)),
        }
    )

    async def _run(self, args: dict[str, Any]) -> JsonObjectType:
        task = await actions.add_task(
            self.data, args["text"], args.get("due"), args.get("remind_minutes_before")
        )
        return {"added": True, "due": task["due"]}


class AddReminderTool(_SorendaTool):
    name = "sorenda_add_reminder"
    description = (
        "Remind the user at a moment. It is spoken at home and shown on their phone. "
        "at is ISO 8601 with offset; compute it from the current time for 'in 20 minutes'."
    )
    annotations = _CREATE
    parameters = vol.Schema(
        {
            vol.Required("text", description="What to remind about, in the user's words"): str,
            vol.Required("at", description="When: ISO 8601 with offset"): str,
        }
    )

    async def _run(self, args: dict[str, Any]) -> JsonObjectType:
        reminder = await actions.add_reminder(self.data, args["text"], args["at"])
        return {"scheduled": True, "at": reminder["at"]}


class ListTasksTool(_SorendaTool):
    name = "sorenda_list_tasks"
    description = "List the user's open Sorenda tasks: due today, this week, overdue, or all."
    annotations = _READ
    parameters = vol.Schema(
        {
            vol.Optional("range", default="today", description="today, week, overdue or all"): vol.In(
                ["today", "week", "overdue", "all"]
            )
        }
    )

    async def _run(self, args: dict[str, Any]) -> JsonObjectType:
        window = args.get("range", "today")
        result = await self.data.client.list_tasks(
            dt_util.now().date().isoformat(),
            due="any" if window == "all" else window,
            limit=7,
        )
        tasks = [{"text": t.get("text"), "due": t.get("due")} for t in result.get("tasks") or []]
        return {"tasks": tasks, "counts": result.get("counts") or {}}


class CompleteTaskTool(_SorendaTool):
    name = "sorenda_complete_task"
    description = (
        "Mark an open Sorenda task done, found by its wording. If several match, "
        "the result lists them: ask the user which one."
    )
    annotations = _TICK
    parameters = vol.Schema(
        {vol.Required("query", description="The task as the user named it"): str}
    )

    async def _run(self, args: dict[str, Any]) -> JsonObjectType:
        await self.data.tasks.async_refresh()
        matches = match_tasks(args["query"], self.data.tasks.data or [])
        if not matches:
            return {"done": False, "reason": "no open task matches"}
        if len(matches) > 1:
            return {"done": False, "ambiguous": [t.get("text") for t in matches[:5]]}
        task = matches[0]
        await self.data.client.complete_task(task["noteId"], task["taskId"], task.get("due") or "")
        await self.data.tasks.async_request_refresh()
        return {"done": True, "task": task.get("text")}


class SearchNotesTool(_SorendaTool):
    name = "sorenda_search_notes"
    description = "Find what the user saved in Sorenda about something. Returns up to 3 notes."
    annotations = _READ
    parameters = vol.Schema(
        {vol.Required("query", description="What to look for, a few words"): str}
    )

    async def _run(self, args: dict[str, Any]) -> JsonObjectType:
        notes = await self.data.client.search_notes(args["query"], limit=3)
        return {
            "notes": [
                {
                    "title": n.get("title") or "",
                    "text": (n.get("preview") or n.get("text") or "")[:400],
                }
                for n in notes
            ]
        }
