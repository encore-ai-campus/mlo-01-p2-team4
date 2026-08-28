"""Approved source-code mappings for levels and employee status."""

from .nulls import clean_text


def map_top_level(value):
    text = clean_text(value)
    if text and text.upper() in {"1", "L1", "TOP LEVEL", "TOP_LEVEL", "최상위"}:
        return "TOP_LEVEL"
    return None


def map_active_flag(value):
    text = clean_text(value)
    if text is None:
        return None

    text = text.upper()
    if text in {"Y", "YES", "1", "사용", "재직", "ACTIVE"}:
        return "ACTIVE"
    if text in {"N", "NO", "0", "미사용", "퇴직", "INACTIVE"}:
        return "INACTIVE"
    return None


normalize_status = map_active_flag
normalize_level = map_top_level


__all__ = ["map_active_flag", "map_top_level", "normalize_level", "normalize_status"]
