"""누적 표준 Flat 행을 네 개 Silver 모델과 정규화 Reject로 투영한다."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from .contracts import (
    FLAT_INPUT_FIELDS,
    MODEL_KEY_MISSING,
    MODEL_SPECS,
    STANDARDIZED_FIELDS,
    ModelSpec,
    NormalizationContractError,
    NormalizationProjection,
    NormalizationReject,
)


@dataclass(frozen=True, slots=True)
class _Candidate:
    """source lineage를 유지한 모델 투영 후보."""

    source_id: str
    row: Mapping[str, object]
    model_key: str
    data_signature: str


def build_normalization_projection(
    rows: Iterable[Mapping[str, object]],
) -> NormalizationProjection:
    """누적 표준 Flat 전체를 충돌 없는 네 모델 snapshot으로 만든다.

    모델 key가 없거나 동일 key에 서로 다른 모델 데이터가 연결된 source는
    정규화 Reject로 기록하고 네 모델 모두에서 제외한다. 동일 key와 동일 데이터는
    한 행으로 축약한다. 부분 증분에서 발생할 수 있는 parent/top-area orphan은
    Reject하지 않고 건수만 반환한다.
    """
    flat_rows = _validate_and_copy_flat_rows(rows)
    candidates = {model_name: [] for model_name in MODEL_SPECS}
    reject_index: dict[tuple[str, str, str, str], NormalizationReject] = {}
    rejected_source_ids: set[str] = set()

    for row in flat_rows:
        source_id = _require_source_id(row)
        standardized_json = _serialize_standardized_row(row)
        missing_keys = tuple(
            (
                spec,
                tuple(field for field in spec.key_fields if _is_missing(row[field])),
            )
            for spec in MODEL_SPECS.values()
        )
        missing_keys = tuple((spec, fields) for spec, fields in missing_keys if fields)
        if missing_keys:
            for spec, missing_key_fields in missing_keys:
                reject = NormalizationReject(
                    source_id=source_id,
                    record_id=row["record_id"],
                    model_name=spec.name,
                    model_key=_serialize_model_key(spec, row),
                    reason_code=MODEL_KEY_MISSING,
                    reason_detail=(
                        "필수 모델 key가 비어 있습니다: " + ",".join(missing_key_fields)
                    ),
                    standardized_json=standardized_json,
                )
                _add_reject(reject_index, reject)
            rejected_source_ids.add(source_id)
            continue

        for spec in MODEL_SPECS.values():
            model_key = _serialize_model_key(spec, row)
            candidates[spec.name].append(
                _Candidate(
                    source_id=source_id,
                    row=row,
                    model_key=model_key,
                    data_signature=_serialize_model_data(spec, row),
                )
            )

    for spec in MODEL_SPECS.values():
        groups = _group_candidates(candidates[spec.name])
        for model_key, group in groups.items():
            signatures = {candidate.data_signature for candidate in group}
            if len(signatures) < 2:
                continue
            for candidate in group:
                reject = NormalizationReject(
                    source_id=candidate.source_id,
                    record_id=candidate.row["record_id"],
                    model_name=spec.name,
                    model_key=model_key,
                    reason_code=spec.conflict_reason_code,
                    reason_detail=(
                        "동일 모델 key에 서로 다른 표준 데이터가 있습니다: "
                        f"model={spec.name}, variants={len(signatures)}"
                    ),
                    standardized_json=_serialize_standardized_row(candidate.row),
                )
                _add_reject(reject_index, reject)
                rejected_source_ids.add(candidate.source_id)

    input_source_ids = {_require_source_id(row) for row in flat_rows}
    accepted_source_ids = input_source_ids - rejected_source_ids
    model_rows = _build_model_rows(candidates, accepted_source_ids)
    orphan_counts = _count_orphans(model_rows)
    rejects = tuple(
        sorted(
            reject_index.values(),
            key=_reject_sort_key,
        )
    )
    projection = NormalizationProjection(
        model_rows=MappingProxyType(model_rows),
        rejects=rejects,
        input_source_count=len(input_source_ids),
        accepted_source_count=len(accepted_source_ids),
        rejected_source_count=len(rejected_source_ids),
        orphan_counts=MappingProxyType(orphan_counts),
    )
    _validate_internal_consistency(projection)
    return projection


def _validate_and_copy_flat_rows(
    rows: Iterable[Mapping[str, object]],
) -> tuple[dict[str, object], ...]:
    copied_rows: list[dict[str, object]] = []
    seen_source_ids: set[str] = set()
    for row_number, row in enumerate(rows, start=2):
        if not isinstance(row, Mapping):
            raise NormalizationContractError(
                f"누적 accept.csv row가 mapping이 아닙니다: row={row_number}"
            )
        missing_fields = tuple(field for field in FLAT_INPUT_FIELDS if field not in row)
        if missing_fields:
            raise NormalizationContractError(
                "누적 accept.csv 필수 컬럼이 없습니다: "
                f"row={row_number}, fields={','.join(missing_fields)}"
            )
        copied = {field: row[field] for field in FLAT_INPUT_FIELDS}
        source_id = _require_source_id(copied, row_number=row_number)
        if _is_missing(copied["record_id"]):
            raise NormalizationContractError(
                "누적 accept.csv record_id가 비어 있습니다: "
                f"row={row_number}, source_id={source_id}"
            )
        if source_id in seen_source_ids:
            raise NormalizationContractError(
                "누적 accept.csv source_id가 중복되었습니다: "
                f"row={row_number}, source_id={source_id}"
            )
        seen_source_ids.add(source_id)
        copied_rows.append(copied)
    return tuple(copied_rows)


def _require_source_id(
    row: Mapping[str, object],
    *,
    row_number: int | None = None,
) -> str:
    source_id = row.get("source_id")
    if type(source_id) is not str or not source_id.strip():
        location = "" if row_number is None else f": row={row_number}"
        raise NormalizationContractError(
            "누적 accept.csv source_id는 비어 있지 않은 문자열이어야 합니다" + location
        )
    return source_id


def _is_missing(value: object) -> bool:
    return value is None or (type(value) is str and not value.strip())


def _serialize_model_key(spec: ModelSpec, row: Mapping[str, object]) -> str:
    values = {
        field: "" if _is_missing(row[field]) else str(row[field])
        for field in spec.key_fields
    }
    if len(spec.key_fields) == 1:
        return values[spec.key_fields[0]]
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def _serialize_standardized_row(row: Mapping[str, object]) -> str:
    return _serialize_ordered_values(STANDARDIZED_FIELDS, row)


def _serialize_model_data(spec: ModelSpec, row: Mapping[str, object]) -> str:
    return _serialize_ordered_values(spec.fields, row)


def _serialize_ordered_values(
    fields: Sequence[str],
    row: Mapping[str, object],
) -> str:
    values = {field: row[field] for field in fields}
    try:
        return json.dumps(
            values,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise NormalizationContractError(
            "누적 accept.csv 표준값을 JSON으로 직렬화할 수 없습니다."
        ) from error


def _add_reject(
    reject_index: dict[tuple[str, str, str, str], NormalizationReject],
    reject: NormalizationReject,
) -> None:
    key = (
        reject.source_id,
        reject.model_name,
        reject.model_key,
        reject.reason_code,
    )
    reject_index.setdefault(key, reject)


def _group_candidates(
    candidates: Sequence[_Candidate],
) -> dict[str, list[_Candidate]]:
    groups: dict[str, list[_Candidate]] = {}
    for candidate in candidates:
        groups.setdefault(candidate.model_key, []).append(candidate)
    return groups


def _build_model_rows(
    candidates: Mapping[str, Sequence[_Candidate]],
    accepted_source_ids: set[str],
) -> dict[str, tuple[dict[str, object], ...]]:
    outputs: dict[str, tuple[dict[str, object], ...]] = {}
    for spec in MODEL_SPECS.values():
        rows_by_key: dict[str, dict[str, object]] = {}
        for candidate in candidates[spec.name]:
            if candidate.source_id not in accepted_source_ids:
                continue
            rows_by_key.setdefault(
                candidate.model_key,
                {field: candidate.row[field] for field in spec.fields},
            )
        outputs[spec.name] = tuple(rows_by_key[key] for key in sorted(rows_by_key))
    return outputs


def _count_orphans(
    model_rows: Mapping[str, Sequence[Mapping[str, object]]],
) -> dict[str, int]:
    area_ids = {str(row["area_id"]) for row in model_rows["silver_area"]}
    parent_area_orphan_count = sum(
        1
        for row in model_rows["silver_area"]
        if not _is_missing(row["parent_area_id"])
        and str(row["parent_area_id"]) not in area_ids
    )
    top_area_orphan_count = sum(
        1
        for row in model_rows["silver_parent_area"]
        if str(row["top_area_id"]) not in area_ids
    )
    return {
        "parent_area_id": parent_area_orphan_count,
        "top_area_id": top_area_orphan_count,
    }


def _reject_sort_key(reject: NormalizationReject) -> tuple[object, ...]:
    model_order = {name: index for index, name in enumerate(MODEL_SPECS)}
    return (
        reject.source_id,
        model_order[reject.model_name],
        reject.model_key,
        reject.reason_code,
        str(reject.record_id),
    )


def _validate_internal_consistency(projection: NormalizationProjection) -> None:
    if tuple(projection.model_rows) != tuple(MODEL_SPECS):
        raise NormalizationContractError("네 개 모델 출력 구성이 고정 계약과 다릅니다.")

    for spec in MODEL_SPECS.values():
        seen_keys: set[str] = set()
        for row in projection.model_rows[spec.name]:
            if tuple(row) != spec.fields:
                raise NormalizationContractError(
                    f"{spec.name} 컬럼 순서가 고정 계약과 다릅니다."
                )
            key = _serialize_model_key(spec, row)
            if not key or key in seen_keys:
                raise NormalizationContractError(
                    f"{spec.name} 모델 key가 비었거나 중복되었습니다."
                )
            seen_keys.add(key)

    rejected_source_ids = {reject.source_id for reject in projection.rejects}
    if projection.rejected_source_count != len(rejected_source_ids):
        raise NormalizationContractError(
            "정규화 rejected source accounting이 일치하지 않습니다."
        )
    if (
        projection.input_source_count
        != projection.accepted_source_count + projection.rejected_source_count
    ):
        raise NormalizationContractError(
            "정규화 source accounting이 일치하지 않습니다."
        )

    employees = {
        str(row["employee_id"]): row for row in projection.model_rows["silver_employee"]
    }
    areas = {str(row["area_id"]): row for row in projection.model_rows["silver_area"]}
    join_keys: set[tuple[str, str]] = set()
    referenced_employee_ids: set[str] = set()
    for area in areas.values():
        employee_id = str(area["employee_id"])
        if employee_id not in employees:
            raise NormalizationContractError(
                "silver_area가 존재하지 않는 employee_id를 참조합니다."
            )
        referenced_employee_ids.add(employee_id)

    employee_snapshot_fields = (
        "employee_id",
        "employee_name",
        "employee_department_name",
        "employee_position_name",
        "employee_hire_datetime",
        "employee_status_code",
    )
    for joined in projection.model_rows["silver_area_join_reference"]:
        area_id = str(joined["area_id"])
        employee_id = str(joined["employee_id"])
        area = areas.get(area_id)
        employee = employees.get(employee_id)
        if area is None or employee is None:
            raise NormalizationContractError(
                "join reference가 존재하지 않는 area 또는 employee를 참조합니다."
            )
        join_keys.add((area_id, employee_id))
        if joined["parent_area_id"] != area["parent_area_id"]:
            raise NormalizationContractError(
                "join reference의 parent_area_id가 silver_area와 다릅니다."
            )
        if any(joined[field] != employee[field] for field in employee_snapshot_fields):
            raise NormalizationContractError(
                "join reference의 직원 snapshot이 silver_employee와 다릅니다."
            )

    expected_join_keys = {
        (area_id, str(area["employee_id"])) for area_id, area in areas.items()
    }
    if join_keys != expected_join_keys:
        raise NormalizationContractError(
            "silver_area와 join reference의 모델 key 집합이 다릅니다."
        )
    if referenced_employee_ids != set(employees):
        raise NormalizationContractError(
            "silver_employee와 silver_area의 참조 key 집합이 다릅니다."
        )

    if dict(projection.orphan_counts) != _count_orphans(projection.model_rows):
        raise NormalizationContractError(
            "report-only orphan accounting이 일치하지 않습니다."
        )


__all__ = ["build_normalization_projection"]
