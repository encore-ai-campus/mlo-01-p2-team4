"""Silver 증분 cleaned-flat 파이프라인의 fake-only 단위 테스트."""

from __future__ import annotations

import csv
from collections.abc import Iterator, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from bson import ObjectId, json_util

import src.bronze.atlas_pipeline as atlas_pipeline_module
import src.silver.csv_output as csv_output_module
import src.silver.flat_pipeline as flat_pipeline_module
from src.bronze.atlas_download import AtlasSettings
from src.silver.flat_pipeline import run_atlas_cleaning, write_clean_flat_data
from src.silver.normalizer import SEOUL
from src.silver.preprocessor import BasicPreprocessor
from src.silver.rules import SilverRuleError, SilverRules
import src.silver.rules as rules_module

VALIDATION_NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=SEOUL)


def _valid_record(
    record_id: int,
    *,
    source_id: str | None = None,
    employee_name: str = "이민서",
) -> dict[str, Any]:
    """모든 표준화 규칙을 통과하는 Atlas 원본 한 건을 만든다."""
    return {
        "_id": source_id or f"source-{record_id}",
        "record_id": record_id,
        "payload": {
            "area_no": "biz-12345",
            "area_nm": "보안관리 49",
            "p_area_no": None,
            "p_area_nm": "",
            "top_area_no": "BIZ 00004",
            "top_area_nm": "기획\t",
            "top_area_lvl": "top level",
            "mgr_no": "emp000038",
            "mgr_nm": employee_name,
            "mgr_dept_nm": "분 석팀",
            "mgr_pos_nm": "\u3000팀장\u3000",
            "mgr_hire_dtm": "2021-12-01T05:30:46",
            "mgr_act_yn": "사용",
            "area_reg_dtm": "2018-10-25T09:31:19",
            "top_area_reg_dtm": "2019-11-04 00:52:02.0",
        },
    }


def _rows(path: Path) -> list[dict[str, str]]:
    """생성된 CSV를 header 기반 dictionary 목록으로 읽는다."""
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def _normalization_paths(temp_dir: Path) -> tuple[Path, ...]:
    """정규화 Reject와 네 모델의 고정 출력 경로를 반환한다."""
    return (
        temp_dir / "normalization_reject.csv",
        temp_dir / "models" / "silver_employee.csv",
        temp_dir / "models" / "silver_area.csv",
        temp_dir / "models" / "silver_parent_area.csv",
        temp_dir / "models" / "silver_area_join_reference.csv",
    )


def _file_bytes(paths: Sequence[Path]) -> dict[Path, bytes]:
    """여러 출력 파일의 byte snapshot을 만든다."""
    return {path: path.read_bytes() for path in paths}


def _write_rows(
    path: Path,
    fieldnames: Sequence[str],
    rows: Sequence[Mapping[str, object]],
) -> None:
    """구형·손상 CSV 상태를 만들기 위해 지정 header로 행을 기록한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _settings() -> AtlasSettings:
    """network에 연결되지 않는 테스트용 Atlas 설정을 만든다."""
    return AtlasSettings(
        uri="mongodb://atlas.invalid",
        database="test_database",
        collection="test_records",
    )


def test_main_logs_silver_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Silver CLI 요약이 공통 logger에 기존 key-value 형태로 전달된다."""
    messages: list[str] = []
    configure_calls = 0

    class CapturingLogger:
        def info(self, message: str, *args: object) -> None:
            messages.append(message % args)

    def configure() -> None:
        nonlocal configure_calls
        configure_calls += 1

    summary = SimpleNamespace(
        batch_count=2,
        input_count=3,
        accepted_count=2,
        rejected_count=1,
        first_rejected_count=1,
        duplicate_rejected_count=0,
        replayed_count=4,
        model_row_counts=(("silver_employee", 2), ("silver_area", 2)),
        orphan_counts=(("parent_area_id", 1),),
        normalization_input_source_count=2,
        normalization_accepted_source_count=2,
        normalization_rejected_source_count=0,
        normalization_reject_row_count=0,
        accept_path=tmp_path / "accept.csv",
        reject_path=tmp_path / "reject.csv",
    )

    def run_cleaning(
        settings: AtlasSettings,
        *,
        batch_size: int,
        temp_dir: Path,
    ) -> SimpleNamespace:
        assert settings == _settings()
        assert batch_size == 7
        assert temp_dir == tmp_path
        return summary

    monkeypatch.setattr(flat_pipeline_module, "configure_pipeline_logging", configure)
    monkeypatch.setattr(flat_pipeline_module, "LOGGER", CapturingLogger())
    monkeypatch.setattr(
        flat_pipeline_module.AtlasSettings,
        "from_environment",
        staticmethod(_settings),
    )
    monkeypatch.setattr(flat_pipeline_module, "run_atlas_cleaning", run_cleaning)

    assert (
        flat_pipeline_module.main(["--batch-size", "7", "--temp-dir", str(tmp_path)])
        == 0
    )
    assert configure_calls == 1
    assert messages == [
        "batches=2 input=3 accepted=2 rejected=1 "
        "first_rejected=1 duplicate_rejected=0 replayed=4",
        "normalization_input_sources=2 normalization_accepted_sources=2 "
        "normalization_rejected_sources=0 normalization_reject_rows=0 "
        "model_rows=silver_employee:2,silver_area:2 "
        "orphan_counts=parent_area_id:1",
        f"accept={tmp_path / 'accept.csv'} reject={tmp_path / 'reject.csv'}",
    ]


