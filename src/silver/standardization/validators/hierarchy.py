"""Organization parent/top-level relationship validation."""

from src.silver.contracts import Violation


def _violation(code, field, detail):
    return Violation(code=code, rule_id="SDEC-006", field=field, detail=detail)


def _is_top_level(row):
    return (
        row.get("area_id") is not None
        and row.get("area_id") == row.get("top_area_id")
        and row.get("area_name") is not None
        and row.get("area_name") == row.get("top_area_name")
    )


def validate_top_level_rule(row):
    """Validate conditional parent nullability for one standardized row."""
    area_id = row.get("area_id")
    top_area_id = row.get("top_area_id")
    if area_id is None or top_area_id is None:
        return ()
    violations = []
    if area_id == top_area_id:
        if not _is_top_level(row):
            violations.append(_violation("AREA_TOP_NAME_MISMATCH", "top_area_name", "최상위 영역은 area_name과 top_area_name이 같아야 합니다."))
        if row.get("parent_area_id") is not None:
            violations.append(_violation("PARENT_AREA_NOT_ALLOWED", "parent_area_id", "최상위 영역은 부모 ID가 없어야 합니다."))
        if row.get("parent_area_name") is not None:
            violations.append(_violation("PARENT_AREA_NOT_ALLOWED", "parent_area_name", "최상위 영역은 부모명이 없어야 합니다."))
    elif row.get("parent_area_id") is None or row.get("parent_area_name") is None:
        violations.append(_violation("PARENT_AREA_MISSING", "parent_area_id", "하위 영역은 부모 ID와 부모명이 필요합니다."))
    return tuple(violations)


def validate_parent_presence(row, canonical_area_ids):
    """Validate parent existence and self-reference against a full snapshot."""
    parent_id = row.get("parent_area_id")
    if _is_top_level(row):
        return ()
    if parent_id is None:
        return (_violation("PARENT_AREA_MISSING", "parent_area_id", "하위 영역의 부모 ID가 없습니다."),)
    if parent_id == row.get("area_id"):
        return (_violation("PARENT_AREA_SELF_REFERENCE", "parent_area_id", "영역이 자기 자신을 부모로 참조합니다."),)
    if parent_id not in set(canonical_area_ids):
        return (_violation("PARENT_AREA_NOT_FOUND", "parent_area_id", "부모 ID가 reference snapshot에 없습니다."),)
    return ()


def validate_hierarchy(row):
    """Return hierarchy errors for one standardized row."""
    errors = []
    area_id = row.get("area_id")
    top_area_id = row.get("top_area_id")
    parent_area_id = row.get("parent_area_id")
    parent_area_name = row.get("parent_area_name")

    if area_id is None or top_area_id is None:
        return errors

    if area_id == top_area_id:
        if parent_area_id is not None:
            errors.append(("PARENT_ID_NOT_ALLOWED", "최상위 영역은 parent_area_id가 NULL이어야 함"))
        if parent_area_name is not None:
            errors.append(("PARENT_NAME_NOT_ALLOWED", "최상위 영역은 parent_area_name이 NULL이어야 함"))
        return errors

    if parent_area_id is None:
        errors.append(("REQUIRED_VALUE_MISSING", "parent_area_id: 하위 영역의 상위 영역 ID 누락"))
    if parent_area_name is None:
        errors.append(("REQUIRED_VALUE_MISSING", "parent_area_name: 하위 영역의 상위 영역명 누락"))
    return errors


__all__ = ["validate_hierarchy", "validate_parent_presence", "validate_top_level_rule"]
