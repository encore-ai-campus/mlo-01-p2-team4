"""CSV runner and Phase 4 orchestration for HR organization standardization.

The business rules live in the sibling modules. This file owns only
composition, file I/O, and the public command-line entry point.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from src.silver.contracts import (  # noqa: E402
    BatchContext,
    BatchContractError,
    ObservedLineage,
    Phase4Metrics,
    Phase4Output,
    RecordLineage,
    RejectedRecord,
    StandardizedBusinessRecord,
    StandardizedRecord,
    Violation,
)
from src.silver.standardization.batch_validator import (  # noqa: E402
    build_cleansing_validation,
    collect_observed_lineage,
    partition_structural_results,
    validate_batch_context,
    validate_reference_snapshot,
    validate_structural_accounting,
)
from src.silver.standardization.field_mapper import (  # noqa: E402
    TRANSFORMS,
    map_business_record,
    map_legacy_payload,
    read_mappings,
    standard_columns,
)
from src.silver.standardization.fingerprints import STANDARD_FIELDS, make_record_fingerprint  # noqa: E402
from src.silver.standardization.normalizers.codes import (  # noqa: E402
    map_active_flag,
    map_top_level,
)
from src.silver.standardization.normalizers.datetimes import (  # noqa: E402
    DATE_FORMATS,
    parse_to_iso_datetime,
)
from src.silver.standardization.normalizers.identifiers import (  # noqa: E402
    normalize_identifier,
)
from src.silver.standardization.normalizers.names import (  # noqa: E402
    AREA_ID_NAME_COLUMNS,
    AREA_NAME_COLUMNS,
    CANONICAL_AREA_IDS,
    CANONICAL_AREA_NAMES,
    canonicalize_area_name,
    normalize_name,
)
from src.silver.standardization.normalizers.nulls import NULL_TOKENS, clean_text  # noqa: E402
from src.silver.standardization.record_validator import (  # noqa: E402
    serialize_raw_record,
    validate_clean_row,
)
from src.silver.standardization.reject_factory import create_rejection  # noqa: E402
from src.silver.standardization.validators.conflicts import (  # noqa: E402
    detect_batch_conflicts,
    reject_conflict_groups,
)
from src.silver.standardization.validators.hierarchy import (  # noqa: E402
    validate_parent_presence,
    validate_top_level_rule,
)


INPUT_FILE = PROJECT_DIR / "test" / "data" / "records.json"
MAPPING_FILE = BASE_DIR / "hr-organization-column-mapping.csv"
CLEANED_FILE = PROJECT_DIR / "test" / "data" / "hr-organization-cleaned.csv"
REJECTED_FILE = PROJECT_DIR / "test" / "data" / "hr-organization-rejected.csv"
VALIDATION_FILE = BASE_DIR / "hr-organization-cleansing-validation.json"


def build_phase4_output(context, accepted, rejected):
    """Build deterministically sorted Phase 4 output with full accounting."""
    accepted = tuple(sorted(accepted, key=lambda row: row.record_fingerprint))
    rejected = tuple(sorted(rejected, key=lambda row: row.raw_json))
    metrics = Phase4Metrics(
        input_count=len(accepted) + len(rejected),
        accepted_count=len(accepted),
        rejected_count=len(rejected),
        excluded_count=0,
    )
    return Phase4Output(context=context, accepted=accepted, rejected=rejected, metrics=metrics)


def validate_phase4_output(output):
    """Return violations for the existing Phase 4 output schema contract."""
    if not isinstance(output, Phase4Output):
        return (Violation("TYPE_INVALID", "SDEC-007", None, "Phase4Output 객체가 아님"),)

    errors = []
    nullable_business_fields = {"parent_area_id", "parent_area_name"}

    def type_error(field, detail):
        errors.append(Violation("TYPE_INVALID", "SDEC-007", field, detail))

    def positive_int(value, minimum=0):
        return isinstance(value, int) and not isinstance(value, bool) and value >= minimum

    def offset_datetime(value):
        if not isinstance(value, str) or not value:
            return False
        try:
            return datetime.fromisoformat(value).tzinfo is not None
        except ValueError:
            return False

    if not isinstance(output.context, BatchContext):
        type_error("context", "context: BatchContext가 아님")
    else:
        errors.extend(validate_batch_context(output.context))
    if not isinstance(output.accepted, tuple):
        type_error("accepted", "accepted: tuple이 아님")
    if not isinstance(output.rejected, tuple):
        type_error("rejected", "rejected: tuple이 아님")

    metrics = output.metrics
    if not isinstance(metrics, Phase4Metrics) or any(
        not positive_int(getattr(metrics, field, None))
        for field in ("input_count", "accepted_count", "rejected_count", "excluded_count")
    ):
        type_error("metrics", "metrics: 0 이상 정수 네 개가 필요함")
    else:
        if metrics.input_count != metrics.accepted_count + metrics.rejected_count + metrics.excluded_count:
            errors.append(Violation("ACCOUNTING_INVALID", "SDEC-007", None, "input = accepted + rejected + excluded 조건 위반"))
        if isinstance(output.accepted, tuple) and isinstance(output.rejected, tuple) and (
            metrics.accepted_count != len(output.accepted) or metrics.rejected_count != len(output.rejected)
        ):
            errors.append(Violation("ACCOUNTING_INVALID", "SDEC-007", None, "metrics 행 수 불일치"))
        context_count = getattr(output.context, "actual_input_count", None)
        if context_count is not None and metrics.input_count != context_count:
            errors.append(Violation("ACCOUNTING_INVALID", "SDEC-007", "actual_input_count", "context와 output metrics의 입력 건수 불일치"))

    fingerprints = set()
    for row in output.accepted if isinstance(output.accepted, tuple) else ():
        if not isinstance(row, StandardizedRecord):
            type_error("accepted", "accepted 항목: StandardizedRecord가 아님")
            continue
        lineage = row.lineage
        if not isinstance(lineage, RecordLineage) or not (
            positive_int(getattr(lineage, "record_id", None))
            and positive_int(getattr(lineage, "source_row_no", None), 1)
            and isinstance(getattr(lineage, "source_record_sha256", None), str)
            and re.fullmatch(r"[0-9a-fA-F]{64}", lineage.source_record_sha256)
            and positive_int(getattr(lineage, "release_slot", None))
            and offset_datetime(getattr(lineage, "scheduled_release_at", None))
        ):
            type_error("accepted.lineage", "accepted lineage: schema 타입 또는 형식 오류")

        business = row.business
        if not isinstance(business, StandardizedBusinessRecord):
            type_error("accepted.business", "accepted business: StandardizedBusinessRecord가 아님")
            continue
        business_data = asdict(business)
        invalid_business_shape = (
            set(business_data) != set(STANDARD_FIELDS)
            or any(
                not isinstance(value, str) or not value
                for field, value in business_data.items()
                if field not in nullable_business_fields
            )
            or any(
                value is not None and (not isinstance(value, str) or not value)
                for field, value in business_data.items()
                if field in nullable_business_fields
            )
        )
        if invalid_business_shape:
            type_error("accepted.business", "accepted business: schema 타입 또는 필수값 오류")
            continue
        for code, detail in validate_clean_row(business_data):
            errors.append(Violation(code, "SDEC-007", None, detail))
        errors.extend(validate_top_level_rule(business_data))
        if not isinstance(row.record_fingerprint, str) or re.fullmatch(r"[0-9a-fA-F]{64}", row.record_fingerprint) is None:
            type_error("accepted.record_fingerprint", "record_fingerprint: SHA-256 hex 형식이 아님")
        elif row.record_fingerprint != make_record_fingerprint(business, output.context.contract_version):
            errors.append(Violation("FINGERPRINT_INVALID", "SDEC-007", None, "record fingerprint 불일치"))
        if row.record_fingerprint in fingerprints:
            errors.append(Violation("DUPLICATE_FINGERPRINT", "SDEC-008", None, "동일 fingerprint 중복"))
        fingerprints.add(row.record_fingerprint)

    for row in output.rejected if isinstance(output.rejected, tuple) else ():
        if not isinstance(row, RejectedRecord):
            type_error("rejected", "rejected 항목: RejectedRecord가 아님")
            continue
        observed = row.observed_lineage
        if not isinstance(observed, ObservedLineage) or not positive_int(getattr(observed, "batch_record_index", None), 1):
            type_error("rejected.observed_lineage", "observed_lineage: schema 타입 또는 batch 위치 오류")
        if not isinstance(row.raw_json, str) or len(row.raw_json) < 2:
            type_error("rejected.raw_json", "raw_json: 비어 있지 않은 문자열이 아님")
        if not isinstance(row.violations, tuple) or not row.violations or any(
            not isinstance(violation, Violation)
            or not isinstance(violation.code, str)
            or not violation.code
            or not isinstance(violation.rule_id, str)
            or not violation.rule_id
            or violation.field is not None and not isinstance(violation.field, str)
            or not isinstance(violation.detail, str)
            or not violation.detail
            for violation in row.violations
        ):
            type_error("rejected.violations", "violations: schema 타입 또는 필수값 오류")
    return tuple(dict.fromkeys(errors))


class Phase4Processor:
    """In-memory Phase 4 pipeline over the immutable batch contracts."""

    def __init__(self, contract_version="1", ruleset_version="1"):
        self.contract_version = str(contract_version)
        self.ruleset_version = str(ruleset_version)

    def process(self, request):
        context = request.batch.context
        context_errors = list(
            validate_batch_context(context, expected_contract_version=self.contract_version)
        )
        actual_input_count = (
            context.get("actual_input_count")
            if isinstance(context, Mapping)
            else getattr(context, "actual_input_count", None)
        )
        if actual_input_count != len(request.batch.records):
            context_errors.append(
                Violation(
                    "BATCH_COUNT_INVALID",
                    "SDEC-003",
                    "actual_input_count",
                    "context 행 수와 records 수가 다름",
                )
            )
        context_errors = list(dict.fromkeys(context_errors))
        if context_errors:
            raise BatchContractError(context_errors)

        snapshot_errors = validate_reference_snapshot(request.reference_snapshot, context)
        if snapshot_errors:
            raise BatchContractError(snapshot_errors)

        records = tuple(request.batch.records)
        candidates, structural_rejected = partition_structural_results(records)
        candidate_ids = {id(record) for record in candidates}
        standardized = []
        rejected = list(structural_rejected)

        for source_index, source in enumerate(records, start=1):
            if id(source) not in candidate_ids:
                continue
            business, mapping_errors = map_business_record(
                source["payload"], request.reference_snapshot.canonical_area_ids
            )
            hierarchy_errors = validate_top_level_rule(business)
            parent_errors = validate_parent_presence(
                business, request.reference_snapshot.canonical_area_ids
            )
            errors = tuple(dict.fromkeys((*mapping_errors, *hierarchy_errors, *parent_errors)))
            observed = collect_observed_lineage(source, source_index)
            raw_json = serialize_raw_record(source)
            if errors:
                rejected.append(
                    RejectedRecord(
                        observed_lineage=observed,
                        raw_json=raw_json,
                        violations=errors,
                    )
                )
                continue

            lineage = RecordLineage(
                record_id=observed.record_id,
                source_row_no=observed.source_row_no,
                source_record_sha256=observed.source_record_sha256,
                release_slot=observed.release_slot,
                scheduled_release_at=observed.scheduled_release_at,
            )
            contract_version = (
                context.get("contract_version")
                if isinstance(context, Mapping)
                else context.contract_version
            )
            business_record = StandardizedBusinessRecord(**business)
            standardized.append(
                (
                    source_index,
                    source,
                    StandardizedRecord(
                        lineage=lineage,
                        business=business_record,
                        record_fingerprint=make_record_fingerprint(
                            business_record, contract_version
                        ),
                    ),
                )
            )

        records_only = [row for _, _, row in standardized]
        conflicts = detect_batch_conflicts(records_only)
        conflicted_positions = reject_conflict_groups(conflicts)
        conflict_code_by_label = {
            "employee": "EMPLOYEE_KEY_CONFLICT",
            "area": "AREA_KEY_CONFLICT",
            "parent_lookup": "TOP_LOOKUP_KEY_CONFLICT",
            "join_reference": "JOIN_REFERENCE_KEY_CONFLICT",
        }
        conflict_errors = {}
        for key, positions in conflicts.items():
            label = key.split(":", 1)[0]
            violation = Violation(
                conflict_code_by_label[label],
                "SDEC-007",
                label,
                f"{key}: 상충 fingerprint 그룹 전체 Reject",
            )
            for position in positions:
                conflict_errors.setdefault(position, []).append(violation)

        accepted = []
        for position, (source_index, source, row) in enumerate(standardized, start=1):
            if position not in conflicted_positions:
                accepted.append(row)
                continue
            rejected.append(
                RejectedRecord(
                    observed_lineage=collect_observed_lineage(source, source_index),
                    raw_json=serialize_raw_record(source),
                    violations=tuple(dict.fromkeys(conflict_errors[position])),
                )
            )

        if not validate_structural_accounting(len(records), len(accepted), len(rejected), 0):
            raise BatchContractError(
                (
                    Violation(
                        "ACCOUNTING_INVALID",
                        "SDEC-007",
                        None,
                        "Phase4 행 accounting 불일치",
                    ),
                )
            )
        output = build_phase4_output(context, accepted, rejected)
        output_errors = validate_phase4_output(output)
        if output_errors:
            raise BatchContractError(output_errors)
        return output


def main():
    """Run the established 15-field CSV standardization pipeline."""
    mappings = read_mappings(MAPPING_FILE)
    columns = standard_columns(mappings)
    with INPUT_FILE.open(encoding="utf-8") as file:
        records = json.load(file)

    accepted = []
    rejected = []
    rejection_code_counts = Counter()
    conversion_failure_counts = Counter()

    for index, record in enumerate(records, start=1):
        cleaned_row, errors, conversion_failures = map_legacy_payload(
            record.get("payload") or {}, mappings
        )
        conversion_failure_counts.update(conversion_failures)
        for error in validate_clean_row(cleaned_row):
            if error not in errors:
                errors.append(error)

        if errors:
            error_codes = list(dict.fromkeys(code for code, _ in errors))
            rejection_code_counts.update(error_codes)
            rejected.append(
                create_rejection(record.get("source_row_no", index), errors, cleaned_row)
            )
        else:
            accepted.append(cleaned_row)

    with CLEANED_FILE.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(accepted)
    with REJECTED_FILE.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["source_row_no", "error_codes", "error_details", *columns],
        )
        writer.writeheader()
        writer.writerows(rejected)

    validation = build_cleansing_validation(
        records,
        accepted,
        rejected,
        columns,
        rejection_code_counts,
        conversion_failure_counts,
    )
    with VALIDATION_FILE.open("w", encoding="utf-8") as file:
        json.dump(validation, file, ensure_ascii=False, indent=2)
        file.write("\n")
    print(json.dumps(validation, ensure_ascii=False, indent=2))


__all__ = [
    "AREA_ID_NAME_COLUMNS",
    "AREA_NAME_COLUMNS",
    "BASE_DIR",
    "CANONICAL_AREA_IDS",
    "CANONICAL_AREA_NAMES",
    "CLEANED_FILE",
    "DATE_FORMATS",
    "INPUT_FILE",
    "MAPPING_FILE",
    "NULL_TOKENS",
    "Phase4Processor",
    "PROJECT_DIR",
    "REJECTED_FILE",
    "TRANSFORMS",
    "VALIDATION_FILE",
    "build_phase4_output",
    "canonicalize_area_name",
    "clean_text",
    "main",
    "map_active_flag",
    "map_business_record",
    "map_top_level",
    "normalize_identifier",
    "normalize_name",
    "parse_to_iso_datetime",
    "validate_clean_row",
    "validate_phase4_output",
]


if __name__ == "__main__":
    main()