@dataclass(frozen=True, slots=True)
class FakeBatch:
    """Bronze 증분 batch의 `.records` 계약을 제공한다."""

    records: tuple[Mapping[str, Any], ...]


class FakeIncrementalPipeline:
    """오래된 미처리 source와 checkpoint 확정을 흉내 낸다."""

    def __init__(
        self,
        documents: Sequence[Mapping[str, Any]],
        processed: set[str],
        *,
        processed_ids_path: Path,
        fail_after_batches: int | None = None,
        fail_mark: bool = False,
    ) -> None:
        self.documents = tuple(deepcopy(document) for document in documents)
        self.processed = processed
        self.processed_ids_path = processed_ids_path
        self.fail_after_batches = fail_after_batches
        self.fail_mark = fail_mark
        self.mark_calls: list[tuple[str, ...]] = []
        self.closed = False

    def iter_batches(self, *, limit: int) -> Iterator[FakeBatch]:
        """입력 순서를 유지하며 아직 확정되지 않은 source만 n건씩 반환한다."""
        unprocessed = [
            document
            for document in self.documents
            if str(document["_id"]) not in self.processed
        ]
        emitted = 0
        for start in range(0, len(unprocessed), limit):
            yield FakeBatch(records=tuple(unprocessed[start : start + limit]))
            emitted += 1
            if self.fail_after_batches == emitted:
                raise RuntimeError("simulated Bronze batch failure")

    def mark_processed(self, source_ids: Sequence[str]) -> None:
        """호출을 기록하고 성공한 경우에만 공유 checkpoint를 전진시킨다."""
        observed = tuple(source_ids)
        self.mark_calls.append(observed)
        if self.fail_mark:
            raise RuntimeError("simulated checkpoint failure")
        self.processed.update(observed)

    def close(self) -> None:
        """Silver가 성공·실패와 무관하게 close하는지 기록한다."""
        self.closed = True


class IntegratedAtlasCollection:
    """실제 Bronze 증분 구현과 Silver를 연결하는 Mongo collection fake."""

    def __init__(self, documents: Sequence[Mapping[str, Any]]) -> None:
        self.documents = tuple(documents)
        self.calls: list[dict[str, object]] = []

    def find(
        self,
        query: Mapping[str, object],
        projection: Mapping[str, int] | None = None,
        *,
        sort: Sequence[tuple[str, int]],
        limit: int = 0,
    ) -> Iterator[Mapping[str, Any]]:
        """`_id` keyset, projection, 오름차순, server limit을 적용한다."""
        self.calls.append(
            {
                "query": dict(query),
                "projection": None if projection is None else dict(projection),
                "sort": tuple(sort),
                "limit": limit,
            }
        )
        documents = sorted(self.documents, key=lambda document: document["_id"])
        predicate = query.get("_id")
        if isinstance(predicate, Mapping) and "$gt" in predicate:
            lower_bound = predicate["$gt"]
            documents = [
                document for document in documents if document["_id"] > lower_bound
            ]
        if projection is not None:
            documents = ({"_id": document["_id"]} for document in documents)
        if limit:
            documents = list(documents)[:limit]
        return iter(documents)


class IntegratedAtlasClient:
    """실제 Bronze lazy-open·close 경계를 검증하는 client fake."""

    def __init__(self, collection: IntegratedAtlasCollection) -> None:
        self.collection = collection
        self.closed = False

    def __getitem__(self, name: str) -> object:
        if name == "test_database":
            return {"test_records": self.collection}
        return self.collection

    def close(self) -> None:
        self.closed = True


