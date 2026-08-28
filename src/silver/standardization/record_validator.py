"""Validated raw-record shape and approved cleaned-row quality rules."""

import json
import re
from collections.abc import Mapping
from datetime import datetime

from src.silver.contracts import Violation

from .normalizers.names import (
    AREA_ID_NAME_COLUMNS,
    CANONICAL_AREA_IDS,
)
from .validators.hierarchy import validate_hierarchy


IDENTIFIER_COLUMNS = (
    "area_id",
    "parent_area_id",
    "top_area_id",
    "employee_id",
)
NAME_COLUMNS = (
    "area_name",
    "parent_area_name",
    "top_area_name",
    "employee_name",
    "employee_department_name",
    "employee_position_name",
)
CODE_COLUMNS = ("top_area_level_code",)
DATE_COLUMNS = (
    "employee_hire_datetime",
    "area_registration_date",
    "top_area_registration_date",
)
REQUIRED_COLUMNS = (
    "area_id",
    "area_name",
    "top_area_id",
    "top_area_name",
    "top_area_level_code",
    "employee_id",
    "employee_name",
    "employee_department_name",
    "employee_position_name",
    "employee_hire_datetime",
    "employee_status_code",
    "area_registration_date",
    "top_area_registration_date",
)
INVALID_VALUES_BY_COLUMN = {
    "top_area_name": frozenset({"오류지역"}),
    "employee_department_name": frozenset({"기타팀"}),
    "employee_position_name": frozenset({"기타"}),
}

EXPECTED_WRAPPER_FIELDS = frozenset(
    {
        "record_id",
        "payload",
        "release_slot",
        "scheduled_release_at",
        "source_record_sha256",
        "source_row_no",
    }
)
EXPECTED_PAYLOAD_FIELDS = frozenset(
    {
        "area_no",
        "area_nm",
        "p_area_no",
        "p_area_nm",
        "top_area_no",
        "top_area_nm",
        "top_area_lvl",
        "mgr_no",
        "mgr_nm",
        "mgr_dept_nm",
        "mgr_pos_nm",
        "mgr_hire_dtm",
        "mgr_act_yn",
        "area_reg_dtm",
        "top_area_reg_dtm",
    }
)


def add_error(errors, code, detail):
    """Append a legacy `(code, detail)` error once, preserving its order."""
    error = (code, detail)
    if error not in errors:
        errors.append(error)


def validate_clean_row(row):
    """Run the existing 15-field output validation without changing values."""
    errors = []

    for column in REQUIRED_COLUMNS:
        if row.get(column) is None:
            add_error(errors, "REQUIRED_VALUE_MISSING", f"{column}: 필수값 누락")

    for id_column, name_column in AREA_ID_NAME_COLUMNS.items():
        area_name = row.get(name_column)
        if area_name is None:
            continue

        expected_id = CANONICAL_AREA_IDS.get(area_name)
        if expected_id is None:
            add_error(errors, "UNKNOWN_AREA_NAME", f"{name_column}: 20개 기준 영역 외 값")
        elif row.get(id_column) is not None and row.get(id_column) != expected_id:
            add_error(errors, "AREA_ID_MISMATCH", f"{id_column}: 표준 영역 ID 불일치")

    for code, detail in validate_hierarchy(row):
        add_error(errors, code, detail)

    for column in IDENTIFIER_COLUMNS:
        value = row.get(column)
        if value and not re.fullmatch(r"[A-Z][A-Z0-9_]{0,19}", value):
            add_error(errors, "IDENTIFIER_FORMAT_INVALID", f"{column}: 식별자 형식 오류")

    for column in NAME_COLUMNS:
        value = row.get(column)
        if not value:
            continue

        if value in INVALID_VALUES_BY_COLUMN.get(column, frozenset()):
            add_error(errors, "INVALID_BUSINESS_VALUE", f"{column}: 허용되지 않는 값")
        if not 1 <= len(value) <= 100:
            add_error(errors, "NAME_LENGTH_INVALID", f"{column}: 이름 길이 오류")
        if value != value.strip() or "\t" in value or "　" in value:
            add_error(errors, "WHITESPACE_REMAINS", f"{column}: 공백 정제 실패")
        if re.search(r"(?<=[가-힣])\s+(?=[가-힣])", value):
            add_error(errors, "WHITESPACE_REMAINS", f"{column}: 한글 내부 공백 잔존")
        if re.search(r"(?<=[A-Z])\s+(?=[A-Z])", value):
            add_error(errors, "WHITESPACE_REMAINS", f"{column}: 영문 내부 공백 잔존")
        if re.search(r"(?:\s+&|&\s+)", value):
            add_error(errors, "WHITESPACE_REMAINS", f"{column}: 앰퍼샌드 주변 공백 잔존")

    for column in CODE_COLUMNS:
        value = row.get(column)
        if value and not re.fullmatch(r"[A-Z][A-Z0-9_]{0,19}", value):
            add_error(errors, "CODE_FORMAT_INVALID", f"{column}: 코드 형식 오류")

    for column in DATE_COLUMNS:
        value = row.get(column)
        if not value:
            continue
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?", value):
            add_error(errors, "DATE_FORMAT_INVALID", f"{column}: YYYY-MM-DD 형식 오류")
            continue
        try:
            datetime.fromisoformat(value)
        except ValueError:
            add_error(errors, "DATE_VALUE_INVALID", f"{column}: 유효하지 않은 일시")

    if row.get("employee_status_code") not in {None, "ACTIVE", "INACTIVE"}:
        add_error(errors, "STATUS_CODE_INVALID", "employee_status_code: ACTIVE 또는 INACTIVE가 아님")

    return errors


