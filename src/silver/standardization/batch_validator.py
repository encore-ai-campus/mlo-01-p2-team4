"""Batch context, structural partitioning, and accounting."""

from collections import defaultdict
from collections.abc import Mapping

from src.silver.contracts import (
    BatchContext,
    ObservedLineage,
    ReferenceSnapshot,
    RejectedRecord,
    Violation,
)

from .record_validator import (
    AREA_ID_NAME_COLUMNS,
    CANONICAL_AREA_IDS,
    REQUIRED_COLUMNS,
    accumulate_violations,
    serialize_raw_record,
    validate_clean_row,
    validate_payload_shape,
    validate_wrapper,
)


def _value(context, name):
    return getattr(context, name) if isinstance(context, BatchContext) else context.get(name)


def _has(context, name):
    return hasattr(context, name) if isinstance(context, BatchContext) else name in context


def _violation(code, field, detail, rule_id="SDEC-003"):
    return Violation(code=code, rule_id=rule_id, field=field, detail=detail)


def validate_batch_context(context, expected_contract_id="hr-silver", expected_contract_version="1"):
    """Validate batch metadata; callers should fail the whole batch on errors."""
    if not isinstance(context, (BatchContext, Mapping)):
        return (_violation("TYPE_INVALID", None, "context: object가 아님"),)

    required = (
        "pipeline_run_id", "batch_id", "batch_sequence", "dataset_id", "snapshot_id",
        "contract_id", "contract_version", "ruleset_version", "cursor_in",
        "cursor_out_candidate", "requested_batch_size", "actual_input_count",
    )
    violations = []
    for field in required:
        if not _has(context, field):
            violations.append(_violation("REQUIRED_KEY_MISSING", field, f"{field}: context 필수값 누락"))

    if _value(context, "contract_id") not in (None, expected_contract_id):
        violations.append(_violation("CONTRACT_ID_MISMATCH", "contract_id", "contract_id: 계약 ID 불일치"))
    if _value(context, "contract_version") not in (None, expected_contract_version):
        violations.append(_violation("CONTRACT_VERSION_MISMATCH", "contract_version", "contract_version: 계약 버전 불일치"))

    for field in (
        "pipeline_run_id",
        "batch_id",
        "dataset_id",
        "snapshot_id",
        "contract_id",
        "contract_version",
        "ruleset_version",
    ):
        value = _value(context, field)
        if not isinstance(value, str) or not value.strip():
            violations.append(_violation("TYPE_INVALID", field, f"{field}: 비어 있지 않은 문자열이 아님"))

    sequence = _value(context, "batch_sequence")
    requested = _value(context, "requested_batch_size")
    actual = _value(context, "actual_input_count")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        violations.append(_violation("TYPE_INVALID", "batch_sequence", "batch_sequence: 1 이상 정수가 아님"))
    if not isinstance(requested, int) or isinstance(requested, bool) or requested < 1:
        violations.append(_violation("TYPE_INVALID", "requested_batch_size", "requested_batch_size: 1 이상 정수가 아님"))
    if not isinstance(actual, int) or isinstance(actual, bool) or actual < 0 or (isinstance(requested, int) and actual > requested):
        violations.append(_violation("BATCH_COUNT_INVALID", "actual_input_count", "actual_input_count: batch size 범위를 벗어남"))
    for field in ("cursor_in", "cursor_out_candidate"):
        value = _value(context, field)
        if value is not None and not isinstance(value, str):
            violations.append(_violation("TYPE_INVALID", field, f"{field}: 문자열 또는 NULL이 아님"))
    return accumulate_violations(violations)


def validate_reference_snapshot(snapshot, context=None):
    """Reject incomplete or context-mismatched snapshots before processing."""
    if not isinstance(snapshot, ReferenceSnapshot):
        return (_violation("TYPE_INVALID", "reference_snapshot", "reference_snapshot: ReferenceSnapshot이 아님", "SDEC-006"),)
    violations = []
    if not snapshot.complete:
        violations.append(_violation("REFERENCE_SNAPSHOT_INCOMPLETE", "complete", "reference snapshot이 불완전함", "SDEC-006"))
    if context is not None:
        if snapshot.dataset_id != _value(context, "dataset_id"):
            violations.append(_violation("SNAPSHOT_DATASET_MISMATCH", "dataset_id", "snapshot dataset이 batch와 다름", "SDEC-006"))
        if snapshot.snapshot_id != _value(context, "snapshot_id"):
            violations.append(_violation("SNAPSHOT_ID_MISMATCH", "snapshot_id", "snapshot ID가 batch와 다름", "SDEC-006"))
    return accumulate_violations(violations)


def collect_observed_lineage(record, batch_record_index):
    """Collect nullable lineage without changing the source record."""
    if not isinstance(record, Mapping):
        return ObservedLineage(batch_record_index, None, None, None, None, None)
    values = {
        "record_id": record.get("record_id"),
        "source_row_no": record.get("source_row_no"),
        "source_record_sha256": record.get("source_record_sha256"),
        "release_slot": record.get("release_slot"),
        "scheduled_release_at": record.get("scheduled_release_at"),
    }
    for field in ("record_id", "source_row_no", "release_slot"):
        if not isinstance(values[field], int) or isinstance(values[field], bool):
            values[field] = None
    for field in ("source_record_sha256", "scheduled_release_at"):
        if not isinstance(values[field], str):
            values[field] = None
    return ObservedLineage(batch_record_index=batch_record_index, **values)