def test_basic_preprocessor_cleans_only_representation() -> None:
    """전처리는 NFKC·제어문자·공백만 정리하고 이상 토큰은 보존한다."""
    original = {
        "payload": {
            "name": "\u3000이\x00민\t 서\n",
            "placeholder": " \u200bUnknown ",
        }
    }

    cleaned = BasicPreprocessor().preprocess(original)

    assert cleaned == {"payload": {"name": "이민 서", "placeholder": "Unknown"}}
    assert original["payload"]["name"] == "\u3000이\x00민\t 서\n"


def test_flat_path_removes_nfkc_unicode_whitespace_from_six_name_fields(
    tmp_path: Path,
) -> None:
    """실제 Flat 경로가 NFKC 후 여섯 이름 필드의 Unicode 공백을 제거한다."""
    record = _valid_record(1)
    record["payload"].update(
        {
            "area_nm": "보\u3000안\u00a0관\t리\u3000４９",
            "p_area_no": "BIZ_00005",
            "p_area_nm": "데\u3000이\u00a0터\t７",
            "top_area_nm": "기\u3000획\u00a0３\t",
            "mgr_nm": "김\u3000민\u00a0수\tＡ",
            "mgr_dept_nm": "데\u3000이\u00a0터\t팀Ｂ",
            "mgr_pos_nm": "책\u3000임\u00a0Ｃ\t",
        }
    )

    summary = write_clean_flat_data(
        ((record,),),
        temp_dir=tmp_path / "temp",
        validation_now=VALIDATION_NOW,
    )

    assert summary.accepted_count == 1
    assert summary.rejected_count == 0
    assert [
        (
            row["area_name"],
            row["parent_area_name"],
            row["top_area_name"],
            row["employee_name"],
            row["employee_department_name"],
            row["employee_position_name"],
        )
        for row in _rows(summary.accept_path)
    ] == [("보안", "데이터", "기획", "김민수A", "데이터팀B", "책임C")]


def test_code_mapping_rejects_canonical_value_outside_allowed_values(
    tmp_path: Path,
) -> None:
    """YAML mapping 결과가 allowed_values 밖이면 규칙 로딩부터 중단한다."""
    mapping_path = tmp_path / "code-normalization.yaml"
    mapping_path.write_text(
        """\
unknown_action: reject
status:
  allowed_values: [ACTIVE, INACTIVE]
  source_values:
    \"Y\": ACTIVE_TYPO
level:
  allowed_values: [TOP_LEVEL]
  source_values:
    \"1\": TOP_LEVEL
""",
        encoding="utf-8",
    )

    with pytest.raises(SilverRuleError, match="allowed_values에 없습니다"):
        rules_module._load_code_mappings(mapping_path)


def test_pattern_and_null_like_failures_are_first_stage_rejects(
    tmp_path: Path,
) -> None:
    """패턴 실패, 이상 토큰, 미래 시각은 모두 1차 Reject로 남긴다."""
    invalid_pattern = _valid_record(1)
    invalid_pattern["payload"]["area_no"] = "AREA-12345"
    null_like = _valid_record(2, employee_name="Unknown")
    future = _valid_record(3)
    future["payload"]["mgr_hire_dtm"] = "2026-08-28T12:00:01"

    summary = write_clean_flat_data(
        ((invalid_pattern, null_like, future),),
        temp_dir=tmp_path / "temp",
        validation_now=VALIDATION_NOW,
    )

    rejected = _rows(summary.reject_path)
    assert summary.input_count == 3
    assert summary.accepted_count == 0
    assert summary.first_rejected_count == 3
    assert summary.duplicate_rejected_count == 0
    assert {row["reject_stage"] for row in rejected} == {"FIRST_STAGE"}
    assert "IDENTIFIER_INVALID" in rejected[0]["reason_codes"]
    assert "NULL_LIKE_VALUE" in rejected[1]["reason_codes"]
    assert "FUTURE_DATETIME" in rejected[2]["reason_codes"]


def test_first_reject_does_not_pollute_duplicate_keys_and_keeps_raw_json(
    tmp_path: Path,
) -> None:
    """1차 Reject의 표준 후보는 중복 key가 아니며 raw_json은 전처리 전이다."""
    invalid_lineage = _valid_record(0, source_id="source-invalid")
    invalid_lineage["payload"]["mgr_nm"] = "\u3000이\x00민서\u3000"
    valid = _valid_record(1, source_id="source-valid")
    duplicate = _valid_record(2, source_id="source-duplicate")

    summary = write_clean_flat_data(
        ((invalid_lineage, valid, duplicate),),
        temp_dir=tmp_path / "temp",
        validation_now=VALIDATION_NOW,
    )

    accepted = _rows(summary.accept_path)
    rejected = _rows(summary.reject_path)
    assert summary.input_count == 3
    assert summary.accepted_count == 1
    assert summary.first_rejected_count == 1
    assert summary.duplicate_rejected_count == 1
    assert summary.rejected_count == 2
    assert summary.replayed_count == 0
    assert [row["source_id"] for row in accepted] == ["source-valid"]
    assert [row["reject_stage"] for row in rejected] == [
        "FIRST_STAGE",
        "SECOND_STAGE",
    ]
    raw_reject = json_util.loads(rejected[0]["raw_json"])
    assert raw_reject["payload"]["mgr_nm"] == "\u3000이\x00민서\u3000"


