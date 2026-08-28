"""Detect conflicting dependent values within a batch."""

import json
import hashlib
from collections import defaultdict
from dataclasses import asdict, is_dataclass


PROJECTIONS = {
    "employee": (
        "employee_id",
        ("employee_name", "employee_department_name", "employee_position_name", "employee_hire_datetime", "employee_status_code"),
    ),
    "area": (
        "area_id",
        ("area_name", "top_area_id", "top_area_name", "area_registration_date"),
    ),
    "parent_lookup": (
        "top_area_id",
        ("top_area_name", "top_area_level_code", "top_area_registration_date"),
    ),
    "join_reference": (
        ("area_id", "employee_id"),
        ("area_name", "employee_name", "employee_department_name", "employee_position_name", "employee_hire_datetime", "employee_status_code"),
    ),
}


def _mapping(row):
    value = getattr(row, "business", row)
    if is_dataclass(value):
        return asdict(value)
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def detect_batch_conflicts(rows):
    """Return conflict-key -> all participating 1-based row indexes."""
    result = {}
    for label, (key_fields, value_fields) in PROJECTIONS.items():
        key_fields = (key_fields,) if isinstance(key_fields, str) else key_fields
        groups = defaultdict(list)
        for index, row in enumerate(rows, start=1):
            data = _mapping(row)
            key = tuple(data.get(field) for field in key_fields)
            if any(value is None for value in key):
                continue
            values = {field: data.get(field) for field in value_fields}
            groups[key].append((index, _digest(values)))
        for key, entries in groups.items():
            if len({digest for _, digest in entries}) > 1:
                rendered = "|".join(str(value) for value in key)
                result[f"{label}:{rendered}"] = tuple(index for index, _ in entries)
    return dict(sorted(result.items()))


def reject_conflict_groups(conflicts):
    """Return every row index participating in a conflict key."""
    return {index for indexes in conflicts.values() for index in indexes}


def find_conflicts(rows, key_column, value_columns):
    """Return groups whose dependent values are not constant."""
    groups = defaultdict(list)
    for row in rows:
        key = row.get(key_column)
        if key is not None:
            groups[key].append(row)

    conflicts = []
    for key, group in groups.items():
        for column in value_columns:
            values = {row.get(column) for row in group}
            if len(values) > 1:
                conflicts.append({"key": key, "column": column, "values": sorted(values, key=str)})
    return conflicts


__all__ = ["detect_batch_conflicts", "find_conflicts", "reject_conflict_groups"]
