"""Constants for the Sorenda integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "sorenda"

CONF_TTS_ENGINE: Final = "tts_engine"
DEFAULT_URL: Final = "https://sorenda.app"
# Empty: Home Assistant's default text-to-speech engine.
DEFAULT_TTS_ENGINE: Final = ""

TASKS_INTERVAL: Final = timedelta(minutes=5)
REMINDERS_INTERVAL: Final = timedelta(minutes=1)
# A reminder HA slept through is still spoken if it is at most this old.
# Older ones are dropped: "drink your pills" two hours late helps nobody.
MISSED_GRACE: Final = timedelta(minutes=10)

EVENT_REMINDER: Final = "sorenda_reminder"
SIGNAL_REMINDERS_UPDATED: Final = "sorenda_reminders_updated_{}"

SERVICE_CREATE_NOTE: Final = "create_note"
SERVICE_ADD_TASK: Final = "add_task"
SERVICE_ADD_REMINDER: Final = "add_reminder"

# Who may use an account. Empty lists leave the account open to everyone.
CONF_SPEAKERS: Final = "speakers"
CONF_HA_USERS: Final = "ha_users"
CONF_SPEAKER_ENTITY: Final = "speaker_entity"
DEFAULT_SPEAKER_ENTITY: Final = "sensor.voice_speaker"
# A speaker older than this is not the one talking now: the recogniser keeps
# the last name when nothing was said, and its write to HA may have failed.
SPEAKER_MAX_AGE: Final = timedelta(seconds=30)
UNKNOWN_SPEAKERS: Final = frozenset({"", "unknown", "unavailable", "none"})

ATTR_ACCOUNT: Final = "account"
