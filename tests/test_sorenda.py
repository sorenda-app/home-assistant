"""Tests for the Sorenda integration."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_capture_events,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import llm

from custom_components.sorenda.const import DOMAIN, EVENT_REMINDER
from custom_components.sorenda.speech import reminder_message, span
from custom_components.sorenda.util import match_tasks

from .conftest import KEY, TASKS, URL

NOW = "2026-11-16T12:00:00+03:00"


@pytest.fixture(autouse=True)
async def moscow(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Run every test at noon in Moscow."""
    await hass.config.async_set_time_zone("Europe/Moscow")
    hass.config.language = "ru"
    freezer.move_to(NOW)


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    with patch(
        "custom_components.sorenda.reminders.ReminderScheduler._async_audio_url",
        return_value="http://ha.local/tts/x.mp3",
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


def _calls(mock: AiohttpClientMocker, method: str, path: str) -> list:
    return [c for c in mock.mock_calls if c[0].upper() == method and c[1].path == path]


# --- config flow -----------------------------------------------------------


async def test_flow_creates_entry(hass: HomeAssistant, sorenda_api) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch("custom_components.sorenda.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"url": URL + "/", "api_key": KEY}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"url": URL, "api_key": KEY}


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (401, {"error": "unauthorized"}, "invalid_auth"),
        (200, {"scopes": ["read", "create"]}, "missing_scope"),
        (500, {"error": "boom"}, "cannot_connect"),
    ],
)
async def test_flow_errors(hass, aioclient_mock, status, body, error) -> None:
    aioclient_mock.get(f"{URL}/api/auth/token-info", status=status, json=body)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": URL, "api_key": KEY}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


async def test_flow_rejects_non_key(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": URL, "api_key": "eyJhbGciOi"}
    )
    assert result["errors"] == {"api_key": "invalid_format"}


# --- to-do list --------------------------------------------------------------


async def test_todo_lists_and_completes(hass, sorenda_api, config_entry) -> None:
    await _setup(hass, config_entry)
    state = hass.states.get("todo.sorenda_zadachi") or hass.states.get("todo.sorenda_tasks")
    assert state is not None
    assert state.state == "2"

    sorenda_api.post(f"{URL}/api/notes/n2/tasks/bbbbbb/complete", json={})
    await hass.services.async_call(
        "todo",
        "update_item",
        {"item": "Проверить духовку", "status": "completed"},
        target={"entity_id": state.entity_id},
        blocking=True,
    )
    calls = _calls(sorenda_api, "POST", "/api/notes/n2/tasks/bbbbbb/complete")
    assert calls[0][2] == {"occurrenceDue": "2026-11-17T09:00:00+03:00"}


async def test_todo_add_item(hass, sorenda_api, config_entry) -> None:
    await _setup(hass, config_entry)
    entity_id = next(iter(hass.states.async_entity_ids("todo")))
    sorenda_api.post(f"{URL}/api/notes", status=201, json={"id": "n9"})
    await hass.services.async_call(
        "todo",
        "add_item",
        {"item": "Купить батарейки", "due_date": "2026-11-18"},
        target={"entity_id": entity_id},
        blocking=True,
    )
    body = _calls(sorenda_api, "POST", "/api/notes")[0][2]
    task = body["tasks"][0]
    assert body["text"] == f"- [ ] Купить батарейки <!--t:{task['id']}-->"
    assert task["due"] == "2026-11-18"
    assert body["clientId"].startswith("ha-")


# --- LLM tools ---------------------------------------------------------------


async def _tool(hass: HomeAssistant, name: str, args: dict) -> dict:
    instance = await llm.async_get_api(
        hass,
        DOMAIN,
        llm.LLMContext(platform="test", context=None, language="ru", assistant="conversation", device_id=None),
    )
    assert "2026-11-16" in instance.api_prompt
    result = await instance.async_call_tool(llm.ToolInput(tool_name=name, tool_args=args))
    return result.data


