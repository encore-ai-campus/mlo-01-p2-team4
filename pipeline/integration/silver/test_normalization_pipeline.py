"""Atlas fake부터 정규화 snapshot까지의 Silver 통합 경계를 검증한다."""

from __future__ import annotations

import csv
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from src.bronze.atlas_download import AtlasSettings
from src.silver.flat_pipeline import run_atlas_cleaning, write_clean_flat_data
from src.silver.modeling import (
    EMPLOYEE_MODEL_CONFLICT,
    MODEL_SPECS,
    NORMALIZATION_REJECT_FIELDS,
)
from src.silver.normalizer import SEOUL

VALIDATION_NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=SEOUL)


def _raw_record(
    source_id: str,
    record_id: int,
    *,
    area_no: str,
    employee_no: str,
    employee_name: str,
    top_area_no: str = "BIZ 00004",
) -> dict[str, Any]:
    """Flat 표준화를 통과하는 Atlas 원본 한 건을 만든다."""
    return {
        "_id": source_id,
        "record_id": record_id,
        "payload": {
            "area_no": area_no,
            "area_nm": "보안관리 49",
            "p_area_no": None,
            "p_area_nm": "",
            "top_area_no": top_area_no,
            "top_area_nm": "기획\t",
            "top_area_lvl": "top level",
            "mgr_no": employee_no,
            "mgr_nm": employee_name,
            "mgr_dept_nm": "분 석팀",
            "mgr_pos_nm": "\u3000팀장\u3000",
            "mgr_hire_dtm": "2021-12-01T05:30:46",
            "mgr_act_yn": "사용",
            "area_reg_dtm": "2018-10-25T09:31:19",
            "top_area_reg_dtm": "2019-11-04 00:52:02.0",
        },
    }


def _settings() -> AtlasSettings:
    """network를 사용하지 않는 Atlas 설정을 반환한다."""
    return AtlasSettings(
        uri="mongodb://atlas.invalid",
        database="test_database",
        collection="test_records",
    )


def _rows(path: Path) -> list[dict[str, str]]:
    """CSV를 header 기반 행 목록으로 읽는다."""
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def _normalization_paths(temp_dir: Path) -> tuple[Path, ...]:
    """정규화 게시 그룹의 정확한 다섯 경로를 반환한다."""
    return (
        temp_dir / "normalization_reject.csv",
        *(temp_dir / "models" / spec.filename for spec in MODEL_SPECS.values()),
    )


def _assert_exact_normalization_outputs(temp_dir: Path) -> tuple[Path, ...]:
    """summary 파일 없이 Reject 하나와 모델 네 개만 게시됐는지 확인한다."""
    expected = _normalization_paths(temp_dir)
    assert expected[0].is_file()
    assert {path for path in (temp_dir / "models").iterdir() if path.is_file()} == set(
        expected[1:]
    )
    assert all(path.is_file() for path in expected)
    assert not (temp_dir / "model_run_summary.json").exists()
    return expected


@dataclass(frozen=True, slots=True)
class _FakeBatch:
    """Bronze batch의 records 속성만 제공한다."""

    records: tuple[Mapping[str, Any], ...]


class _FakeAtlasPipeline:
    """Atlas batch와 checkpoint 호출을 기록하는 메모리 fake다."""

    def __init__(self, documents: Sequence[Mapping[str, Any]]) -> None:
        self.documents = tuple(documents)
        self.mark_calls: list[tuple[str, ...]] = []
        self.closed = False

    def iter_batches(self, *, limit: int) -> Iterator[_FakeBatch]:
        for start in range(0, len(self.documents), limit):
            yield _FakeBatch(records=self.documents[start : start + limit])

    def mark_processed(self, source_ids: Sequence[str]) -> None:
        self.mark_calls.append(tuple(source_ids))

    def close(self) -> None:
        self.closed = True


