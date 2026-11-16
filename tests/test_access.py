"""Which account a request gets: by voice, by HA user, failing closed."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util

from custom_components.sorenda.const import DOMAIN

from .conftest import URL

SENSOR = "sensor.voice_speaker"


@pytest.fixture
async def accounts(hass: HomeAssistant, sorenda_api):
    """Anna (voice + HA user) and Alyona (voice only), two Sorenda keys."""
    sorenda_api.post(f"{URL}/api/notes", status=201, json={"id": "n9"})
    anna = MockConfigEntry(
        domain=DOMAIN,
        title="Анна",
        unique_id="anna",
        data={"url": URL, "api_key": "srnd_anna"},
        options={"speakers": ["Анна"], "ha_users": ["user-anna"]},
    )
    alena = MockConfigEntry(
        domain=DOMAIN,
        title="Алёна",
        unique_id="alena",
        data={"url": URL, "api_key": "srnd_alena"},
        options={"speakers": ["Алёна"]},
    )
    anna.add_to_hass(hass)
    alena.add_to_hass(hass)
    with patch(
        "custom_components.sorenda.reminders.ReminderScheduler._async_audio_url",
        return_value=None,
    ):
        assert await hass.config_entries.async_setup(anna.entry_id)
        await hass.async_block_till_done()
    return anna, alena


def _speaker(hass: HomeAssistant, name: str, age: timedelta = timedelta(0)) -> None:
    hass.states.async_set(SENSOR, name, {"at": (dt_util.utcnow() - age).isoformat()})


def _ctx(device: str | None = "voice-pe", user: str | None = None) -> llm.LLMContext:
    return llm.LLMContext(
        platform="test",
        context=Context(user_id=user),
        language="ru",
        assistant="conversation",
        device_id=device,
    )


async def _instance(hass: HomeAssistant, ctx: llm.LLMContext) -> llm.APIInstance:
    return await llm.async_get_api(hass, DOMAIN, ctx)


async def _note_key(hass, sorenda_api, ctx) -> str:
    instance = await _instance(hass, ctx)
    result = await instance.async_call_tool(
        llm.ToolInput(tool_name="sorenda_create_note", tool_args={"text": "код 1234"})
    )
    assert result.data == {"saved": True, "id": "n9"}
    call = [c for c in sorenda_api.mock_calls if c[0] == "POST"][-1]
    return call[3]["Authorization"]


async def test_voice_picks_account(hass, accounts, sorenda_api) -> None:
    _speaker(hass, "Анна")
    assert await _note_key(hass, sorenda_api, _ctx()) == "Bearer srnd_anna"
    _speaker(hass, "алена")  # case and ё do not matter
    assert await _note_key(hass, sorenda_api, _ctx()) == "Bearer srnd_alena"


@pytest.mark.parametrize(
    ("name", "age"),
    [
        ("unknown", timedelta(0)),
        ("Гость", timedelta(0)),
        ("Анна", timedelta(minutes=2)),  # stale: the last speaker, not this one
    ],
)
async def test_voice_fails_closed(hass, accounts, name, age) -> None:
    _speaker(hass, name, age)
    instance = await _instance(hass, _ctx())
    assert instance.tools == []
    assert "not available" in instance.api_prompt


async def test_no_sensor_fails_closed(hass, accounts) -> None:
    instance = await _instance(hass, _ctx())
    assert instance.tools == []


async def test_typed_chat_uses_ha_user(hass, accounts, sorenda_api) -> None:
    # A fresh voice reading must not leak into a typed request.
    _speaker(hass, "Алёна")
    assert await _note_key(hass, sorenda_api, _ctx(device=None, user="user-anna")) == "Bearer srnd_anna"
    instance = await _instance(hass, _ctx(device=None, user="someone-else"))
    assert instance.tools == []


async def test_speaker_change_blocks_tool(hass, accounts, sorenda_api) -> None:
    _speaker(hass, "Анна")
    instance = await _instance(hass, _ctx())
    _speaker(hass, "Гость")
    result = await instance.async_call_tool(
        llm.ToolInput(tool_name="sorenda_create_note", tool_args={"text": "x"})
    )
    assert result.error
    assert result.data["error"].startswith("not allowed")
    assert not [c for c in sorenda_api.mock_calls if c[0] == "POST"]


async def test_open_account_takes_the_rest(hass, accounts, sorenda_api) -> None:
    anna, alena = accounts
    hass.config_entries.async_update_entry(alena, options={})
    await hass.async_block_till_done()
    _speaker(hass, "Гость")
    assert await _note_key(hass, sorenda_api, _ctx()) == "Bearer srnd_alena"
    _speaker(hass, "Анна")
    assert await _note_key(hass, sorenda_api, _ctx()) == "Bearer srnd_anna"


async def test_service_account(hass, accounts, sorenda_api) -> None:
    await hass.services.async_call(
        DOMAIN, "create_note", {"account": "Алёна", "text": "x"}, blocking=True
    )
    call = [c for c in sorenda_api.mock_calls if c[0] == "POST"][-1]
    assert call[3]["Authorization"] == "Bearer srnd_alena"


async def test_options_flow_saves_access(hass, sorenda_api, config_entry) -> None:
    config_entry.add_to_hass(hass)
    with patch(
        "custom_components.sorenda.reminders.ReminderScheduler._async_audio_url",
        return_value=None,
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        result = await hass.config_entries.options.async_init(config_entry.entry_id)
        assert result["step_id"] == "init"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"speakers": [" Анна ", ""], "ha_users": [], "speaker_entity": SENSOR},
        )
        await hass.async_block_till_done()
    assert config_entry.options["speakers"] == ["Анна"]
