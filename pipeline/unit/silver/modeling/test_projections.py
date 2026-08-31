"""표준 Flat 행의 네 개 Silver 모델 투영 계약을 검증한다."""

from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

from src.silver.modeling import (
    AREA_MODEL_CONFLICT,
    EMPLOYEE_MODEL_CONFLICT,
    JOIN_REFERENCE_MODEL_CONFLICT,
    MODEL_KEY_MISSING,
    MODEL_SPECS,
    PARENT_AREA_MODEL_CONFLICT,
    STANDARDIZED_FIELDS,
    build_normalization_projection,
)


def _flat_row(
    source_id: str,
    record_id: int,
    **changes: object,
) -> dict[str, object]:
    """모든 표준 Flat 필드를 가진 테스트 행을 만든다."""
    row: dict[str, object] = {
        "source_id": source_id,
        "record_id": record_id,
        "area_id": "BIZ_00009",
        "area_name": "보안",
        "parent_area_id": "BIZ_00004",
        "parent_area_name": "기획",
        "top_area_id": "BIZ_00004",
        "top_area_name": "기획",
        "top_area_level_code": "TOP_LEVEL",
        "employee_id": "EMP000038",
        "employee_name": "이민서",
        "employee_department_name": "분석팀",
        "employee_position_name": "팀장",
        "employee_hire_datetime": "2021-12-01T05:30:46",
        "employee_status_code": "ACTIVE",
        "area_registration_date": "2018-10-25T09:31:19",
        "top_area_registration_date": "2019-11-04T00:52:02",
    }
    row.update(changes)
    return row


def test_one_flat_row_projects_exact_four_model_contracts() -> None:
    """네 모델의 이름, 파일, key, 컬럼과 lineage 제외 계약을 고정한다."""
    expected_specs = {
        "silver_employee": {
            "filename": "silver_employee.csv",
            "fields": (
                "employee_id",
                "employee_name",
                "employee_department_name",
                "employee_position_name",
                "employee_hire_datetime",
                "employee_status_code",
            ),
            "key_fields": ("employee_id",),
            "reason_code": EMPLOYEE_MODEL_CONFLICT,
        },
        "silver_area": {
            "filename": "silver_area.csv",
            "fields": (
                "area_id",
                "area_name",
                "parent_area_id",
                "employee_id",
                "area_registration_date",
            ),
            "key_fields": ("area_id",),
            "reason_code": AREA_MODEL_CONFLICT,
        },
        "silver_parent_area": {
            "filename": "silver_parent_area.csv",
            "fields": (
                "top_area_id",
                "top_area_name",
                "top_area_level_code",
                "top_area_registration_date",
            ),
            "key_fields": ("top_area_id",),
            "reason_code": PARENT_AREA_MODEL_CONFLICT,
        },
        "silver_area_join_reference": {
            "filename": "silver_area_join_reference.csv",
            "fields": (
                "area_id",
                "parent_area_id",
                "parent_area_name",
                "employee_id",
                "employee_name",
                "employee_department_name",
                "employee_position_name",
                "employee_hire_datetime",
                "employee_status_code",
            ),
            "key_fields": ("area_id", "employee_id"),
            "reason_code": JOIN_REFERENCE_MODEL_CONFLICT,
        },
    }

    projection = build_normalization_projection((_flat_row("source-1", 1),))

    assert tuple(MODEL_SPECS) == tuple(expected_specs)
    assert tuple(projection.model_rows) == tuple(expected_specs)
    for model_name, expected in expected_specs.items():
        spec = MODEL_SPECS[model_name]
        assert spec.filename == expected["filename"]
        assert spec.fields == expected["fields"]
        assert spec.key_fields == expected["key_fields"]
        assert spec.conflict_reason_code == expected["reason_code"]
        assert len(projection.model_rows[model_name]) == 1
        model_row = projection.model_rows[model_name][0]
        assert tuple(model_row) == expected["fields"]
        assert {"source_id", "record_id", "raw_json"}.isdisjoint(model_row)

    assert projection.rejects == ()
    assert projection.input_source_count == 1
    assert projection.accepted_source_count == 1
    assert projection.rejected_source_count == 0


def test_same_key_and_same_data_deduplicates_without_reject() -> None:
    """lineage만 다른 동일 모델 데이터는 모델별 한 행으로 축약한다."""
    projection = build_normalization_projection(
        (
            _flat_row("source-1", 1),
            _flat_row("source-2", 2),
        )
    )

    assert projection.rejects == ()
    assert projection.input_source_count == 2
    assert projection.accepted_source_count == 2
    assert projection.rejected_source_count == 0
    assert all(len(rows) == 1 for rows in projection.model_rows.values())


