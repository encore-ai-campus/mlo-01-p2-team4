"""Legacy identifier normalization and Phase 4 strict identifier checks."""

import re

from .nulls import clean_text


def normalize_identifier(value):
    """Preserve the existing generic identifier normalization rule."""
    value = clean_text(value)
    if value is None:
        return None

    compact = re.sub(r"[\s_-]+", "", value.upper())
    if match := re.fullmatch(r"BIZ(\d+)", compact):
        value = f"BIZ_{match.group(1)}"
    elif match := re.fullmatch(r"EMP(\d+)", compact):
        value = f"EMP{match.group(1)}"
    else:
        value = re.sub("_+", "_", re.sub(r"[\s-]+", "_", value.upper()))

    return value if re.fullmatch(r"[A-Z][A-Z0-9_]{0,19}", value) else None


def normalize_employee_id(value):
    """Return only the approved six-digit employee identifier."""
    normalized = normalize_identifier(value)
    return normalized if normalized and re.fullmatch(r"EMP[0-9]{6}", normalized) else None


def normalize_area_id(value):
    """Return only the approved five-digit canonical area identifier."""
    normalized = normalize_identifier(value)
    return normalized if normalized and re.fullmatch(r"BIZ_[0-9]{5}", normalized) else None


__all__ = ["normalize_area_id", "normalize_employee_id", "normalize_identifier"]