def test_duplicate_detection_covers_same_and_later_batches(tmp_path: Path) -> None:
    """같은 batch와 다음 batch의 전체값 중복은 가장 오래된 행 뒤에서 Reject된다."""
    first = _valid_record(1)
    same_batch = _valid_record(2)
    later_batch = _valid_record(3)

    summary = write_clean_flat_data(
        ((first, same_batch), (later_batch,)),
        temp_dir=tmp_path / "temp",
        validation_now=VALIDATION_NOW,
    )

    assert summary.batch_count == 2
    assert summary.accepted_count == 1
    assert summary.first_rejected_count == 0
    assert summary.duplicate_rejected_count == 2
    assert [row["source_id"] for row in _rows(summary.accept_path)] == ["source-1"]
    rejected = _rows(summary.reject_path)
    assert [row["source_id"] for row in rejected] == ["source-2", "source-3"]
    assert {row["reject_stage"] for row in rejected} == {"SECOND_STAGE"}
    assert all(row["reason_codes"] == "DUPLICATE_NORMALIZED_ROW" for row in rejected)


def test_whitespace_and_nullable_placeholder_variants_become_full_row_duplicate(
    tmp_path: Path,
) -> None:
    """공백·부모 placeholder 차이는 같은 표준 full row의 후행 중복 Reject가 된다."""
    first = _valid_record(1)
    first["payload"].update(
        {
            "area_nm": "보안관리 49",
            "p_area_no": None,
            "p_area_nm": "",
            "top_area_nm": "기획",
            "mgr_nm": "이민서",
            "mgr_dept_nm": "분석팀",
            "mgr_pos_nm": "팀장",
        }
    )
    later_variant = _valid_record(2)
    later_variant["payload"].update(
        {
            "area_nm": "보 안 관 리 49",
            "p_area_no": "N/A",
            "p_area_nm": "미상",
            "top_area_nm": "기 획",
            "mgr_nm": "이 민 서",
            "mgr_dept_nm": "분 석 팀",
            "mgr_pos_nm": "팀 장",
        }
    )

    summary = write_clean_flat_data(
        ((first, later_variant),),
        temp_dir=tmp_path / "temp",
        validation_now=VALIDATION_NOW,
    )

    assert summary.input_count == 2
    assert summary.accepted_count == 1
    assert summary.first_rejected_count == 0
    assert summary.duplicate_rejected_count == 1
    assert [row["source_id"] for row in _rows(summary.accept_path)] == ["source-1"]
    rejected = _rows(summary.reject_path)
    assert [row["source_id"] for row in rejected] == ["source-2"]
    assert rejected[0]["reject_stage"] == "SECOND_STAGE"
    assert rejected[0]["reason_codes"] == "DUPLICATE_NORMALIZED_ROW"


def test_previous_accept_is_duplicate_and_only_new_distinct_row_is_added(
    tmp_path: Path,
) -> None:
    """이전 accept 전체값도 기준에 포함되고 다른 신규값만 append된다."""
    temp_dir = tmp_path / "temp"
    write_clean_flat_data(
        ((_valid_record(1),),),
        temp_dir=temp_dir,
        validation_now=VALIDATION_NOW,
    )
    previous_duplicate = _valid_record(2)
    distinct = _valid_record(3, employee_name="박지수")

    summary = write_clean_flat_data(
        ((previous_duplicate, distinct),),
        temp_dir=temp_dir,
        validation_now=VALIDATION_NOW,
    )

    assert summary.accepted_count == 1
    assert summary.first_rejected_count == 0
    assert summary.duplicate_rejected_count == 1
    assert [row["source_id"] for row in _rows(summary.accept_path)] == [
        "source-1",
        "source-3",
    ]
    assert [row["source_id"] for row in _rows(summary.reject_path)] == ["source-2"]


