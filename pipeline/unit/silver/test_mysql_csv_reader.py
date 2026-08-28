"""네 Silver 모델 CSV의 MySQL 입력 경계 단위 테스트."""

from __future__ import annotations

import csv
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import pytest

from src.silver.modeling.contracts import MODEL_SPECS
from src.silver.mysql_csv_reader import MySQLInputError, read_mysql_model_snapshot


def _valid_model_rows() -> dict[str, list[dict[str, str]]]:
    """네 모델 계약을 만족하는 최소 CSV 행을 반환한다."""

    return {
        "silver_employee": [
            {
                "employee_id": "EMP000001",
                "employee_name": "김민서",
                "employee_department_name": " 분석팀 ",
                "employee_position_name": "팀장",
                "employee_hire_datetime": "2021-12-01T05:30:46",
                "employee_status_code": "ACTIVE",
            }
        ],
        "silver_area": [
            {
                "area_id": "BIZ_00001",
                "area_name": "보안",
                "parent_area_id": "",
                "employee_id": "EMP000001",
                "area_registration_date": "2018-10-25T09:31:19",
            }
        ],
        "silver_parent_area": [
            {
                "top_area_id": "BIZ_00010",
                "top_area_name": "기획",
                "top_area_level_code": "TOP_LEVEL",
                "top_area_registration_date": "2019-11-04T00:52:02",
            }
        ],
        "silver_area_join_reference": [
            {
                "area_id": "BIZ_00001",
                "parent_area_id": "",
                "parent_area_name": "",
                "employee_id": "EMP000001",
                "employee_name": "김민서",
                "employee_department_name": " 분석팀 ",
                "employee_position_name": "팀장",
                "employee_hire_datetime": "2021-12-01T05:30:46",
                "employee_status_code": "ACTIVE",
            }
        ],
    }


def _write_csv(
    path: Path,
    fieldnames: Sequence[str],
    rows: Sequence[Mapping[str, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_all_models(
    models_dir: Path,
    rows: Mapping[str, Sequence[Mapping[str, str]]],
) -> None:
    """MODEL_SPECS의 네 파일과 ordered header를 그대로 기록한다."""

    for spec in MODEL_SPECS.values():
        _write_csv(models_dir / spec.filename, spec.fields, rows[spec.name])


def test_reads_exact_four_model_snapshot_and_converts_types(tmp_path: Path) -> None:
    """행 순서를 보존하며 datetime과 허용된 빈 nullable만 변환한다."""

    models_dir = tmp_path / "models"
    _write_all_models(models_dir, _valid_model_rows())

    snapshot = read_mysql_model_snapshot(models_dir)

    assert tuple(snapshot.rows) == tuple(MODEL_SPECS)
    assert snapshot.row_counts == {name: 1 for name in MODEL_SPECS}
    assert snapshot.columns == {name: spec.fields for name, spec in MODEL_SPECS.items()}
    assert snapshot.rows["silver_employee"] == (
        (
            "EMP000001",
            "김민서",
            " 분석팀 ",
            "팀장",
            datetime(2021, 12, 1, 5, 30, 46),
            "ACTIVE",
        ),
    )
    assert snapshot.rows["silver_area"][0][2] is None
    assert snapshot.rows["silver_area_join_reference"][0][1:3] == (None, None)
    for model_name, datetime_index in {
        "silver_employee": 4,
        "silver_area": 4,
        "silver_parent_area": 3,
        "silver_area_join_reference": 7,
    }.items():
        value = snapshot.rows[model_name][0][datetime_index]
        assert isinstance(value, datetime)
        assert value.tzinfo is None


def test_exact_header_order_mismatch_fails_closed(tmp_path: Path) -> None:
    """동일 컬럼이라도 ordered header가 다르면 전체 snapshot을 거부한다."""

    models_dir = tmp_path / "models"
    rows = _valid_model_rows()
    _write_all_models(models_dir, rows)
    spec = MODEL_SPECS["silver_employee"]
    _write_csv(
        models_dir / spec.filename, tuple(reversed(spec.fields)), rows[spec.name]
    )

    with pytest.raises(MySQLInputError, match="header"):
        read_mysql_model_snapshot(models_dir)


@pytest.mark.parametrize(
    "model_name,changed_field",
    [
        ("silver_employee", "employee_name"),
        ("silver_area_join_reference", "employee_name"),
    ],
)
def test_duplicate_single_or_composite_model_key_fails_closed(
    model_name: str,
    changed_field: str,
    tmp_path: Path,
) -> None:
    """단일 key와 join 복합 key 중복을 모두 적재 전에 거부한다."""

    models_dir = tmp_path / "models"
    rows = deepcopy(_valid_model_rows())
    duplicate = dict(rows[model_name][0])
    duplicate[changed_field] = "중복 key의 다른 값"
    rows[model_name].append(duplicate)
    _write_all_models(models_dir, rows)

    with pytest.raises(MySQLInputError, match="key가 중복"):
        read_mysql_model_snapshot(models_dir)


def test_nonnullable_blank_field_fails_closed(tmp_path: Path) -> None:
    """공백뿐인 non-nullable 필드는 빈 값으로 처리해 거부한다."""

    models_dir = tmp_path / "models"
    rows = _valid_model_rows()
    rows["silver_area"][0]["area_name"] = "  "
    _write_all_models(models_dir, rows)

    with pytest.raises(MySQLInputError, match="필수 필드"):
        read_mysql_model_snapshot(models_dir)


@pytest.mark.parametrize(
    "value,error_pattern",
    [
        ("not-a-datetime", "datetime 형식"),
        ("2021-12-01T05:30:46+09:00", "timezone"),
    ],
)
def test_malformed_or_timezone_aware_datetime_fails_closed(
    value: str,
    error_pattern: str,
    tmp_path: Path,
) -> None:
    """파싱 불가 또는 timezone-aware datetime은 snapshot에 들어가지 않는다."""

    models_dir = tmp_path / "models"
    rows = _valid_model_rows()
    rows["silver_employee"][0]["employee_hire_datetime"] = value
    _write_all_models(models_dir, rows)

    with pytest.raises(MySQLInputError, match=error_pattern):
        read_mysql_model_snapshot(models_dir)
