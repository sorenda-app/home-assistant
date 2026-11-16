"""Time and text helpers shared by the services, the to-do list and the LLM tools."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any

from homeassistant.util import dt as dt_util

# A moment this far in the past is still accepted: the user said "in a minute"
# and the model rounded down. Anything older is a mistake, not a reminder.
PAST_SLACK = timedelta(minutes=2)


class TimeError(ValueError):
    """A time the user gave cannot be used."""


def parse_moment(raw: str) -> datetime:
    """Parse an ISO 8601 moment; a naive one is taken in HA's own time zone."""
    parsed = dt_util.parse_datetime(raw.strip())
    if parsed is None:
        raise TimeError(f"not a date and time: {raw!r}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.get_default_time_zone())
    return parsed


def future_moment(raw: str) -> str:
    """Return a moment as RFC 3339 with the local offset, refusing the past."""
    moment = parse_moment(raw)
    if moment < dt_util.utcnow() - PAST_SLACK:
        raise TimeError(f"{raw} is in the past")
    return format_moment(moment)


def format_moment(moment: datetime) -> str:
    """Return RFC 3339 with seconds and the local offset, as Sorenda stores it."""
    return dt_util.as_local(moment).replace(microsecond=0).isoformat()


def normalize_due(raw: str | None) -> str | None:
    """Accept a day (YYYY-MM-DD) or a moment; return Sorenda's due string."""
    if raw is None or not raw.strip():
        return None
    raw = raw.strip()
    if len(raw) == 10:
        day = dt_util.parse_date(raw)
        if day is None:
            raise TimeError(f"not a date: {raw!r}")
        if day < dt_util.now().date():
            raise TimeError(f"{raw} is in the past")
        return day.isoformat()
    return future_moment(raw)


def due_value(due: str | None) -> date | datetime | None:
    """Turn Sorenda's due string into a date (whole day) or an aware datetime."""
    if not due:
        return None
    if len(due) == 10:
        return dt_util.parse_date(due)
    moment = dt_util.parse_datetime(due)
    return dt_util.as_local(moment) if moment else None


def due_string(value: date | datetime | None) -> str | None:
    """Turn a to-do item's due back into Sorenda's due string."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return format_moment(value)
    return value.isoformat()


def today_local() -> str:
    """Return the user's date, YYYY-MM-DD."""
    return dt_util.now().date().isoformat()


def match_tasks(query: str, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the tasks a spoken name most likely means, best first.

    A substring hit wins outright. Otherwise each spoken word is matched to
    its closest word of the task and the ratios are averaged, so "called
    mum" finds "Call mum about the weekend" while a sentence that only shares
    a look-alike word does not. Russian ё and е count as one letter.
    """
    q = _norm(query)
    if not q:
        return []
    scored: list[tuple[float, dict[str, Any]]] = []
    for task in tasks:
        text = _norm(task.get("text", ""))
        if not text:
            continue
        if q in text:
            score = 1.0
        else:
            task_words = text.split()
            per_word = [
                max(SequenceMatcher(None, qw, tw).ratio() for tw in task_words)
                for qw in q.split()
            ]
            score = sum(per_word) / len(per_word)
        if score >= 0.7:
            scored.append((score, task))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    if len(scored) >= 2 and scored[0][0] - scored[1][0] >= 0.15:
        return [scored[0][1]]
    return [task for _, task in scored]


def _norm(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").split())