def test_batch_failure_preserves_csv_and_does_not_advance_processed_ids(
    tmp_path: Path,
) -> None:
    """중간 조회 실패는 staging을 폐기하고 final CSV와 checkpoint를 유지한다."""
    temp_dir = tmp_path / "temp"
    processed: set[str] = set()
    documents = [
        _valid_record(1, employee_name="이민서"),
        _valid_record(2, employee_name="박지수"),
        _valid_record(3, employee_name="최서윤"),
    ]
    created: list[FakeIncrementalPipeline] = []

    def baseline_factory(
        settings: AtlasSettings,
        *,
        processed_ids_path: Path,
    ) -> FakeIncrementalPipeline:
        del settings
        pipeline = FakeIncrementalPipeline(
            documents[:1],
            processed,
            processed_ids_path=processed_ids_path,
        )
        created.append(pipeline)
        return pipeline

    run_atlas_cleaning(
        _settings(),
        batch_size=1,
        temp_dir=temp_dir,
        validation_now=VALIDATION_NOW,
        pipeline_factory=baseline_factory,
    )
    accept_before = (temp_dir / "accept.csv").read_bytes()
    reject_before = (temp_dir / "reject.csv").read_bytes()
    normalization_paths = _normalization_paths(temp_dir)
    normalization_before = _file_bytes(normalization_paths)

    def failing_factory(
        settings: AtlasSettings,
        *,
        processed_ids_path: Path,
    ) -> FakeIncrementalPipeline:
        del settings
        pipeline = FakeIncrementalPipeline(
            documents,
            processed,
            processed_ids_path=processed_ids_path,
            fail_after_batches=1,
        )
        created.append(pipeline)
        return pipeline

    with pytest.raises(RuntimeError, match="simulated Bronze batch failure"):
        run_atlas_cleaning(
            _settings(),
            batch_size=1,
            temp_dir=temp_dir,
            validation_now=VALIDATION_NOW,
            pipeline_factory=failing_factory,
        )

    assert (temp_dir / "accept.csv").read_bytes() == accept_before
    assert (temp_dir / "reject.csv").read_bytes() == reject_before
    assert _file_bytes(normalization_paths) == normalization_before
    assert processed == {"source-1"}
    assert created[-1].mark_calls == []
    assert created[-1].closed is True
    assert not any(path.name.startswith(".silver-flat-") for path in temp_dir.iterdir())


def test_second_csv_publish_failure_restores_previous_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """reject 교체 실패 시 먼저 교체된 accept까지 이전 pair로 복원한다."""
    temp_dir = tmp_path / "temp"
    write_clean_flat_data(
        ((_valid_record(1),),),
        temp_dir=temp_dir,
        validation_now=VALIDATION_NOW,
    )
    accept_before = (temp_dir / "accept.csv").read_bytes()
    reject_before = (temp_dir / "reject.csv").read_bytes()
    original_replace = csv_output_module.os.replace

    def fail_reject_replace(source: object, destination: object) -> None:
        destination_path = Path(destination)  # type: ignore[arg-type]
        if destination_path == temp_dir / "reject.csv":
            raise OSError("simulated reject publication failure")
        original_replace(source, destination)

    monkeypatch.setattr(csv_output_module.os, "replace", fail_reject_replace)

    with pytest.raises(OSError, match="simulated reject publication failure"):
        write_clean_flat_data(
            ((_valid_record(2, employee_name="박지수"),),),
            temp_dir=temp_dir,
            validation_now=VALIDATION_NOW,
        )

    assert (temp_dir / "accept.csv").read_bytes() == accept_before
    assert (temp_dir / "reject.csv").read_bytes() == reject_before
    assert not any(path.name.startswith(".silver-flat-") for path in temp_dir.iterdir())


