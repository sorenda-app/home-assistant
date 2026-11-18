"""A small client for the Sorenda REST API.

Only what the voice assistant needs: create notes, tasks and reminders, read
open tasks and upcoming reminders, tick and move a task. Every call carries
the personal API key and Home Assistant's time zone, so "today" and timed
deadlines mean the user's own day.
"""

from __future__ import annotations

import secrets
import string
from typing import Any
import uuid

import aiohttp

USER_AGENT = "sorenda-homeassistant/0.1"
_TASK_ID_ALPHABET = string.ascii_lowercase + string.digits
_TIMEOUT = aiohttp.ClientTimeout(total=20)


class SorendaError(Exception):
    """Sorenda could not be reached or refused the request."""


class SorendaAuthError(SorendaError):
    """The API key is wrong or revoked."""


class SorendaSubscriptionError(SorendaError):
    """Writing needs an active Sorenda subscription (Plus/Pro)."""


def new_task_id() -> str:
    """Return a task line id in Sorenda's own format (6 chars, a-z0-9)."""
    return "".join(secrets.choice(_TASK_ID_ALPHABET) for _ in range(6))


class SorendaClient:
    """Talks to one Sorenda account through a personal API key."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        api_key: str,
        time_zone: str,
    ) -> None:
        self._session = session
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._tz = time_zone

    @property
    def base_url(self) -> str:
        """Return the base URL, without a trailing slash."""
        return self._base

    def note_url(self, note_id: str) -> str:
        """Return the web address of a note."""
        return f"{self._base}/app/?note={note_id}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        etag: str | None = None,
    ) -> tuple[int, Any, str | None]:
        headers = {
            "Authorization": f"Bearer {self._key}",
            "X-Sorenda-Time-Zone": self._tz,
            "User-Agent": USER_AGENT,
        }
        if etag:
            headers["If-None-Match"] = etag
        try:
            async with self._session.request(
                method,
                f"{self._base}{path}",
                params=params,
                json=json,
                headers=headers,
                timeout=_TIMEOUT,
            ) as resp:
                if resp.status == 304:
                    return 304, None, etag
                try:
                    body = await resp.json(content_type=None)
                except ValueError:
                    body = None
                if resp.status in (401,):
                    raise SorendaAuthError("invalid API key")
                if resp.status in (402, 403):
                    raise SorendaSubscriptionError(_message(body, resp.status))
                if resp.status >= 400:
                    raise SorendaError(_message(body, resp.status))
                return resp.status, body, resp.headers.get("ETag")
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SorendaError(f"cannot reach Sorenda: {err}") from err

    async def token_info(self) -> list[str]:
        """Return the key's scopes; raises SorendaAuthError for a bad key."""
        _, body, _ = await self._request("GET", "/api/auth/token-info")
        return list((body or {}).get("scopes") or [])

    async def create_note(
        self,
        text: str,
        *,
        title: str | None = None,
        tags: list[str] | None = None,
        reminders: list[dict[str, Any]] | None = None,
        tasks: list[dict[str, Any]] | None = None,
        client_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a note. The clientId makes a retried call return the same note."""
        payload: dict[str, Any] = {
            "text": text,
            "clientId": client_id or f"ha-{uuid.uuid4().hex}",
        }
        if title:
            payload["title"] = title
        if tags:
            payload["tags"] = tags
        if reminders:
            payload["reminders"] = reminders
        if tasks:
            payload["tasks"] = tasks
        _, body, _ = await self._request("POST", "/api/notes", json=payload)
        return body or {}

    async def create_task(
        self,
        text: str,
        *,
        due: str | None = None,
        remind: list[int] | None = None,
        client_id: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Create a note holding one task line; return the note and the task id."""
        task_id = new_task_id()
        line = " ".join(text.split())
        task: dict[str, Any] = {"id": task_id, "status": "todo"}
        if due:
            task["due"] = due
        if remind:
            task["remind"] = remind
        note = await self.create_note(
            f"- [ ] {line} <!--t:{task_id}-->", tasks=[task], client_id=client_id
        )
        return note, task_id

    async def list_tasks(
        self, today: str, *, due: str = "any", limit: int = 100
    ) -> dict[str, Any]:
        """Return open tasks: {"tasks": [...], "counts": {...}}."""
        _, body, _ = await self._request(
            "GET",
            "/api/notes/tasks",
            params={"status": "open", "due": due, "today": today, "limit": limit},
        )
        return body or {"tasks": []}

    async def complete_task(
        self, note_id: str, task_id: str, occurrence_due: str
    ) -> dict[str, Any]:
        """Tick a task. occurrence_due is the due it had ("" if none): a repeat-safe key."""
        _, body, _ = await self._request(
            "POST",
            f"/api/notes/{note_id}/tasks/{task_id}/complete",
            json={"occurrenceDue": occurrence_due},
        )
        return body or {}

    async def patch_task(
        self, note_id: str, task_id: str, patch: dict[str, Any]
    ) -> dict[str, Any]:
        """Merge a patch over a task; a key set to None clears it."""
        _, body, _ = await self._request(
            "PATCH", f"/api/notes/{note_id}/tasks/{task_id}", json={"patch": patch}
        )
        return body or {}

    async def reminders(
        self, *, now: str, since: str, tz: str, within: str = "1h", etag: str | None = None
    ) -> tuple[list[dict[str, Any]] | None, str | None]:
        """Return (fires, etag); fires is None when nothing changed since etag."""
        status, body, new_etag = await self._request(
            "GET",
            "/api/notes/reminders",
            params={"now": now, "since": since, "within": within, "tz": tz},
            etag=etag,
        )
        if status == 304:
            return None, etag
        return list((body or {}).get("fires") or []), new_etag

    async def search_notes(self, query: str, *, limit: int = 3) -> list[dict[str, Any]]:
        """Return notes matching a query, as previews.

        anyWords: a spoken query is a phrase ("моя подписка на Claude"), and
        with every word required the grammar words alone ("моя", "на") find
        nothing. Any word may match; ranking decides what comes first.
        """
        _, body, _ = await self._request(
            "GET",
            "/api/notes",
            params={"q": query, "limit": limit, "preview": "1", "anyWords": "true"},
        )
        return list((body or {}).get("notes") or [])

    async def list_cards(
        self, note_type: str, query: str = "", *, limit: int = 30
    ) -> list[dict[str, Any]]:
        """Return one type's cards, newest first, narrowed by a query when given."""
        params: dict[str, Any] = {"type": note_type, "limit": limit}
        if query:
            params.update(q=query, anyWords="true")
        _, body, _ = await self._request("GET", "/api/notes", params=params)
        return list((body or {}).get("notes") or [])


def _message(body: Any, status: int) -> str:
    if isinstance(body, dict) and isinstance(body.get("error"), str):
        return body["error"]
    return f"Sorenda returned {status}"