async def test_tool_reminder_refuses_past(hass, sorenda_api, config_entry) -> None:
    await _setup(hass, config_entry)
    result = await _tool(
        hass, "sorenda_add_reminder", {"text": "духовка", "at": "2026-11-16T10:00:00+03:00"}
    )
    assert result["error"].startswith("bad time")
    assert not _calls(sorenda_api, "POST", "/api/notes")


async def test_tool_reminder_saves_local_offset(hass, sorenda_api, config_entry) -> None:
    await _setup(hass, config_entry)
    sorenda_api.post(f"{URL}/api/notes", status=201, json={"id": "n9"})
    result = await _tool(
        hass, "sorenda_add_reminder", {"text": "Проверить духовку", "at": "2026-11-16T12:20:00"}
    )
    assert result == {"scheduled": True, "at": "2026-11-16T12:20:00+03:00"}
    body = _calls(sorenda_api, "POST", "/api/notes")[0][2]
    assert body["reminders"] == [{"at": "2026-11-16T12:20:00+03:00"}]


async def test_tool_complete_picks_one(hass, sorenda_api, config_entry) -> None:
    await _setup(hass, config_entry)
    sorenda_api.post(f"{URL}/api/notes/n1/tasks/aaaaaa/complete", json={})
    result = await _tool(hass, "sorenda_complete_task", {"query": "позвонить маме"})
    assert result == {"done": True, "task": "Позвонить маме насчёт выходных"}


async def test_tool_task_remind_needs_due(hass, sorenda_api, config_entry) -> None:
    await _setup(hass, config_entry)
    result = await _tool(
        hass, "sorenda_add_task", {"text": "x", "remind_minutes_before": 30}
    )
    assert "needs a due" in result["error"]


# --- reminders ---------------------------------------------------------------


def _fire(at: str, **extra) -> dict:
    return {
        "noteId": "n5",
        "reminderId": "r1",
        "kind": "at",
        "text": "Проверить духовку",
        "fireAt": at,
        "before": 0,
        "missed": False,
        "version": 1,
        **extra,
    }


async def test_reminder_fires_once_across_restart(
    hass, aioclient_mock, config_entry, freezer
) -> None:
    aioclient_mock.get(f"{URL}/api/auth/token-info", json={"scopes": ["read", "write"]})
    aioclient_mock.get(f"{URL}/api/notes/tasks", json={"tasks": TASKS})
    aioclient_mock.get(
        f"{URL}/api/notes/reminders", json={"fires": [_fire("2026-11-16T09:02:00Z")]}
    )
    events = async_capture_events(hass, EVENT_REMINDER)
    await _setup(hass, config_entry)
    assert not events
    assert hass.states.get(
        next(e for e in hass.states.async_entity_ids("sensor"))
    ).state.startswith("2026-11-16T09:02:00")

    with patch(
        "custom_components.sorenda.reminders.ReminderScheduler._async_audio_url",
        return_value="http://ha.local/tts/x.mp3",
    ):
        freezer.tick(timedelta(minutes=2, seconds=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert len(events) == 1
    data = events[0].data
    assert data["message"] == "Напоминание: Проверить духовку."
    assert data["audio_url"] == "http://ha.local/tts/x.mp3"

    # A restart: the same fire now comes back as missed. It must not speak again.
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(events) == 1


async def test_reminder_missed_long_ago_is_dropped(hass, aioclient_mock, config_entry) -> None:
    aioclient_mock.get(f"{URL}/api/auth/token-info", json={"scopes": ["read", "write"]})
    aioclient_mock.get(f"{URL}/api/notes/tasks", json={"tasks": []})
    aioclient_mock.get(
        f"{URL}/api/notes/reminders",
        json={
            "fires": [
                _fire("2026-11-16T08:30:00Z", missed=True),
                _fire("2026-11-16T08:57:00Z", reminderId="r2", missed=True, kind="before", before=15),
            ]
        },
    )
    events = async_capture_events(hass, EVENT_REMINDER)
    await _setup(hass, config_entry)
    assert [e.data["reminder_id"] for e in events] == ["r2"]
    assert events[0].data["message"] == "Напоминание: Проверить духовку. Через 15 минут."


# --- helpers -----------------------------------------------------------------


def test_match_tasks() -> None:
    assert [t["taskId"] for t in match_tasks("духовку", TASKS)] == ["bbbbbb"]
    assert [t["taskId"] for t in match_tasks("позвонила маме", TASKS)] == ["aaaaaa"]
    assert match_tasks("полить цветы", TASKS) == []


def test_span() -> None:
    assert span(1, "ru") == "1 минуту"
    assert span(15, "ru") == "15 минут"
    assert span(90, "ru") == "1 час 30 минут"
    assert span(1440, "ru") == "1 день"
    assert span(1, "en") == "1 minute"
    assert span(90, "en") == "1 hour 30 minutes"
    assert span(2880, "en") == "2 days"
    assert span(1500, "en") == "25 hours"
    assert span(90, "de") == "1 Stunde 30 Minuten"
    assert span(90, "es") == "1 hora 30 minutos"
    assert span(90, "ja") == "1時間30分"
    assert span(90, "zh") == "1小时30分钟"


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("en", "Reminder: Check the oven. In 15 minutes."),
        ("en-GB", "Reminder: Check the oven. In 15 minutes."),
        ("pt-BR", "Reminder: Check the oven. In 15 minutes."),  # not spoken: English
        (None, "Reminder: Check the oven. In 15 minutes."),
        ("ru", "Напоминание: Check the oven. Через 15 минут."),
        ("es", "Recordatorio: Check the oven. En 15 minutos."),
        ("de", "Erinnerung: Check the oven. In 15 Minuten."),
        ("ja", "リマインダー：Check the oven。15分後です。"),
        ("zh-Hans", "提醒：Check the oven。15分钟后。"),
    ],
)
def test_reminder_message(language, expected) -> None:
    fire = {"kind": "before", "before": 15, "text": "Check the oven"}
    assert reminder_message(fire, language) == expected