def _violation(code, field, detail, rule_id="SDEC-003"):
    return Violation(code=code, rule_id=rule_id, field=field, detail=detail)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def serialize_raw_record(record):
    """Serialize a source row deterministically without modifying it."""
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def validate_wrapper(record):
    """Validate all six wrapper fields and accumulate every error."""
    if not isinstance(record, Mapping):
        return (_violation("TYPE_INVALID", None, "wrapper: object가 아님"),)

    violations = []
    keys = set(record)
    for field in sorted(EXPECTED_WRAPPER_FIELDS - keys):
        violations.append(_violation("REQUIRED_KEY_MISSING", field, f"{field}: wrapper 필수 키 누락"))
    for field in sorted(keys - EXPECTED_WRAPPER_FIELDS):
        violations.append(_violation("UNEXPECTED_KEY", field, f"{field}: wrapper 허용 목록 외 키"))
    if "record_id" in record and (not _is_int(record["record_id"]) or record["record_id"] < 0):
        violations.append(_violation("TYPE_INVALID", "record_id", "record_id: 음이 아닌 정수가 아님"))
    if "payload" in record and not isinstance(record["payload"], Mapping):
        violations.append(_violation("TYPE_INVALID", "payload", "payload: object가 아님"))
    if "release_slot" in record and (not _is_int(record["release_slot"]) or record["release_slot"] < 0):
        violations.append(_violation("TYPE_INVALID", "release_slot", "release_slot: 0 이상 정수가 아님"))
    if "scheduled_release_at" in record:
        value = record["scheduled_release_at"]
        if not isinstance(value, str):
            violations.append(_violation("TYPE_INVALID", "scheduled_release_at", "scheduled_release_at: 문자열이 아님"))
        else:
            try:
                parsed = datetime.fromisoformat(value)
                if parsed.tzinfo is None:
                    raise ValueError
            except ValueError:
                violations.append(_violation("DATETIME_INVALID", "scheduled_release_at", "scheduled_release_at: offset 포함 ISO 일시가 아님", "SDEC-005"))
    if "source_record_sha256" in record:
        value = record["source_record_sha256"]
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
            violations.append(_violation("SHA256_INVALID", "source_record_sha256", "source_record_sha256: SHA-256 hex 64자 형식이 아님"))
    if "source_row_no" in record and (not _is_int(record["source_row_no"]) or record["source_row_no"] < 1):
        violations.append(_violation("TYPE_INVALID", "source_row_no", "source_row_no: 1 이상 정수가 아님"))
    return accumulate_violations(violations)


def validate_payload_shape(payload):
    """Validate all fifteen payload fields and primitive source types."""
    if not isinstance(payload, Mapping):
        return (_violation("TYPE_INVALID", "payload", "payload: object가 아님"),)

    violations = []
    keys = set(payload)
    for field in sorted(EXPECTED_PAYLOAD_FIELDS - keys):
        violations.append(_violation("REQUIRED_KEY_MISSING", field, f"{field}: payload 필수 키 누락"))
    for field in sorted(keys - EXPECTED_PAYLOAD_FIELDS):
        violations.append(_violation("UNEXPECTED_KEY", field, f"{field}: payload 허용 목록 외 키"))
    for field in sorted(keys & EXPECTED_PAYLOAD_FIELDS):
        allowed = (str, type(None), int, bool) if field == "mgr_act_yn" else (str, type(None))
        if not isinstance(payload[field], allowed):
            violations.append(_violation("TYPE_INVALID", field, f"{field}: 허용되지 않은 원천 타입"))
    return accumulate_violations(violations)


def accumulate_violations(*groups):
    """Flatten violation groups while preserving the first occurrence."""
    result = []
    seen = set()
    for group in groups:
        if isinstance(group, Violation):
            group = (group,)
        for violation in group:
            key = (violation.code, violation.rule_id, violation.field, violation.detail)
            if key not in seen:
                seen.add(key)
                result.append(violation)
    return tuple(result)


validate_record = validate_clean_row


__all__ = [
    "CODE_COLUMNS",
    "DATE_COLUMNS",
    "EXPECTED_PAYLOAD_FIELDS",
    "EXPECTED_WRAPPER_FIELDS",
    "IDENTIFIER_COLUMNS",
    "INVALID_VALUES_BY_COLUMN",
    "NAME_COLUMNS",
    "REQUIRED_COLUMNS",
    "accumulate_violations",
    "add_error",
    "serialize_raw_record",
    "validate_clean_row",
    "validate_payload_shape",
    "validate_record",
    "validate_wrapper",
]