def test_flat_only_state_bootstraps_normalization_without_mark_processed(
    tmp_path: Path,
) -> None:
    """기존 Flat만 있어도 빈 Atlas 실행이 모델을 만들고 checkpoint는 건드리지 않는다."""
    temp_dir = tmp_path / "temp"
    existing = _raw_record(
        "source-existing",
        1,
        area_no="biz-10001",
        employee_no="emp000001",
        employee_name="이민서",
    )
    write_clean_flat_data(
        ((existing,),),
        temp_dir=temp_dir,
        validation_now=VALIDATION_NOW,
    )
    assert not (temp_dir / "normalization_reject.csv").exists()
    assert not (temp_dir / "models").exists()

    created: list[_FakeAtlasPipeline] = []

    def factory(
        settings: AtlasSettings,
        *,
        processed_ids_path: Path,
    ) -> _FakeAtlasPipeline:
        del settings, processed_ids_path
        pipeline = _FakeAtlasPipeline(())
        created.append(pipeline)
        return pipeline

    summary = run_atlas_cleaning(
        _settings(),
        temp_dir=temp_dir,
        validation_now=VALIDATION_NOW,
        pipeline_factory=factory,
    )

    paths = _assert_exact_normalization_outputs(temp_dir)
    assert _rows(paths[0]) == []
    assert all(len(_rows(path)) == 1 for path in paths[1:])
    assert summary.input_count == 0
    assert summary.source_ids == ()
    assert summary.normalization_input_source_count == 1
    assert summary.normalization_accepted_source_count == 1
    assert summary.normalization_rejected_source_count == 0
    assert created[0].mark_calls == []
    assert created[0].closed is True


def test_whitespace_variants_keep_composite_area_employee_join_keys(
    tmp_path: Path,
) -> None:
    """공백 표기만 다른 동일 직원은 한 직원과 두 개의 복합 Join key로 남는다."""
    whitespace_variant = _raw_record(
        "source-whitespace",
        1,
        area_no="BIZ_10001",
        employee_no="EMP000001",
        employee_name="이 민 서",
    )
    whitespace_variant["payload"].update(
        {
            "area_nm": "보 안 관 리 49",
            "p_area_no": "N/A",
            "p_area_nm": "미상",
            "top_area_nm": "기 획 3",
            "mgr_dept_nm": "분 석 팀",
            "mgr_pos_nm": "팀 장",
        }
    )
    canonical_variant = _raw_record(
        "source-canonical",
        2,
        area_no="BIZ_10002",
        employee_no="EMP000001",
        employee_name="이민서",
    )
    canonical_variant["payload"].update(
        {
            "mgr_dept_nm": "분석팀",
            "mgr_pos_nm": "팀장",
        }
    )
    documents = (whitespace_variant, canonical_variant)
    created: list[_FakeAtlasPipeline] = []

    def factory(
        settings: AtlasSettings,
        *,
        processed_ids_path: Path,
    ) -> _FakeAtlasPipeline:
        del settings, processed_ids_path
        pipeline = _FakeAtlasPipeline(documents)
        created.append(pipeline)
        return pipeline

    temp_dir = tmp_path / "temp"
    summary = run_atlas_cleaning(
        _settings(),
        temp_dir=temp_dir,
        validation_now=VALIDATION_NOW,
        pipeline_factory=factory,
    )

    paths = _assert_exact_normalization_outputs(temp_dir)
    assert _rows(paths[0]) == []
    flat_rows = _rows(temp_dir / "accept.csv")
    assert [
        (
            row["area_name"],
            row["parent_area_id"],
            row["parent_area_name"],
            row["top_area_name"],
            row["employee_name"],
            row["employee_department_name"],
            row["employee_position_name"],
        )
        for row in flat_rows
    ] == [
        ("보안", "", "", "기획", "이민서", "분석팀", "팀장"),
        ("보안", "", "", "기획", "이민서", "분석팀", "팀장"),
    ]

    model_rows = {
        spec.name: _rows(path)
        for spec, path in zip(MODEL_SPECS.values(), paths[1:], strict=True)
    }
    assert MODEL_SPECS["silver_area_join_reference"].key_fields == (
        "area_id",
        "employee_id",
    )
    assert len(model_rows["silver_employee"]) == 1
    assert {row["area_id"] for row in model_rows["silver_area"]} == {
        "BIZ_10001",
        "BIZ_10002",
    }
    assert {
        (row["area_id"], row["employee_id"])
        for row in model_rows["silver_area_join_reference"]
    } == {
        ("BIZ_10001", "EMP000001"),
        ("BIZ_10002", "EMP000001"),
    }
    assert summary.normalization_input_source_count == 2
    assert summary.normalization_accepted_source_count == 2
    assert summary.normalization_rejected_source_count == 0
    assert created[0].mark_calls == [("source-whitespace", "source-canonical")]
    assert created[0].closed is True


