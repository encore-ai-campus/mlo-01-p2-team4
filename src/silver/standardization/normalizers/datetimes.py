"""Legacy date parsing plus the Phase 4 Seoul-normalized datetime API."""

import re
from datetime import datetime, timedelta, timezone

from .nulls import clean_text


DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d",
    "%Y%m%d",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S.%f",
    "%Y/%m/%dT%H:%M:%S",
    "%Y/%m/%dT%H:%M:%S.%f",
    "%Y.%m.%d %H:%M:%S",
    "%Y.%m.%d %H:%M:%S.%f",
    "%Y.%m.%dT%H:%M:%S",
    "%Y.%m.%dT%H:%M:%S.%f",
    "%Y%m%d%H%M%S",
)
SEOUL = timezone(timedelta(hours=9))


def _parse_korean_time(text):
    matched = re.fullmatch(
        r"(\d{4}[-/.]\d{2}[-/.]\d{2})\s+(오전|오후)\s+(\d{1,2}):(\d{2}):(\d{2})",
        text,
    )
    if not matched:
        return None

    date_text, ampm, hour, minute, second = matched.groups()
    year, month, day = map(int, re.split(r"[-/.]", date_text))
    hour = int(hour)
    if ampm == "오후" and hour < 12:
        hour += 12
    elif ampm == "오전" and hour == 12:
        hour = 0
    try:
        return datetime(year, month, day, hour, int(minute), int(second))
    except ValueError:
        return None


def parse_to_iso_datetime(value):
    """Use the pre-existing accepted legacy datetime formats."""
    text = clean_text(value)
    if text is None:
        return None

    parsed = _parse_korean_time(text)
    if parsed is None:
        for date_format in DATE_FORMATS:
            try:
                parsed = datetime.strptime(text, date_format)
                break
            except ValueError:
                continue
    if parsed is None:
        return None

    timespec = "microseconds" if parsed.microsecond else "seconds"
    return parsed.isoformat(timespec=timespec)


def normalize_datetime(value):
    """Normalize an accepted datetime to a Seoul-local naive ISO second."""
    text = clean_text(value)
    if text is None:
        return None

    iso_text = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(iso_text)
    except ValueError:
        parsed_text = parse_to_iso_datetime(text)
        if parsed_text is None:
            return None
        parsed = datetime.fromisoformat(parsed_text)

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(SEOUL).replace(tzinfo=None)
    return parsed.isoformat(timespec="seconds")


__all__ = ["DATE_FORMATS", "normalize_datetime", "parse_to_iso_datetime"]
