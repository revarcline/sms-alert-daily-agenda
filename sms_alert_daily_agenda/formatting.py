from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sms_alert_daily_agenda.config import SMS_MAX_CHARS
from sms_alert_daily_agenda.gcal import _parse_start


def _is_all_day(event: dict) -> bool:
    return "date" in event.get("start", {}) and "dateTime" not in event.get("start", {})


def _fmt_time(dt: datetime, tz: ZoneInfo) -> str:
    local = dt.astimezone(tz)
    h = local.hour % 12 or 12
    m = local.minute
    period = "a" if local.hour < 12 else "p"
    return f"{h}:{m:02d}{period}" if m else f"{h}{period}"


def _event_time_str(event: dict, tz: ZoneInfo) -> str:
    """Return compact time string (e.g. '9a-10:30a'), empty string for all-day events."""
    if _is_all_day(event):
        return ""
    s = event.get("start", {})
    e = event.get("end", {})
    try:
        start_dt = datetime.fromisoformat(s["dateTime"])
        if not start_dt.tzinfo:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
        end_dt = datetime.fromisoformat(e.get("dateTime", s["dateTime"]))
        if not end_dt.tzinfo:
            end_dt = end_dt.replace(tzinfo=timezone.utc)
        duration_min = (end_dt - start_dt).total_seconds() / 60
        t = _fmt_time(start_dt, tz)
        if duration_min > 1:
            t += f"-{_fmt_time(end_dt, tz)}"
        return t
    except (ValueError, KeyError):
        return ""


def _event_line(event: dict, tz: ZoneInfo, date_prefix: str = "") -> str:
    title = event.get("summary", "(no title)").strip()
    time_str = _event_time_str(event, tz)
    parts = [p for p in (date_prefix.strip(), time_str, title) if p]
    return " ".join(parts)


_FAR = datetime.max.replace(tzinfo=timezone.utc)


def _sort_key(event: dict) -> datetime:
    return _parse_start(event) or _FAR


def _dedup_sort(events: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for e in sorted(events, key=_sort_key):
        eid = e.get("id", "")
        if eid not in seen:
            seen.add(eid)
            out.append(e)
    return out


def build_messages(
    day: date,
    today: list[dict],
    upcoming: list[dict],
    tz: ZoneInfo,
) -> list[str]:
    """Pack the agenda into ≤160-char SMS strings."""
    lines: list[str] = []
    hdr = day.strftime("%-m/%-d")

    lines.append(f"{hdr}:")
    if today:
        for evt in today:
            lines.append(f"  {_event_line(evt, tz)}")
    else:
        lines.append("  (free)")

    if upcoming:
        lines.append("Soon:")
        for evt in upcoming:
            dt = _parse_start(evt)
            dp = dt.astimezone(tz).strftime("%-m/%-d") if dt else ""
            lines.append(f"  {_event_line(evt, tz, date_prefix=dp)}")

    full = "\n".join(lines)
    chunks: list[str] = []
    while full:
        if len(full) <= SMS_MAX_CHARS:
            chunks.append(full.rstrip())
            break
        chunk = full[:SMS_MAX_CHARS]
        nl = chunk.rfind("\n")
        cut = nl if nl > 0 else SMS_MAX_CHARS
        chunks.append(full[:cut].rstrip())
        full = full[cut:].lstrip("\n")

    return [c for c in chunks if c]