@pytest.mark.parametrize(
    ("changes", "expected_model", "expected_reason_code"),
    (
        (
            {
                "area_id": "BIZ_00010",
                "area_name": "분석",
                "employee_name": "박지수",
            },
            "silver_employee",
            EMPLOYEE_MODEL_CONFLICT,
        ),
        (
            {"area_name": "분석"},
            "silver_area",
            AREA_MODEL_CONFLICT,
        ),
        (
            {"top_area_name": "전략"},
            "silver_parent_area",
            PARENT_AREA_MODEL_CONFLICT,
        ),
        (
            {"parent_area_name": "전략"},
            "silver_area_join_reference",
            JOIN_REFERENCE_MODEL_CONFLICT,
        ),
    ),
    ids=("employee", "area", "parent-area", "join-reference"),
)
def test_conflicting_key_rejects_all_related_sources_from_all_models(
    changes: Mapping[str, object],
    expected_model: str,
    expected_reason_code: str,
) -> None:
    """동일 key의 상이 데이터는 first/last 선택 없이 관련 source 전체를 제외한다."""
    projection = build_normalization_projection(
        (
            _flat_row("source-1", 1),
            _flat_row("source-2", 2, **changes),
        )
    )

    assert {reject.source_id for reject in projection.rejects} == {
        "source-1",
        "source-2",
    }
    assert {reject.model_name for reject in projection.rejects} == {expected_model}
    assert {reject.reason_code for reject in projection.rejects} == {
        expected_reason_code
    }
    assert all(rows == () for rows in projection.model_rows.values())
    assert projection.input_source_count == 2
    assert projection.accepted_source_count == 0
    assert projection.rejected_source_count == 2

    if expected_model == "silver_area_join_reference":
        assert {reject.model_key for reject in projection.rejects} == {
            '{"area_id":"BIZ_00009","employee_id":"EMP000038"}'
        }
        for reject in projection.rejects:
            standardized = json.loads(reject.standardized_json)
            assert tuple(standardized) == STANDARDIZED_FIELDS
            assert len(standardized) == 15
            assert {"source_id", "record_id", "raw_json"}.isdisjoint(standardized)


@pytest.mark.parametrize(
    ("missing_field", "expected_models"),
    (
        ("area_id", {"silver_area", "silver_area_join_reference"}),
        ("top_area_id", {"silver_parent_area"}),
        ("employee_id", {"silver_employee", "silver_area_join_reference"}),
    ),
    ids=("area-id", "top-area-id", "employee-id"),
)
def test_missing_model_key_routes_source_to_normalization_reject(
    missing_field: str,
    expected_models: set[str],
) -> None:
    """빈 모델 key는 해당 모델 Reject를 만들고 source를 네 모델에서 제외한다."""
    projection = build_normalization_projection(
        (_flat_row("source-1", 1, **{missing_field: ""}),)
    )

    assert {reject.model_name for reject in projection.rejects} == expected_models
    assert {reject.reason_code for reject in projection.rejects} == {MODEL_KEY_MISSING}
    assert all(rows == () for rows in projection.model_rows.values())
    assert projection.input_source_count == 1
    assert projection.accepted_source_count == 0
    assert projection.rejected_source_count == 1


def test_accounting_counts_distinct_sources_not_reject_rows() -> None:
    """한 source의 복수 모델 Reject는 rejected source 한 건으로 계산한다."""
    projection = build_normalization_projection(
        (
            _flat_row("source-accepted", 1),
            _flat_row("source-rejected", 2, employee_id=""),
        )
    )

    rejected = [
        reject for reject in projection.rejects if reject.source_id == "source-rejected"
    ]
    assert len(rejected) == 2
    assert {reject.model_name for reject in rejected} == {
        "silver_employee",
        "silver_area_join_reference",
    }
    assert projection.input_source_count == 2
    assert projection.accepted_source_count == 1
    assert projection.rejected_source_count == 1
    assert (
        projection.input_source_count
        == projection.accepted_source_count + projection.rejected_source_count
    )

    for reject in rejected:
        standardized = json.loads(reject.standardized_json)
        assert tuple(standardized) == STANDARDIZED_FIELDS
        assert len(standardized) == 15
