"""Parse human friendly durations like "30 min", "2h", "until 7am", "tomorrow morning"."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

FOREVER = {"", "forever", "until i undo", "until undo", "indefinitely", "none", "always"}

_UNITS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
}


def parse_until(text: str | None, now: datetime) -> datetime | None:
    """Return the expiry time, or None for "until I undo it".

    `now` must be timezone aware; results are in the same timezone.
    """
    t = (text or "").strip().lower()
    if t in FOREVER:
        return None
    if t in ("tomorrow morning", "until tomorrow morning", "until morning"):
        t = "until 7am"

    # "30", "30m", "1.5 hours", "1 hour 30 min"
    parts = re.findall(r"(\d+(?:\.\d+)?)\s*([a-z]*)", t)
    if parts and not t.startswith("until") and re.fullmatch(r"[\d.\sa-z]+", t):
        total = 0.0
        for num, unit in parts:
            mult = _UNITS.get(unit or "min")
            if mult is None:
                break
            total += float(num) * mult
        else:
            if total <= 0:
                raise ValueError(f"Duration must be positive: {text!r}")
            return now + timedelta(seconds=total)

    # "until 7am", "7:30 pm", "21:00"
    m = re.fullmatch(r"(?:until\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", t)
    if m:
        hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if ampm == "pm" and hour != 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        if hour > 23 or minute > 59:
            raise ValueError(f"Not a valid time: {text!r}")
        when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if when <= now:
            when += timedelta(days=1)
        return when

    raise ValueError(f"Don't understand duration {text!r}. Try '30 min', '2 hours', 'until 7am' or 'until I undo'.")


def describe(until: datetime | None, now: datetime) -> str:
    if until is None:
        return "until you turn it off"
    clock = until.strftime("%-I:%M %p")
    if until.date() != now.date():
        clock += " " + ("tomorrow" if until.date() == (now + timedelta(days=1)).date() else until.strftime("%a %b %-d"))
    mins = round((until - now).total_seconds() / 60)
    span = f"{mins} min" if mins < 90 else f"{mins / 60:.1f} h".replace(".0 h", " h")
    return f"for {span} (until {clock})"