def test_checkpoint_failure_after_publication_replays_without_csv_duplicates(
    tmp_path: Path,
) -> None:
    """CSV 성공 뒤 checkpoint 실패 시 재조회 source는 재기록 없이 확정된다."""
    temp_dir = tmp_path / "temp"
    processed: set[str] = set()
    accepted_document = _valid_record(1)
    rejected_document = _valid_record(2)
    rejected_document["payload"]["area_no"] = "AREA-12345"
    documents = [accepted_document, rejected_document]
    attempts = iter((True, False))
    created: list[FakeIncrementalPipeline] = []

    def factory(
        settings: AtlasSettings,
        *,
        processed_ids_path: Path,
    ) -> FakeIncrementalPipeline:
        del settings
        pipeline = FakeIncrementalPipeline(
            documents,
            processed,
            processed_ids_path=processed_ids_path,
            fail_mark=next(attempts),
        )
        created.append(pipeline)
        return pipeline

    with pytest.raises(RuntimeError, match="simulated checkpoint failure"):
        run_atlas_cleaning(
            _settings(),
            temp_dir=temp_dir,
            validation_now=VALIDATION_NOW,
            pipeline_factory=factory,
        )
    accept_after_publication = (temp_dir / "accept.csv").read_bytes()
    reject_after_publication = (temp_dir / "reject.csv").read_bytes()
    normalization_paths = _normalization_paths(temp_dir)
    normalization_after_publication = _file_bytes(normalization_paths)
    assert processed == set()
    assert len(_rows(temp_dir / "accept.csv")) == 1
    assert len(_rows(temp_dir / "reject.csv")) == 1

    retry = run_atlas_cleaning(
        _settings(),
        temp_dir=temp_dir,
        validation_now=VALIDATION_NOW,
        pipeline_factory=factory,
    )

    assert retry.input_count == 2
    assert retry.accepted_count == 0
    assert retry.rejected_count == 0
    assert retry.replayed_count == 2
    assert processed == {"source-1", "source-2"}
    assert (temp_dir / "accept.csv").read_bytes() == accept_after_publication
    assert (temp_dir / "reject.csv").read_bytes() == reject_after_publication
    assert _file_bytes(normalization_paths) == normalization_after_publication
    assert [row["source_id"] for row in _rows(temp_dir / "accept.csv")] == ["source-1"]
    assert [row["source_id"] for row in _rows(temp_dir / "reject.csv")] == ["source-2"]
    assert all(pipeline.closed for pipeline in created)


def test_normalization_publication_failure_replays_flat_before_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """정규화 실패는 Flat만 남기고 재시도 성공 뒤 checkpoint를 확정한다."""
    temp_dir = tmp_path / "temp"
    processed: set[str] = set()
    documents = [_valid_record(1)]
    created: list[FakeIncrementalPipeline] = []

    def factory(
        settings: AtlasSettings,
        *,
        processed_ids_path: Path,
    ) -> FakeIncrementalPipeline:
        del settings
        pipeline = FakeIncrementalPipeline(
            documents,
            processed,
            processed_ids_path=processed_ids_path,
        )
        created.append(pipeline)
        return pipeline

    actual_materializer = flat_pipeline_module.materialize_normalized_outputs

    def fail_normalization_publication(temp_dir: Path) -> object:
        del temp_dir
        raise OSError("simulated normalization publication failure")

    monkeypatch.setattr(
        flat_pipeline_module,
        "materialize_normalized_outputs",
        fail_normalization_publication,
    )

    with pytest.raises(OSError, match="simulated normalization publication failure"):
        run_atlas_cleaning(
            _settings(),
            temp_dir=temp_dir,
            validation_now=VALIDATION_NOW,
            pipeline_factory=factory,
        )

    accept_after_failure = (temp_dir / "accept.csv").read_bytes()
    reject_after_failure = (temp_dir / "reject.csv").read_bytes()
    assert [row["source_id"] for row in _rows(temp_dir / "accept.csv")] == ["source-1"]
    assert _rows(temp_dir / "reject.csv") == []
    assert processed == set()
    assert created[0].mark_calls == []
    assert created[0].processed_ids_path.exists() is False
    assert created[0].closed is True

    monkeypatch.setattr(
        flat_pipeline_module,
        "materialize_normalized_outputs",
        actual_materializer,
    )
    retry = run_atlas_cleaning(
        _settings(),
        temp_dir=temp_dir,
        validation_now=VALIDATION_NOW,
        pipeline_factory=factory,
    )

    assert retry.input_count == 1
    assert retry.accepted_count == 0
    assert retry.rejected_count == 0
    assert retry.replayed_count == 1
    assert (temp_dir / "accept.csv").read_bytes() == accept_after_failure
    assert (temp_dir / "reject.csv").read_bytes() == reject_after_failure
    assert all(path.is_file() for path in _normalization_paths(temp_dir))
    assert processed == {"source-1"}
    assert created[1].mark_calls == [("source-1",)]
    assert created[1].closed is True


def test_checkpoint_without_output_pair_fails_before_pipeline_creation(
    tmp_path: Path,
) -> None:
    """processed 상태만 남은 경우 빈 CSV를 만들거나 Atlas를 읽지 않는다."""
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    checkpoint_path = temp_dir / "processed_ids.json"
    checkpoint_path.write_text('["source-1"]\n', encoding="utf-8")
    factory_called = False

    def factory(
        settings: AtlasSettings,
        *,
        processed_ids_path: Path,
    ) -> FakeIncrementalPipeline:
        del settings, processed_ids_path
        nonlocal factory_called
        factory_called = True
        raise AssertionError("pipeline must not be created")

    with pytest.raises(RuntimeError, match="checkpoint는 있지만"):
        run_atlas_cleaning(
            _settings(),
            temp_dir=temp_dir,
            validation_now=VALIDATION_NOW,
            pipeline_factory=factory,
        )

    assert factory_called is False
    assert not (temp_dir / "accept.csv").exists()
    assert not (temp_dir / "reject.csv").exists()


