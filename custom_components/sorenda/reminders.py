"""Speaks Sorenda reminders at home.

Sorenda keeps no delivery record: its apps schedule reminders themselves as
local notifications, and the server only lists upcoming and missed fires
(GET /api/notes/reminders). Home Assistant becomes one more such client: it
polls that list every minute, sets a timer per fire, and at the moment fires
the `sorenda_reminder` event with a ready TTS audio URL. An automation turns
the event into speech on a speaker (see the blueprint in blueprints/).

Each fire is remembered in a Store by (note, reminder, moment), so a restart
neither repeats a reminder nor loses one: what HA slept through comes back
through `since` and is spoken if it is at most MISSED_GRACE old.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.components import media_source
from homeassistant.components.media_player import async_process_play_media_url
from homeassistant.components.tts.media_source import generate_media_source_id
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .api import SorendaClient, SorendaError
from .const import (
    CONF_TTS_ENGINE,
    DEFAULT_TTS_ENGINE,
    DOMAIN,
    EVENT_REMINDER,
    MISSED_GRACE,
    REMINDERS_INTERVAL,
    SIGNAL_REMINDERS_UPDATED,
)
from .speech import reminder_message

_LOGGER = logging.getLogger(__name__)
_STORE_VERSION = 1
_KEEP_FIRED = timedelta(days=3)


def fire_key(fire: dict[str, Any]) -> str:
    """Identify one fire: a series shares its reminder id, the moment tells them apart."""
    return f"{fire.get('noteId')}:{fire.get('reminderId')}:{fire.get('fireAt')}"


class ReminderScheduler:
    """Polls Sorenda for reminders and fires an HA event at each one's moment."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: SorendaClient) -> None:
        self.hass = hass
        self.entry = entry
        self.client = client
        self._store: Store[dict[str, Any]] = Store(
            hass, _STORE_VERSION, f"{DOMAIN}.{entry.entry_id}.reminders"
        )
        self._fired: dict[str, str] = {}
        self._last_poll: datetime | None = None
        self._etag: str | None = None
        self._timers: dict[str, Callable[[], None]] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        self._unsub_poll: Callable[[], None] | None = None

    @property
    def next_fire(self) -> dict[str, Any] | None:
        """Return the soonest scheduled fire, if any."""
        if not self._pending:
            return None
        return min(self._pending.values(), key=lambda f: f["fireAt"])

    async def async_start(self) -> None:
        """Load what was already spoken, poll now and every minute after."""
        data = await self._store.async_load() or {}
        self._fired = dict(data.get("fired") or {})
        if raw := data.get("last_poll"):
            self._last_poll = dt_util.parse_datetime(raw)
        await self.async_poll()
        self._unsub_poll = async_track_time_interval(
            self.hass, self._async_poll_tick, REMINDERS_INTERVAL
        )

    @callback
    def async_stop(self) -> None:
        """Cancel the poll and every timer."""
        if self._unsub_poll:
            self._unsub_poll()
            self._unsub_poll = None
        for cancel in self._timers.values():
            cancel()
        self._timers.clear()
        self._pending.clear()

    async def _async_poll_tick(self, _now: datetime) -> None:
        await self.async_poll()

    async def async_poll(self) -> None:
        """Ask Sorenda what fires in the next hour and what was missed."""
        now = dt_util.utcnow()
        floor = now - MISSED_GRACE
        since = max(self._last_poll, floor) if self._last_poll else floor
        try:
            fires, self._etag = await self.client.reminders(
                now=_iso(now),
                since=_iso(since),
                tz=str(self.hass.config.time_zone),
                etag=self._etag,
            )
        except SorendaError as err:
            _LOGGER.warning("Sorenda reminders poll failed: %s", err)
            return
        self._last_poll = now
        if fires is not None:
            self._apply(fires, now)
        self._prune(now)
        self._save()

    def _apply(self, fires: list[dict[str, Any]], now: datetime) -> None:
        seen: set[str] = set()
        for fire in fires:
            key = fire_key(fire)
            seen.add(key)
            if key in self._fired:
                continue
            at = dt_util.parse_datetime(fire.get("fireAt") or "")
            if at is None:
                continue
            if at <= now:
                if now - at <= MISSED_GRACE:
                    self.hass.async_create_task(self._async_fire(key, fire))
                else:
                    self._fired[key] = _iso(now)
                continue
            self._pending[key] = fire
            if key not in self._timers:
                self._timers[key] = async_track_point_in_utc_time(
                    self.hass, self._make_timer(key), at
                )
        # A reminder ticked, deleted or moved since the last poll no longer
        # comes back: its timer must not speak.
        for key in list(self._timers):
            if key not in seen:
                self._timers.pop(key)()
                self._pending.pop(key, None)
        async_dispatcher_send(self.hass, SIGNAL_REMINDERS_UPDATED.format(self.entry.entry_id))

    def _make_timer(self, key: str) -> Callable[[datetime], Any]:
        async def _on_time(_when: datetime) -> None:
            self._timers.pop(key, None)
            fire = self._pending.pop(key, None)
            if fire is not None:
                await self._async_fire(key, fire)
            async_dispatcher_send(
                self.hass, SIGNAL_REMINDERS_UPDATED.format(self.entry.entry_id)
            )

        return _on_time

    async def _async_fire(self, key: str, fire: dict[str, Any]) -> None:
        if key in self._fired:
            return
        self._fired[key] = _iso(dt_util.utcnow())
        # Written at once, not delayed: a reload or crash right after must not
        # speak the same reminder twice.
        await self._store.async_save(self._snapshot())
        language = self.hass.config.language or "en"
        message = reminder_message(fire, language)
        audio_url = await self._async_audio_url(message, language)
        _LOGGER.debug("Sorenda reminder: %s (%s)", message, audio_url)
        self.hass.bus.async_fire(
            EVENT_REMINDER,
            {
                "note_id": fire.get("noteId"),
                "task_id": fire.get("taskId"),
                "reminder_id": fire.get("reminderId"),
                "title": fire.get("title") or "",
                "text": fire.get("text") or "",
                "fire_at": fire.get("fireAt"),
                "missed": bool(fire.get("missed")),
                "message": message,
                "audio_url": audio_url,
                "url": self.client.note_url(str(fire.get("noteId"))),
                "account": self.entry.title,
                "entry_id": self.entry.entry_id,
            },
        )

    async def _async_audio_url(self, message: str, language: str) -> str | None:
        """Render the message to MP3 (every speaker plays it) and return its URL."""
        engine = self.entry.options.get(CONF_TTS_ENGINE, DEFAULT_TTS_ENGINE)
        try:
            media_id = generate_media_source_id(
                self.hass,
                message,
                engine=engine or None,
                language=language,
                options={"preferred_format": "mp3"},
            )
            media = await media_source.async_resolve_media(self.hass, media_id, None)
        except Exception as err:  # noqa: BLE001 - speech is best effort
            _LOGGER.warning("Sorenda reminder TTS failed (%s): %s", engine, err)
            return None
        return async_process_play_media_url(self.hass, media.url)

    def _prune(self, now: datetime) -> None:
        cutoff = now - _KEEP_FIRED
        for key, when in list(self._fired.items()):
            moment = dt_util.parse_datetime(when)
            if moment is None or moment < cutoff:
                del self._fired[key]

    def _snapshot(self) -> dict[str, Any]:
        return {
            "fired": dict(self._fired),
            "last_poll": _iso(self._last_poll) if self._last_poll else None,
        }

    def _save(self) -> None:
        self._store.async_delay_save(self._snapshot, 5)


def _iso(moment: datetime) -> str:
    return moment.astimezone(dt_util.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