def test_reminder_message_at() -> None:
    assert reminder_message({"kind": "at", "text": "Call mum"}, "en") == "Reminder: Call mum."



async def test_setup_installs_blueprint_once(hass, sorenda_api, config_entry) -> None:
    await _setup(hass, config_entry)
    path = Path(hass.config.path("blueprints/automation/sorenda/announce_reminder.yaml"))
    assert path.exists()
    path.write_text("# edited by the user\n")
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert path.read_text() == "# edited by the user\n"


async def test_tool_search_shows_card_data(hass, sorenda_api, config_entry) -> None:
    await _setup(hass, config_entry)
    sorenda_api.get(
        f"{URL}/api/notes",
        json={
            "notes": [
                {
                    "id": "s1",
                    "type": "shopping.subscription",
                    "text": "",
                    "data": {
                        "service": "Claude Code",
                        "plan": "Pro",
                        "price": 20,
                        "currency": "USD",
                        "renewsAt": "2026-10-25",
                    },
                },
                {
                    "id": "o1",
                    "type": "shopping.order",
                    "data": {
                        "merchant": "Ozon",
                        "status": "shipped",
                        "items": [{"name": "Кроссовки", "qty": 1}, {"name": "Носки"}],
                        "meta": {"x": 1},
                    },
                },
                {"id": "n1", "title": "Дача", "text": "Полить грядки"},
            ]
        },
    )
    result = await _tool(hass, "sorenda_search_notes", {"query": "подписка Claude"})
    assert result["notes"] == [
        {
            "title": "",
            "text": "",
            "type": "shopping.subscription",
            "card": "service: Claude Code; plan: Pro; price: 20; currency: USD; renewsAt: 2026-10-25",
        },
        {
            "title": "",
            "text": "",
            "type": "shopping.order",
            "card": "merchant: Ozon; status: shipped; items: Кроссовки, Носки",
        },
        {"title": "Дача", "text": "Полить грядки"},
    ]
    url = _calls(sorenda_api, "GET", "/api/notes")[0][1]
    assert url.query["anyWords"] == "true"
    assert url.query["q"] == "подписка Claude"
