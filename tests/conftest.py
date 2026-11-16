"""Fixtures: a fake Sorenda backend behind HA's aiohttp mocker."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

# custom_components/ sits next to tests/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.sorenda.const import DOMAIN  # noqa: E402

URL = "https://sorenda.test"
KEY = "srnd_testkey"

TASKS = [
    {
        "noteId": "n1",
        "taskId": "aaaaaa",
        "text": "Позвонить маме насчёт выходных",
        "status": "todo",
        "due": "2026-11-17",
        "tags": [],
        "version": 1,
    },
    {
        "noteId": "n2",
        "taskId": "bbbbbb",
        "text": "Проверить духовку",
        "status": "todo",
        "due": "2026-11-17T09:00:00+03:00",
        "tags": [],
        "version": 1,
    },
]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/ in every test."""
    return


@pytest.fixture(autouse=True)
def private_config_dir(hass, tmp_path):
    """Give each test its own /config, so installed blueprints don't leak."""
    hass.config.config_dir = str(tmp_path)


@pytest.fixture
def sorenda_api(aioclient_mock):
    """Answer the endpoints the integration calls at setup."""
    aioclient_mock.get(f"{URL}/api/auth/token-info", json={"scopes": ["read", "write"]})
    aioclient_mock.get(f"{URL}/api/notes/tasks", json={"tasks": TASKS, "counts": {}})
    aioclient_mock.get(f"{URL}/api/notes/reminders", json={"fires": []})
    return aioclient_mock


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """A configured account."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Sorenda",
        unique_id="abc",
        data={"url": URL, "api_key": KEY},
        options={"tts_engine": ""},
    )
