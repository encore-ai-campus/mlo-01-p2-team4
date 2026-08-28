"""Deterministic SHA-256 fingerprints for standardized business records."""

import hashlib
import json
from dataclasses import asdict, is_dataclass


STANDARD_FIELDS = (
    "area_id", "area_name", "parent_area_id", "parent_area_name", "top_area_id",
    "top_area_name", "top_area_level_code", "employee_id", "employee_name",
    "employee_department_name", "employee_position_name", "employee_hire_datetime",
    "employee_status_code", "area_registration_date", "top_area_registration_date",
)


def _mapping(record):
    value = getattr(record, "business", record)
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict) and isinstance(value.get("business"), dict):
        return value["business"]
    return value


def make_record_fingerprint(record, contract_version="1"):
    """Hash exactly the standard 15 fields plus the contract version."""
    data = _mapping(record)
    payload = {
        "contract_version": str(contract_version),
        "business": {field: data.get(field) for field in STANDARD_FIELDS},
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = ["make_record_fingerprint"]
