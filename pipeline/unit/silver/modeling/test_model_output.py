"""정규화 Reject와 네 개 Silver 모델의 snapshot 게시를 검증한다."""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

import pytest

import src.silver.modeling.model_output as model_output_module
from src.silver.modeling import (
    FLAT_INPUT_FIELDS,
    JOIN_REFERENCE_MODEL_CONFLICT,
    MODEL_SPECS,
    NORMALIZATION_REJECT_FIELDS,
    STANDARDIZED_FIELDS,
    NormalizationContractError,
    build_normalization_projection,
)
from src.silver.modeling.materializer import materialize_normalized_outputs
from src.silver.modeling.model_output import publish_normalization_outputs


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


def _write_csv(
    path: Path,
    fieldnames: Sequence[str],
    rows: Sequence[Mapping[str, object]],
) -> None:
    """지정된 계약으로 테스트 입력 CSV를 기록한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_accept(temp_dir: Path, rows: Sequence[Mapping[str, object]]) -> None:
    """고정 Flat 계약의 누적 accept.csv를 기록한다."""
    _write_csv(temp_dir / "accept.csv", FLAT_INPUT_FIELDS, rows)


def _read_csv(path: Path) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    """CSV header와 행을 함께 읽는다."""
    with path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        return tuple(reader.fieldnames or ()), list(reader)


def _normalization_paths(temp_dir: Path) -> tuple[Path, ...]:
    """다섯 정규화 출력의 고정 게시 순서를 반환한다."""
    return (
        temp_dir / "normalization_reject.csv",
        *(temp_dir / "models" / spec.filename for spec in MODEL_SPECS.values()),
    )


def _snapshot_bytes(paths: Sequence[Path]) -> dict[Path, bytes]:
    """게시 복원 및 결정성을 비교할 byte snapshot을 만든다."""
    return {path: path.read_bytes() for path in paths}


def test_header_only_materialization_publishes_exact_five_outputs(
    tmp_path: Path,
) -> None:
    """빈 누적 Flat도 정확한 header를 가진 Reject와 네 모델을 게시한다."""
    temp_dir = tmp_path / "temp"
    _write_accept(temp_dir, ())

    summary = materialize_normalized_outputs(temp_dir)

    paths = _normalization_paths(temp_dir)
    assert all(path.is_file() for path in paths)
    reject_fields, reject_rows = _read_csv(paths[0])
    assert reject_fields == NORMALIZATION_REJECT_FIELDS
    assert reject_rows == []
    for spec, path in zip(MODEL_SPECS.values(), paths[1:], strict=True):
        fields, rows = _read_csv(path)
        assert fields == spec.fields
        assert rows == []

    assert summary.input_source_count == 0
    assert summary.accepted_source_count == 0
    assert summary.rejected_source_count == 0
    assert summary.reject_row_count == 0
    assert summary.normalization_reject_path == paths[0]
    assert tuple(path for _, path in summary.model_paths) == paths[1:]


def test_snapshot_is_key_sorted_and_byte_stable(tmp_path: Path) -> None:
    """입력 순서와 무관하게 모델 key 정렬과 재실행 byte 결정성을 유지한다."""
    temp_dir = tmp_path / "temp"
    _write_accept(
        temp_dir,
        (
            _flat_row(
                "source-2",
                2,
                area_id="BIZ_00010",
                area_name="분석",
                top_area_id="BIZ_00005",
                top_area_name="데이터",
                employee_id="EMP000039",
                employee_name="박지수",
            ),
            _flat_row("source-1", 1),
        ),
    )

    first = materialize_normalized_outputs(temp_dir)
    paths = _normalization_paths(temp_dir)
    first_bytes = _snapshot_bytes(paths)
    second = materialize_normalized_outputs(temp_dir)

    assert first.input_source_count == second.input_source_count == 2
    assert _snapshot_bytes(paths) == first_bytes
    for spec, path in zip(MODEL_SPECS.values(), paths[1:], strict=True):
        _, rows = _read_csv(path)
        keys = [tuple(row[field] for field in spec.key_fields) for row in rows]
        assert keys == sorted(keys)
        assert len(keys) == len(set(keys))


def test_normalization_reject_serialization_contract(tmp_path: Path) -> None:
    """Reject가 compact model key와 표준 15개 필드 JSON을 보존한다."""
    temp_dir = tmp_path / "temp"
    _write_accept(
        temp_dir,
        (
            _flat_row("source-1", 1),
            _flat_row("source-2", 2, parent_area_name="전략"),
        ),
    )

    summary = materialize_normalized_outputs(temp_dir)

    fields, rows = _read_csv(temp_dir / "normalization_reject.csv")
    assert fields == NORMALIZATION_REJECT_FIELDS
    assert len(rows) == 2
    assert summary.reject_row_count == 2
    assert {row["reject_stage"] for row in rows} == {"NORMALIZATION"}
    assert {row["model_name"] for row in rows} == {"silver_area_join_reference"}
    assert {row["reason_code"] for row in rows} == {JOIN_REFERENCE_MODEL_CONFLICT}
    assert {row["model_key"] for row in rows} == {
        '{"area_id":"BIZ_00009","employee_id":"EMP000038"}'
    }
    for row in rows:
        standardized = json.loads(row["standardized_json"])
        assert tuple(standardized) == STANDARDIZED_FIELDS
        assert len(standardized) == 15
        assert {"source_id", "record_id", "raw_json"}.isdisjoint(standardized)


def test_invalid_accept_header_preserves_existing_output_group(
    tmp_path: Path,
) -> None:
    """누적 Flat header 오류는 기존 다섯 출력에 손대기 전에 차단한다."""
    temp_dir = tmp_path / "temp"
    valid_row = _flat_row("source-1", 1)
    _write_accept(temp_dir, (valid_row,))
    materialize_normalized_outputs(temp_dir)
    paths = _normalization_paths(temp_dir)
    before = _snapshot_bytes(paths)
    invalid_fields = FLAT_INPUT_FIELDS[:-1]
    _write_csv(
        temp_dir / "accept.csv",
        invalid_fields,
        ({field: valid_row[field] for field in invalid_fields},),
    )

    with pytest.raises(NormalizationContractError, match="header"):
        materialize_normalized_outputs(temp_dir)

    assert _snapshot_bytes(paths) == before


def test_duplicate_model_key_blocks_publication(tmp_path: Path) -> None:
    """모델 key 중복은 게시 전에 차단하고 기존 다섯 출력을 유지한다."""
    temp_dir = tmp_path / "temp"
    projection = build_normalization_projection((_flat_row("source-1", 1),))
    publish_normalization_outputs(temp_dir, projection)
    paths = _normalization_paths(temp_dir)
    before = _snapshot_bytes(paths)
    model_rows = dict(projection.model_rows)
    employee_row = model_rows["silver_employee"][0]
    model_rows["silver_employee"] = (employee_row, employee_row)
    invalid_projection = replace(projection, model_rows=model_rows)

    with pytest.raises(NormalizationContractError, match="key가 중복"):
        publish_normalization_outputs(temp_dir, invalid_projection)

    assert _snapshot_bytes(paths) == before


def test_source_accounting_mismatch_blocks_publication(tmp_path: Path) -> None:
    """source accounting 불일치는 기존 다섯 출력에 손대기 전에 차단한다."""
    temp_dir = tmp_path / "temp"
    projection = build_normalization_projection((_flat_row("source-1", 1),))
    publish_normalization_outputs(temp_dir, projection)
    paths = _normalization_paths(temp_dir)
    before = _snapshot_bytes(paths)
    invalid_projection = replace(projection, input_source_count=2)

    with pytest.raises(NormalizationContractError, match="source accounting"):
        publish_normalization_outputs(temp_dir, invalid_projection)

    assert _snapshot_bytes(paths) == before


def test_replace_failure_restores_all_five_previous_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """중간 파일 교체 실패 시 먼저 교체된 파일까지 이전 그룹으로 복원한다."""
    temp_dir = tmp_path / "temp"
    _write_accept(temp_dir, (_flat_row("source-1", 1),))
    materialize_normalized_outputs(temp_dir)
    paths = _normalization_paths(temp_dir)
    before = _snapshot_bytes(paths)
    _write_accept(
        temp_dir,
        (
            _flat_row("source-1", 1),
            _flat_row(
                "source-2",
                2,
                area_id="BIZ_00010",
                area_name="분석",
                top_area_id="BIZ_00005",
                top_area_name="데이터",
                employee_id="EMP000039",
                employee_name="박지수",
            ),
        ),
    )
    original_replace = model_output_module.os.replace
    failure_target = temp_dir / "models" / "silver_area.csv"

    def fail_area_replace(source: object, destination: object) -> None:
        if Path(destination) == failure_target:  # type: ignore[arg-type]
            raise OSError("simulated normalization publication failure")
        original_replace(source, destination)

    monkeypatch.setattr(model_output_module.os, "replace", fail_area_replace)

    with pytest.raises(OSError, match="simulated normalization publication failure"):
        materialize_normalized_outputs(temp_dir)

    assert _snapshot_bytes(paths) == before