def test_rerun_adds_only_new_sources_and_empty_run_is_idempotent(
    tmp_path: Path,
) -> None:
    """정상 checkpoint 재실행은 신규 source만 추가하고 빈 실행은 bytes를 유지한다."""
    temp_dir = tmp_path / "temp"
    processed: set[str] = set()
    documents = [_valid_record(1, employee_name="이민서")]
    created: list[FakeIncrementalPipeline] = []

    def factory(
        settings: AtlasSettings,
        *,
        processed_ids_path: Path,
    ) -> FakeIncrementalPipeline:
        del settings
        pipeline = FakeIncrementalPipeline(
            documents,
            processed,
            processed_ids_path=processed_ids_path,
        )
        created.append(pipeline)
        return pipeline

    first = run_atlas_cleaning(
        _settings(),
        temp_dir=temp_dir,
        validation_now=VALIDATION_NOW,
        pipeline_factory=factory,
    )
    documents.append(_valid_record(2, employee_name="박지수"))
    second = run_atlas_cleaning(
        _settings(),
        temp_dir=temp_dir,
        validation_now=VALIDATION_NOW,
        pipeline_factory=factory,
    )
    accept_after_second = (temp_dir / "accept.csv").read_bytes()
    reject_after_second = (temp_dir / "reject.csv").read_bytes()
    third = run_atlas_cleaning(
        _settings(),
        temp_dir=temp_dir,
        validation_now=VALIDATION_NOW,
        pipeline_factory=factory,
    )

    assert first.accepted_count == 1
    assert second.input_count == 1
    assert second.accepted_count == 1
    assert third.input_count == 0
    assert third.source_ids == ()
    assert created[-1].mark_calls == []
    assert processed == {"source-1", "source-2"}
    assert [row["source_id"] for row in _rows(temp_dir / "accept.csv")] == [
        "source-1",
        "source-2",
    ]
    assert (temp_dir / "accept.csv").read_bytes() == accept_after_second
    assert (temp_dir / "reject.csv").read_bytes() == reject_after_second


