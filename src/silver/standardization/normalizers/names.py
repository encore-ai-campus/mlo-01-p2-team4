"""Name normalization and the approved 20-area canonical mapping."""

import re
import unicodedata

from .nulls import NULL_TOKENS, clean_text


CANONICAL_AREA_IDS = {
    "고객서비스": "BIZ_00001",
    "교육": "BIZ_00002",
    "구매": "BIZ_00003",
    "기획": "BIZ_00004",
    "데이터": "BIZ_00005",
    "마케팅": "BIZ_00006",
    "물류": "BIZ_00007",
    "법무": "BIZ_00008",
    "보안": "BIZ_00009",
    "분석": "BIZ_00010",
    "생산": "BIZ_00011",
    "시설": "BIZ_00012",
    "영업": "BIZ_00013",
    "인사": "BIZ_00014",
    "자산관리": "BIZ_00015",
    "재무": "BIZ_00016",
    "전략": "BIZ_00017",
    "품질관리": "BIZ_00018",
    "IT": "BIZ_00019",
    "R&D": "BIZ_00020",
}
CANONICAL_AREA_NAMES = tuple(sorted(CANONICAL_AREA_IDS, key=len, reverse=True))
AREA_ID_NAME_COLUMNS = {
    "area_id": "area_name",
    "parent_area_id": "parent_area_name",
    "top_area_id": "top_area_name",
}
AREA_NAME_COLUMNS = frozenset(AREA_ID_NAME_COLUMNS.values())


def normalize_name(value):
    """Apply the already approved CSV-name cleanup behavior."""
    text = clean_text(value)
    if text is None:
        return None

    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"(?<=[가-힣])\s+(?=[가-힣])", "", text)
    text = re.sub(r"(?<=[A-Z])\s+(?=[A-Z])", "", text)
    text = re.sub(r"\s*&\s*", "&", text)
    return text if 1 <= len(text) <= 100 else None


def canonicalize_area_name(value):
    """Map an approved legacy area-name variant to one canonical area."""
    text = normalize_name(value)
    if text is None:
        return None

    text = re.sub(r"\s+\d+$", "", text).strip()
    upper_text = text.upper()
    for canonical_name in CANONICAL_AREA_NAMES:
        if upper_text.startswith(canonical_name.upper()):
            return canonical_name
    return None


def normalize_text(value):
    """Phase 4 general text rule: NFKC and outer whitespace only."""
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value)).strip()
    return None if text.upper() in NULL_TOKENS else text


def normalize_area_name(value):
    """Map an approved canonical area prefix after numeric-suffix cleanup."""
    text = normalize_text(value)
    if text is None:
        return None
    text = re.sub(r"\s+\d+$", "", text).strip()
    upper_text = text.upper()
    for canonical_name in CANONICAL_AREA_NAMES:
        if upper_text == canonical_name.upper() or upper_text.startswith(canonical_name.upper()):
            return canonical_name
    return None


__all__ = [
    "AREA_ID_NAME_COLUMNS",
    "AREA_NAME_COLUMNS",
    "CANONICAL_AREA_IDS",
    "CANONICAL_AREA_NAMES",
    "canonicalize_area_name",
    "normalize_area_name",
    "normalize_name",
    "normalize_text",
]