def detect_duplicate_lineage_groups(records):
    """Return all duplicate record_id/source_row_no groups as 1-based indexes."""
    groups = defaultdict(list)
    for index, record in enumerate(records, start=1):
        if not isinstance(record, Mapping):
            continue
        for field in ("record_id", "source_row_no"):
            value = record.get(field)
            if isinstance(value, int) and not isinstance(value, bool):
                groups[f"{field}:{value}"].append(index)
    return {
        key: tuple(indexes)
        for key, indexes in groups.items()
        if len(indexes) > 1
    }


def partition_structural_results(records):
    """Partition source rows into candidates and structural RejectedRecord values."""
    records = tuple(records)
    row_results = []
    for index, record in enumerate(records, start=1):
        wrapper_errors = validate_wrapper(record)
        payload = record.get("payload") if isinstance(record, Mapping) else None
        payload_errors = validate_payload_shape(payload)
        row_results.append({
            "record": record,
            "index": index,
            "violations": list(accumulate_violations(wrapper_errors, payload_errors)),
            "observed_lineage": collect_observed_lineage(record, index),
            "raw_json": serialize_raw_record(record),
        })

    for key, indexes in detect_duplicate_lineage_groups(records).items():
        code = "DUPLICATE_RECORD_ID" if key.startswith("record_id:") else "DUPLICATE_SOURCE_ROW_NO"
        field = key.split(":", 1)[0]
        violation = _violation(code, field, f"{field}: batch 내 중복 그룹 전체 Reject")
        for index in indexes:
            row_results[index - 1]["violations"] = list(
                accumulate_violations(row_results[index - 1]["violations"], (violation,))
            )

    candidates = []
    rejected = []
    for result in row_results:
        if result["violations"]:
            rejected.append(
                RejectedRecord(
                    observed_lineage=result["observed_lineage"],
                    raw_json=result["raw_json"],
                    violations=tuple(result["violations"]),
                )
            )
        else:
            candidates.append(result["record"])
    return tuple(candidates), tuple(rejected)


def validate_structural_accounting(input_count, candidate_count, rejected_count, excluded_count=0):
    """Return whether structural partition counts account for every input row."""
    return (
        min(input_count, candidate_count, rejected_count, excluded_count) >= 0
        and input_count == candidate_count + rejected_count + excluded_count
    )


def validate_batch(rows):
    """Return row validation errors in input order."""
    return [validate_clean_row(row) for row in rows]


def build_cleansing_checks(records, accepted, rejected, columns):
    """Build the existing CSV cleansing gate checks from the accepted rows."""
    return {
        "row_balance": len(records) == len(accepted) + len(rejected),
        "column_count_is_15": len(columns) == 15,
        "accepted_rows_are_valid": all(not validate_clean_row(row) for row in accepted),
        "top_level_parent_fields_are_null": all(
            row.get("area_id") != row.get("top_area_id")
            or (
                row.get("parent_area_id") is None
                and row.get("parent_area_name") is None
            )
            for row in accepted
        ),
        "non_top_level_parent_fields_are_present": all(
            row.get("area_id") == row.get("top_area_id")
            or (
                row.get("parent_area_id") is not None
                and row.get("parent_area_name") is not None
            )
            for row in accepted
        ),
        "only_parent_fields_are_nullable": all(
            all(row.get(column) is not None for column in REQUIRED_COLUMNS)
            for row in accepted
        ),
        "active_values_are_standard": all(
            row.get("employee_status_code") in {"ACTIVE", "INACTIVE"}
            for row in accepted
        ),
        "rejected_rows_have_reason": all(row["error_codes"] for row in rejected),
        "area_ids_match_names": all(
            (
                id_column == "parent_area_id"
                and row.get("area_id") == row.get("top_area_id")
                and row.get(id_column) is None
            )
            or row.get(id_column) == CANONICAL_AREA_IDS.get(row.get(name_column))
            for row in accepted
            for id_column, name_column in AREA_ID_NAME_COLUMNS.items()
        ),
    }


def build_cleansing_validation(
    records,
    accepted,
    rejected,
    columns,
    rejection_code_counts,
    conversion_failure_counts,
):
    """Create the legacy validation report with its established JSON structure."""
    checks = build_cleansing_checks(records, accepted, rejected, columns)
    return {
        "status": "ready" if all(checks.values()) else "error",
        "scope": "records.json supplemental legacy input",
        "input_row_count": len(records),
        "accepted_row_count": len(accepted),
        "rejected_row_count": len(rejected),
        "output_column_count": len(columns),
        "canonical_area_count": len({row["area_name"] for row in accepted}),
        "canonical_area_id_counts": {
            id_column: len({row[id_column] for row in accepted if row.get(id_column)})
            for id_column in AREA_ID_NAME_COLUMNS
        },
        "checks": checks,
        "rejection_code_counts": dict(rejection_code_counts),
        "conversion_failure_counts": dict(conversion_failure_counts),
    }


__all__ = [
    "collect_observed_lineage",
    "detect_duplicate_lineage_groups",
    "build_cleansing_checks",
    "build_cleansing_validation",
    "partition_structural_results",
    "validate_batch",
    "validate_batch_context",
    "validate_reference_snapshot",
    "validate_structural_accounting",
]
