"""Null and sentinel handling used by every standardization path."""

import unicodedata


NULL_TOKENS = frozenset(
    {
        "",
        "NULL",
        "N/A",
        "NONE",
        "UNKNOWN",
        "없음",
        "미상",
        "오류값",
        "-",
    }
)


def clean_text(value):
    """Apply the pre-existing NFKC, trim, and placeholder policy."""
    if value is None:
        return None

    text = unicodedata.normalize("NFKC", str(value)).strip()
    return None if text.upper() in NULL_TOKENS else text


normalize_null = clean_text


__all__ = ["NULL_TOKENS", "clean_text", "normalize_null"]
