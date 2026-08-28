"""Map the approved legacy payload fields into the 15 standard fields."""

import csv
from collections import Counter
from pathlib import Path

from src.silver.contracts import Violation

from .normalizers.codes import map_active_flag, map_top_level, normalize_level, normalize_status
from .normalizers.datetimes import normalize_datetime, parse_to_iso_datetime
from .normalizers.identifiers import normalize_area_id, normalize_employee_id, normalize_identifier
from .normalizers.names import (
    AREA_ID_NAME_COLUMNS,
    AREA_NAME_COLUMNS,
    CANONICAL_AREA_IDS,
    canonicalize_area_name,
    normalize_area_name,
    normalize_name,
    normalize_text,
)
from .normalizers.nulls import clean_text
from .record_validator import INVALID_VALUES_BY_COLUMN, add_error


MAPPING_FILE = Path(__file__).resolve().with_name("hr-organization-column-mapping.csv")
TRANSFORMS = {
    "normalize_identifier": normalize_identifier,
    "normalize_name": normalize_name,
    "canonicalize_area_name": canonicalize_area_name,
    "map_top_level": map_top_level,
    "map_active_flag": map_active_flag,
    "parse_to_iso_datetime": parse_to_iso_datetime,
    "parse_to_iso_date": parse_to_iso_datetime,
}


def read_mappings(mapping_file=MAPPING_FILE):
    with Path(mapping_file).open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def standard_columns(mappings):
    if len(mappings) != 15:
        raise ValueError("컬럼 매핑은 15개여야 합니다.")
    columns = [mapping["standard_column"] for mapping in mappings]
    if len(columns) != len(set(columns)):
        raise ValueError("표준 컬럼명이 중복되었습니다.")
    return columns


def transform_value(standard_column, raw_value, conversion_rule):
    """Apply the conversion rule declared in the existing mapping CSV."""
    transformer = canonicalize_area_name if standard_column in AREA_NAME_COLUMNS else TRANSFORMS.get(conversion_rule)
    if transformer is None:
        raise ValueError(f"알 수 없는 변환 규칙: {conversion_rule}")
    return transformer(raw_value)


def map_legacy_payload(payload, mappings):
    """Reproduce the verified CSV mapping behavior for one raw payload."""
    payload = payload or {}
    cleaned_row = {}
    errors = []
    conversion_failures = Counter()

    source_area_name = canonicalize_area_name(payload.get("area_nm"))
    source_top_area_name = canonicalize_area_name(payload.get("top_area_nm"))
    source_is_top_level = source_area_name is not None and source_area_name == source_top_area_name

    for mapping in mappings:
        legacy_column = mapping["legacy_column"]
        standard_column = mapping["standard_column"]
        if standard_column in AREA_ID_NAME_COLUMNS:
            continue
        if standard_column == "parent_area_name" and source_is_top_level:
            cleaned_row[standard_column] = None
            continue

        raw_value = payload.get(legacy_column)
        source_value = clean_text(raw_value)
        cleaned_value = transform_value(standard_column, raw_value, mapping["conversion_rule"])
        cleaned_row[standard_column] = cleaned_value

        if source_value is not None and cleaned_value is None:
            conversion_failures[standard_column] += 1
            add_error(
                errors,
                "UNKNOWN_AREA_NAME" if standard_column in AREA_NAME_COLUMNS else "TRANSFORM_FAILED",
                f"{standard_column}: 변환 실패",
            )
        elif mapping["required"] == "Y" and cleaned_value is None:
            add_error(errors, "REQUIRED_VALUE_MISSING", f"{standard_column}: 필수값 누락")

    for id_column, name_column in AREA_ID_NAME_COLUMNS.items():
        cleaned_row[id_column] = CANONICAL_AREA_IDS.get(cleaned_row.get(name_column))

    if cleaned_row.get("area_id") is not None and cleaned_row.get("area_id") == cleaned_row.get("top_area_id"):
        cleaned_row["parent_area_id"] = None
        cleaned_row["parent_area_name"] = None

    return cleaned_row, errors, conversion_failures


def _violation(code, rule_id, field, detail):
    return Violation(code=code, rule_id=rule_id, field=field, detail=detail)


