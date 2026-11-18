# Sorenda for Home Assistant

Save notes, add tasks and set reminders in [Sorenda](https://sorenda.app) by voice, through your Home Assistant voice assistant. Reminders are spoken at home on a speaker.

> «Напомни через 20 минут проверить духовку» · "Remind me tomorrow at 9 to call mum" · «Что у меня на сегодня?»

## What you get

- **LLM tools** for any LLM conversation agent (OpenAI, Anthropic, Google, OpenRouter, Ollama…):
  - `sorenda_create_note`: "write down that the door code is 1234"
  - `sorenda_add_task`: "add a task to buy batteries by Friday"
  - `sorenda_add_reminder`: "remind me in 20 minutes…", "…tomorrow at 9"
  - `sorenda_list_tasks`: "what do I have today?"
  - `sorenda_complete_task`: "mark *call mum* done". If several tasks match, the assistant asks which one.
  - `sorenda_search_notes`: "what did I note about the door code?"
  - `sorenda_list_cards`: "what subscriptions do I have?", "when does iCloud renew?", "where is my Ozon order?"

  Nothing here deletes or rewrites a note.
- **To-do list** `todo.sorenda_tasks` with your open tasks. Home Assistant's built-in list intents work on it too ("add batteries to Sorenda"), even without an LLM.
- **Spoken reminders.** At the moment a reminder fires, the integration renders it to speech and fires the `sorenda_reminder` event. A bundled blueprint plays it on a speaker as an announcement. Your phone still gets the usual Sorenda notification.
- **Sensor** `sensor.sorenda_next_reminder`: when the next reminder fires and what it says.
- **Actions** `sorenda.create_note`, `sorenda.add_task`, `sorenda.add_reminder` for your own automations. Each returns the note's `id` and `url`.

## Languages

The integration's screens and actions are in English, Russian, Spanish, German, Japanese and Chinese (Simplified), following Home Assistant's language. Reminders are spoken in those six languages too; any other Home Assistant language falls back to English. The voice tools work in whatever language the assistant is spoken to.

## Requirements

- Home Assistant 2026.10 or newer.
- A Sorenda account on a plan with API keys (Plus or Pro).
- For voice: an assist pipeline with an LLM conversation agent.

## Install

**Marketplace** (Home Assistant 2026.11+): Settings → Marketplace → ⋮ → Custom repositories → add `https://github.com/sorenda-app/home-assistant` as an *Integration* → install **Sorenda** → restart Home Assistant.

**By hand:** download `sorenda.zip` from the [latest release](https://github.com/sorenda-app/home-assistant/releases/latest) and unpack it into `/config/custom_components/sorenda/`, so that `manifest.json` ends up at `/config/custom_components/sorenda/manifest.json`. Then restart Home Assistant.

## Set up

1. In Sorenda go to Settings → API Keys and create a key with the **read** and **write** scopes.
2. In Home Assistant go to Settings → Devices & services → Add integration → **Sorenda**. Keep the address `https://sorenda.app` and paste the key.
3. Turn on the tools for the voice assistant: Settings → Voice assistants → your assistant → the ⚙ next to the conversation agent → **Control Home Assistant** → tick **Sorenda** (keep **Assist** ticked too).
4. Spoken reminders: Settings → Automations → Blueprints → **Sorenda — speak reminders** → create an automation and pick a speaker. The blueprint is copied to `/config/blueprints/automation/sorenda/` when the integration is set up. It has quiet hours (23:00–08:00 by default).
5. Optional: the Sorenda integration's ⚙ → choose which text-to-speech engine speaks reminders. If you leave it empty, Home Assistant's default engine is used.

## Several people, several accounts

Sorenda can follow who is speaking. If a speaker-recognition setup writes the speaker's name to a sensor, Sorenda reads it. The default sensor is `sensor.voice_speaker`; it should hold a name or `unknown`, plus an `at` timestamp of the recognition.

- Add the integration once per Sorenda account, each with its own API key and a name.
- In each account's options (⚙), list its **Voices**, written exactly as the sensor names them, and optionally the **Home Assistant users** who may use it from the typed chat.
- A request from a voice satellite goes to the account whose list holds the speaker. A typed request goes to the account that lists the Home Assistant user. An account that lists nobody takes everything else. If no account fits, the assistant gets no Sorenda tools and says Sorenda is not available for that voice.
- Access fails closed: Sorenda stays locked if the speaker is `unknown`, the sensor is missing, or the reading is older than 30 seconds. A recogniser that keeps the last name, or fails to update it, never opens someone else's notes.
- The account is checked again before every action, so a follow-up turn by another person cannot use the first person's tools.
- One account, one voice, nothing else: Sorenda works only for that person.

Voice is not a password: a recording or a very similar voice passes. Home Assistant's built-in list intents don't check the voice either, so don't expose the Sorenda to-do list to Assist if it must stay private. Spoken reminders carry `account` and `entry_id`; the blueprint can limit a speaker to one account. The actions take an optional `account`.

## How it works

- Requests go to Sorenda's REST API with your personal key, plus Home Assistant's time zone, so "today" and "at 9" mean your own day. Every create carries a `clientId`, so a retried request never saves a note twice.
- The LLM gets the current local time in its prompt and passes moments as ISO 8601 with your offset. The integration refuses moments in the past.
- Sorenda's own apps show reminders as local notifications, and the server does not push them anywhere. Home Assistant acts as one more such client:
  - Every minute it asks for the reminders of the next hour (with an `ETag`, so an unchanged list costs a 304) and sets a timer for each.
  - What was already spoken is remembered in `.storage`, so a restart neither repeats a reminder nor loses one.
  - A reminder missed while Home Assistant was down is still spoken if it is at most 10 minutes late.
- Reminder audio is MP3, so it plays on any speaker.

### The `sorenda_reminder` event

| Field | |
|---|---|
| `message` | the sentence spoken, e.g. «Напоминание: проверить духовку.» |
| `audio_url` | MP3 of `message`; empty if text-to-speech failed |
| `text`, `title` | what the reminder is about |
| `fire_at`, `missed` | when it was due; `missed` if Home Assistant was down at that moment |
| `note_id`, `task_id`, `reminder_id`, `url` | where it lives in Sorenda |
| `account`, `entry_id` | which account (its name in Home Assistant) |

You can use it for things other than speech, for example a notification to a TV or a light that flashes.

## Development

```bash
python3.14 -m venv .venv && .venv/bin/pip install -r requirements_test.txt
cd tests && ../.venv/bin/pytest -c pytest.ini -q
./scripts/build.sh          # dist/sorenda.zip
```

The integration is developed inside the Sorenda monorepo and published here by hand on each release, as a single commit with a GitHub release that carries `sorenda.zip`. Issues and changes go through Sorenda rather than pull requests to this repository.
