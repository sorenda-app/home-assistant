"""Which Sorenda account a request may use, by voice or by Home Assistant user.

Every account (config entry) can list the voices allowed to use it, as a
speaker-recognition sensor names them, and the Home Assistant users allowed
to use it from a typed chat. A request from a voice satellite is matched by
the speaker; a request without a device (the Assist dialog in the app) by
the user. An account that lists nobody stays open to everyone.

The rule fails closed: an unknown, missing or stale speaker never matches a
restricted account. Stale matters because a recogniser may keep the last
name when nothing was said, or fail to write the new one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util

from .const import (
    CONF_HA_USERS,
    CONF_SPEAKER_ENTITY,
    CONF_SPEAKERS,
    DEFAULT_SPEAKER_ENTITY,
    DOMAIN,
    SPEAKER_MAX_AGE,
    UNKNOWN_SPEAKERS,
)


@dataclass(frozen=True)
class Access:
    """The account a request resolved to, or why there is none."""

    entry: ConfigEntry | None
    speaker: str | None = None
    reason: str = ""


def is_restricted(entry: ConfigEntry) -> bool:
    """Return whether the account lists who may use it."""
    return bool(entry.options.get(CONF_SPEAKERS) or entry.options.get(CONF_HA_USERS))


def current_speaker(hass: HomeAssistant, entity_id: str) -> str | None:
    """Return who is talking now, or None if the sensor cannot say."""
    state = hass.states.get(entity_id)
    if state is None or _norm(state.state) in UNKNOWN_SPEAKERS:
        return None
    if dt_util.utcnow() - _heard_at(state) > SPEAKER_MAX_AGE:
        return None
    return state.state


def _heard_at(state: State) -> datetime:
    """When the speaker was recognised: the sensor's `at`, else its last write."""
    raw = state.attributes.get("at")
    if isinstance(raw, str) and (parsed := dt_util.parse_datetime(raw)) is not None:
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt_util.UTC)
    return max(state.last_updated, getattr(state, "last_reported", state.last_updated))


def _norm(name: str) -> str:
    return " ".join(name.lower().replace("ё", "е").split())


def resolve(hass: HomeAssistant, llm_context: llm.LLMContext | None) -> Access:
    """Pick the account for an LLM request."""
    entries = list(hass.config_entries.async_loaded_entries(DOMAIN))
    if not entries:
        return Access(None, reason="Sorenda is not set up")

    from_satellite = llm_context is not None and llm_context.device_id is not None
    user_id = (
        llm_context.context.user_id
        if llm_context is not None and llm_context.context is not None
        else None
    )

    speaker: str | None = None
    if from_satellite:
        # Each account may name its own sensor; the usual setup has one.
        for entity_id in dict.fromkeys(
            e.options.get(CONF_SPEAKER_ENTITY) or DEFAULT_SPEAKER_ENTITY for e in entries
        ):
            if (speaker := current_speaker(hass, entity_id)) is not None:
                break
        if speaker is not None:
            for entry in entries:
                names = {_norm(n) for n in entry.options.get(CONF_SPEAKERS) or []}
                if _norm(speaker) in names:
                    return Access(entry, speaker=speaker)
    elif user_id is not None:
        for entry in entries:
            if user_id in (entry.options.get(CONF_HA_USERS) or []):
                return Access(entry)

    for entry in entries:
        if not is_restricted(entry):
            return Access(entry, speaker=speaker)

    if from_satellite:
        who = speaker or "an unrecognised voice"
        return Access(None, speaker=speaker, reason=f"Sorenda is not available to {who}")
    return Access(None, reason="Sorenda is not available to this user")
