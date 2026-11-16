"""The sentence a reminder is spoken as, in the languages Sorenda speaks.

English is the default; Russian, Spanish, German, Japanese and Chinese
follow Home Assistant's own language. Any other language falls back to
English.
"""

from __future__ import annotations

from typing import Any


def language_of(hass_language: str | None) -> str:
    """Reduce "en-GB", "zh-Hans", "pt_BR" to a language this module speaks."""
    base = (hass_language or "en").replace("_", "-").split("-")[0].lower()
    return base if base in _SPEAKERS else "en"


def reminder_message(fire: dict[str, Any], hass_language: str | None) -> str:
    """Say what a reminder is about, and how soon its deadline is."""
    lang = language_of(hass_language)
    what = (fire.get("text") or fire.get("title") or "").strip()
    before = int(fire.get("before") or 0) if fire.get("kind") == "before" else 0
    return _SPEAKERS[lang](what, before)


def span(minutes: int, lang: str) -> str:
    """A span of time in words: 15 minutes, 1 hour 30 minutes, 2 days."""
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    if days and not rest:
        parts = [(days, "day")]
    else:
        parts = [(n, u) for n, u in ((days * 24 + hours, "hour"), (mins, "minute")) if n]
    words = _UNITS[lang]
    joined = " ".join(words(n, unit) for n, unit in parts or [(0, "minute")])
    return joined.replace(" ", "") if lang in ("ja", "zh") else joined


# --- units -------------------------------------------------------------------


def _en(n: int, unit: str) -> str:
    return f"{n} {unit}{'' if n == 1 else 's'}"


def _es(n: int, unit: str) -> str:
    one, many = {"day": ("día", "días"), "hour": ("hora", "horas"), "minute": ("minuto", "minutos")}[unit]
    return f"{n} {one if n == 1 else many}"


def _de(n: int, unit: str) -> str:
    one, many = {"day": ("Tag", "Tagen"), "hour": ("Stunde", "Stunden"), "minute": ("Minute", "Minuten")}[unit]
    return f"{n} {one if n == 1 else many}"


def _ru(n: int, unit: str) -> str:
    one, few, many = {
        "day": ("день", "дня", "дней"),
        "hour": ("час", "часа", "часов"),
        "minute": ("минуту", "минуты", "минут"),
    }[unit]
    if n % 10 == 1 and n % 100 != 11:
        word = one
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        word = few
    else:
        word = many
    return f"{n} {word}"


def _ja(n: int, unit: str) -> str:
    return f"{n}{ {'day': '日', 'hour': '時間', 'minute': '分'}[unit] }"


def _zh(n: int, unit: str) -> str:
    return f"{n}{ {'day': '天', 'hour': '小时', 'minute': '分钟'}[unit] }"


_UNITS = {"en": _en, "es": _es, "de": _de, "ru": _ru, "ja": _ja, "zh": _zh}


# --- sentences ---------------------------------------------------------------


def _say_en(what: str, before: int) -> str:
    what = what or "no text"
    return f"Reminder: {what}. In {span(before, 'en')}." if before else f"Reminder: {what}."


def _say_ru(what: str, before: int) -> str:
    what = what or "без текста"
    return f"Напоминание: {what}. Через {span(before, 'ru')}." if before else f"Напоминание: {what}."


def _say_es(what: str, before: int) -> str:
    what = what or "sin texto"
    return f"Recordatorio: {what}. En {span(before, 'es')}." if before else f"Recordatorio: {what}."


def _say_de(what: str, before: int) -> str:
    what = what or "ohne Text"
    return f"Erinnerung: {what}. In {span(before, 'de')}." if before else f"Erinnerung: {what}."


def _say_ja(what: str, before: int) -> str:
    what = what or "テキストなし"
    return f"リマインダー：{what}。{span(before, 'ja')}後です。" if before else f"リマインダー：{what}。"


def _say_zh(what: str, before: int) -> str:
    what = what or "无内容"
    return f"提醒：{what}。{span(before, 'zh')}后。" if before else f"提醒：{what}。"


_SPEAKERS = {"en": _say_en, "ru": _say_ru, "es": _say_es, "de": _say_de, "ja": _say_ja, "zh": _say_zh}