def test_real_bronze_and_silver_default_factory_integrate_idempotently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실제 Bronze API와 Silver 조립이 publication 후 checkpoint를 공유한다."""
    source_ids = [ObjectId(f"{number:024x}") for number in range(1, 4)]
    documents = [
        _valid_record(1, employee_name="이민서"),
        _valid_record(2, employee_name="박지수"),
        _valid_record(3, employee_name="최서윤"),
    ]
    for document, source_id in zip(documents, source_ids, strict=True):
        document["_id"] = source_id
    collection = IntegratedAtlasCollection(tuple(reversed(documents)))
    clients: list[IntegratedAtlasClient] = []

    def client_factory(uri: str) -> IntegratedAtlasClient:
        assert uri == "mongodb://atlas.invalid"
        client = IntegratedAtlasClient(collection)
        clients.append(client)
        return client

    monkeypatch.setattr(atlas_pipeline_module, "_new_mongo_client", client_factory)
    temp_dir = tmp_path / "temp"

    first = run_atlas_cleaning(
        _settings(),
        batch_size=2,
        temp_dir=temp_dir,
        validation_now=VALIDATION_NOW,
    )
    accept_after_first = (temp_dir / "accept.csv").read_bytes()
    reject_after_first = (temp_dir / "reject.csv").read_bytes()
    first_page_calls = [call for call in collection.calls if call["projection"] is None]
    second = run_atlas_cleaning(
        _settings(),
        batch_size=2,
        temp_dir=temp_dir,
        validation_now=VALIDATION_NOW,
    )

    assert first.accepted_count == 3
    assert first.source_ids == tuple(str(source_id) for source_id in source_ids)
    assert [call["limit"] for call in first_page_calls] == [2, 2]
    assert second.input_count == 0
    assert second.source_ids == ()
    assert json_util.loads(
        (temp_dir / "processed_ids.json").read_text(encoding="utf-8")
    ) == [str(source_id) for source_id in source_ids]
    assert (temp_dir / "accept.csv").read_bytes() == accept_after_first
    assert (temp_dir / "reject.csv").read_bytes() == reject_after_first
    assert all(client.closed for client in clients)


def test_empty_initial_source_creates_header_only_outputs(tmp_path: Path) -> None:
    """초기 미처리 source가 없어도 재실행 가능한 두 header를 만든다."""
    temp_dir = tmp_path / "temp"
    processed: set[str] = set()
    created: list[FakeIncrementalPipeline] = []

    def factory(
        settings: AtlasSettings,
        *,
        processed_ids_path: Path,
    ) -> FakeIncrementalPipeline:
        del settings
        pipeline = FakeIncrementalPipeline(
            [],
            processed,
            processed_ids_path=processed_ids_path,
        )
        created.append(pipeline)
        return pipeline

    summary = run_atlas_cleaning(
        _settings(),
        temp_dir=temp_dir,
        validation_now=VALIDATION_NOW,
        pipeline_factory=factory,
    )

    assert summary.input_count == 0
    assert summary.accepted_count == 0
    assert summary.rejected_count == 0
    assert summary.replayed_count == 0
    assert _rows(summary.accept_path) == []
    assert _rows(summary.reject_path) == []
    assert created[0].mark_calls == []
    assert created[0].closed is True


def test_old_csv_schema_is_loaded_and_migrated_on_next_publication(
    tmp_path: Path,
) -> None:
    """source_id/reject_stage가 없던 CSV도 값 기준을 보존해 새 header로 이관한다."""
    temp_dir = tmp_path / "temp"
    invalid = _valid_record(2)
    invalid["payload"]["area_no"] = "AREA-12345"
    write_clean_flat_data(
        ((_valid_record(1), invalid),),
        temp_dir=temp_dir,
        validation_now=VALIDATION_NOW,
    )
    current_accept = _rows(temp_dir / "accept.csv")
    current_reject = _rows(temp_dir / "reject.csv")
    output_fields = SilverRules.load_default().output_fields
    old_accept_fields = ("record_id", *output_fields)
    old_reject_fields = ("record_id", "reason_codes", "reason_details", "raw_json")
    _write_rows(
        temp_dir / "accept.csv",
        old_accept_fields,
        [{field: current_accept[0][field] for field in old_accept_fields}],
    )
    _write_rows(
        temp_dir / "reject.csv",
        old_reject_fields,
        [{field: current_reject[0][field] for field in old_reject_fields}],
    )

    summary = write_clean_flat_data(
        (
            (
                _valid_record(3),
                _valid_record(4, employee_name="박지수"),
            ),
        ),
        temp_dir=temp_dir,
        validation_now=VALIDATION_NOW,
    )

    accepted = _rows(temp_dir / "accept.csv")
    rejected = _rows(temp_dir / "reject.csv")
    assert summary.accepted_count == 1
    assert summary.duplicate_rejected_count == 1
    assert [row["source_id"] for row in accepted] == ["", "source-4"]
    assert [row["source_id"] for row in rejected] == ["", "source-3"]
    assert [row["reject_stage"] for row in rejected] == [
        "FIRST_STAGE",
        "SECOND_STAGE",
    ]


def test_incomplete_existing_csv_pair_fails_closed(tmp_path: Path) -> None:
    """accept 또는 reject 하나만 남은 상태는 빈 반대편 파일로 덮지 않는다."""
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    accept_path = temp_dir / "accept.csv"
    accept_path.write_text("record_id\n1\n", encoding="utf-8")
    accept_before = accept_path.read_bytes()

    with pytest.raises(ValueError, match="pair 중 한 파일만"):
        write_clean_flat_data((), temp_dir=temp_dir)

    assert accept_path.read_bytes() == accept_before
    assert not (temp_dir / "reject.csv").exists()


def test_duplicate_existing_source_id_fails_closed(tmp_path: Path) -> None:
    """기존 accept와 reject가 같은 source를 주장하면 재개 전에 중단한다."""
    temp_dir = tmp_path / "temp"
    invalid = _valid_record(2)
    invalid["payload"]["area_no"] = "AREA-12345"
    write_clean_flat_data(
        ((_valid_record(1), invalid),),
        temp_dir=temp_dir,
        validation_now=VALIDATION_NOW,
    )
    accept_before = (temp_dir / "accept.csv").read_bytes()
    reject_rows = _rows(temp_dir / "reject.csv")
    reject_rows[0]["source_id"] = "source-1"
    _write_rows(
        temp_dir / "reject.csv",
        tuple(reject_rows[0]),
        reject_rows,
    )
    reject_before = (temp_dir / "reject.csv").read_bytes()

    with pytest.raises(ValueError, match="source_id가 중복"):
        write_clean_flat_data((), temp_dir=temp_dir)

    assert (temp_dir / "accept.csv").read_bytes() == accept_before
    assert (temp_dir / "reject.csv").read_bytes() == reject_before