def map_business_record(payload, canonical_area_ids):
    """Map a structurally valid payload to the Phase 4 standard record."""
    errors = []

    def required_value(field, raw, normalized, invalid_code, rule_id):
        if normalized is None:
            if clean_text(raw) is not None:
                errors.append(_violation(invalid_code, rule_id, field, f"{field}: 표준화할 수 없는 값"))
            else:
                errors.append(_violation("REQUIRED_VALUE_MISSING", "SDEC-007", field, f"{field}: 필수값 누락"))
        return normalized

    area_name = required_value("area_name", payload.get("area_nm"), normalize_area_name(payload.get("area_nm")), "AREA_NAME_UNMAPPED", "SDEC-004")
    top_area_name = required_value("top_area_name", payload.get("top_area_nm"), normalize_area_name(payload.get("top_area_nm")), "AREA_NAME_UNMAPPED", "SDEC-004")
    area_id = CANONICAL_AREA_IDS.get(area_name)
    top_area_id = CANONICAL_AREA_IDS.get(top_area_name)

    for field, raw in (("area_id", payload.get("area_no")), ("top_area_id", payload.get("top_area_no"))):
        if clean_text(raw) is not None and normalize_area_id(raw) is None:
            errors.append(_violation("IDENTIFIER_INVALID", "SDEC-004", field, f"{field}: BIZ_##### 형식이 아님"))

    parent_name = None
    parent_id = None
    if area_name is None or area_name != top_area_name:
        parent_name = normalize_area_name(payload.get("p_area_nm"))
        if clean_text(payload.get("p_area_nm")) is not None and parent_name is None:
            errors.append(_violation("AREA_NAME_UNMAPPED", "SDEC-004", "parent_area_name", "parent_area_name: 기준 영역으로 매핑할 수 없음"))
        parent_id = CANONICAL_AREA_IDS.get(parent_name)
        if clean_text(payload.get("p_area_no")) is not None and normalize_area_id(payload.get("p_area_no")) is None:
            errors.append(_violation("IDENTIFIER_INVALID", "SDEC-004", "parent_area_id", "parent_area_id: BIZ_##### 형식이 아님"))

    employee_id = required_value("employee_id", payload.get("mgr_no"), normalize_employee_id(payload.get("mgr_no")), "IDENTIFIER_INVALID", "SDEC-003")
    employee_name = required_value("employee_name", payload.get("mgr_nm"), normalize_text(payload.get("mgr_nm")), "NAME_INVALID", "SDEC-007")
    employee_department_name = required_value("employee_department_name", payload.get("mgr_dept_nm"), normalize_text(payload.get("mgr_dept_nm")), "NAME_INVALID", "SDEC-007")
    employee_position_name = required_value("employee_position_name", payload.get("mgr_pos_nm"), normalize_text(payload.get("mgr_pos_nm")), "NAME_INVALID", "SDEC-007")
    employee_hire_datetime = required_value("employee_hire_datetime", payload.get("mgr_hire_dtm"), normalize_datetime(payload.get("mgr_hire_dtm")), "DATETIME_INVALID", "SDEC-005")
    area_registration_date = required_value("area_registration_date", payload.get("area_reg_dtm"), normalize_datetime(payload.get("area_reg_dtm")), "DATETIME_INVALID", "SDEC-005")
    top_area_registration_date = required_value("top_area_registration_date", payload.get("top_area_reg_dtm"), normalize_datetime(payload.get("top_area_reg_dtm")), "DATETIME_INVALID", "SDEC-005")

    top_area_level_code = normalize_level(payload.get("top_area_lvl"))
    if top_area_level_code is None:
        code = "REQUIRED_VALUE_MISSING" if clean_text(payload.get("top_area_lvl")) is None else "LEVEL_CODE_INVALID"
        errors.append(_violation(code, "SDEC-006", "top_area_level_code", "top_area_level_code: TOP_LEVEL로 변환할 수 없음"))

    employee_status_code = normalize_status(payload.get("mgr_act_yn"))
    if employee_status_code is None:
        code = "REQUIRED_VALUE_MISSING" if clean_text(payload.get("mgr_act_yn")) is None else "STATUS_CODE_INVALID"
        errors.append(_violation(code, "SDEC-005", "employee_status_code", "employee_status_code: ACTIVE/INACTIVE로 변환할 수 없음"))

    business = {
        "area_id": area_id,
        "area_name": area_name,
        "parent_area_id": parent_id,
        "parent_area_name": parent_name,
        "top_area_id": top_area_id,
        "top_area_name": top_area_name,
        "top_area_level_code": top_area_level_code,
        "employee_id": employee_id,
        "employee_name": employee_name,
        "employee_department_name": employee_department_name,
        "employee_position_name": employee_position_name,
        "employee_hire_datetime": employee_hire_datetime,
        "employee_status_code": employee_status_code,
        "area_registration_date": area_registration_date,
        "top_area_registration_date": top_area_registration_date,
    }
    for field, invalid_values in INVALID_VALUES_BY_COLUMN.items():
        if business.get(field) in invalid_values:
            errors.append(_violation("INVALID_BUSINESS_VALUE", "SDEC-007", field, f"{field}: 격리 대상 업무값"))
    return business, tuple(errors)


__all__ = [
    "MAPPING_FILE",
    "TRANSFORMS",
    "map_business_record",
    "map_legacy_payload",
    "read_mappings",
    "standard_columns",
    "transform_value",
]