def test_atlas_flat_normalization_outputs_conflicts_and_source_accounting(
    tmp_path: Path,
) -> None:
    """Atlas 원본이 Flat과 정규화를 거쳐 충돌 source 전체를 모델에서 제외한다."""
    documents = (
        _raw_record(
            "source-accepted",
            1,
            area_no="biz-10001",
            employee_no="emp000001",
            employee_name="이민서",
        ),
        _raw_record(
            "source-conflict-a",
            2,
            area_no="biz-10002",
            employee_no="emp000002",
            employee_name="김충돌",
            top_area_no="BIZ 00005",
        ),
        _raw_record(
            "source-conflict-b",
            3,
            area_no="biz-10003",
            employee_no="emp000002",
            employee_name="박충돌",
            top_area_no="BIZ 00005",
        ),
    )
    created: list[_FakeAtlasPipeline] = []

    def factory(
        settings: AtlasSettings,
        *,
        processed_ids_path: Path,
    ) -> _FakeAtlasPipeline:
        del settings, processed_ids_path
        pipeline = _FakeAtlasPipeline(documents)
        created.append(pipeline)
        return pipeline

    temp_dir = tmp_path / "temp"
    summary = run_atlas_cleaning(
        _settings(),
        batch_size=2,
        temp_dir=temp_dir,
        validation_now=VALIDATION_NOW,
        pipeline_factory=factory,
    )

    paths = _assert_exact_normalization_outputs(temp_dir)
    reject_header: tuple[str, ...]
    with paths[0].open(encoding="utf-8", newline="") as file:
        reject_reader = csv.DictReader(file)
        reject_header = tuple(reject_reader.fieldnames or ())
        normalization_rejects = list(reject_reader)
    assert reject_header == NORMALIZATION_REJECT_FIELDS
    assert {row["source_id"] for row in normalization_rejects} == {
        "source-conflict-a",
        "source-conflict-b",
    }
    assert {row["model_name"] for row in normalization_rejects} == {"silver_employee"}
    assert {row["reason_code"] for row in normalization_rejects} == {
        EMPLOYEE_MODEL_CONFLICT
    }

    model_rows = {
        spec.name: _rows(path)
        for spec, path in zip(MODEL_SPECS.values(), paths[1:], strict=True)
    }
    for spec in MODEL_SPECS.values():
        keys = [
            tuple(row[field] for field in spec.key_fields)
            for row in model_rows[spec.name]
        ]
        assert len(keys) == len(set(keys))
        assert len(keys) == 1
    assert {row["employee_id"] for row in model_rows["silver_employee"]} == {
        "EMP000001"
    }
    assert {row["area_id"] for row in model_rows["silver_area"]} == {"BIZ_10001"}
    assert {row["top_area_id"] for row in model_rows["silver_parent_area"]} == {
        "BIZ_00004"
    }
    assert {
        (row["area_id"], row["employee_id"])
        for row in model_rows["silver_area_join_reference"]
    } == {("BIZ_10001", "EMP000001")}

    flat_accepts = _rows(temp_dir / "accept.csv")
    rejected_source_ids = {row["source_id"] for row in normalization_rejects}
    assert summary.input_count == 3
    assert summary.accepted_count == 3
    assert summary.rejected_count == 0
    assert summary.replayed_count == 0
    assert summary.input_count == (
        summary.accepted_count + summary.rejected_count + summary.replayed_count
    )
    assert summary.normalization_input_source_count == 3
    assert summary.normalization_accepted_source_count == 1
    assert summary.normalization_rejected_source_count == 2
    assert summary.normalization_input_source_count == (
        summary.normalization_accepted_source_count
        + summary.normalization_rejected_source_count
    )
    assert len({row["source_id"] for row in flat_accepts}) == (
        summary.normalization_accepted_source_count + len(rejected_source_ids)
    )
    assert created[0].mark_calls == [
        ("source-accepted", "source-conflict-a", "source-conflict-b")
    ]
    assert created[0].closed is True
