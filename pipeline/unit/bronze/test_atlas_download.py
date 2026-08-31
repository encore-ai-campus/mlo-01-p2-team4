"""Atlas Bronze reader와 append-only raw store의 fake-only 단위 테스트."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest
from bson import ObjectId, json_util

import src.bronze.atlas_download as atlas_download_module
import src.bronze.raw_store as raw_store_module
from src.bronze.atlas_download import (
    DEFAULT_BATCH_LIMIT,
    AtlasCursor,
    AtlasSettings,
    AtlasSourceReader,
    AtlasSourceShapeError,
    parse_args,
)
from src.bronze.raw_store import RawBatchStore, load_finalized_resume_cursor


class FakeCollection:
    """서버 정렬·projection·limit·$gt를 기록하는 Mongo Collection fake다."""

    def __init__(self, documents: list[dict[str, Any]]) -> None:
        """원본 입력 순서를 보존한 fake documents를 만든다.

        Args:
            documents: fake Atlas가 반환할 원본 문서 목록.
        """
        self.documents = documents
        self.calls: list[dict[str, object]] = []
        self.before_batch_query: Callable[[FakeCollection], None] | None = None

    def find(
        self,
        query: Mapping[str, object],
        projection: Mapping[str, int] | None = None,
        *,
        sort: list[tuple[str, int]] | None = None,
        limit: int = 0,
    ) -> Any:
        """PyMongo find와 같은 최소 계약으로 가짜 서버 결과를 반환한다.

        Args:
            query: 빈 query 또는 양의 정수 $gt predicate.
            projection: preflight에서 요청한 안정 키 projection.
            sort: 서버 정렬 선언.
            limit: 서버가 적용할 최대 문서 수.

        Returns:
            독립된 fake 문서 iterator.
        """
        self.calls.append(
            {
                "query": dict(query),
                "projection": None if projection is None else dict(projection),
                "sort": sort,
                "limit": limit,
            }
        )
        if projection is None and self.before_batch_query is not None:
            action = self.before_batch_query
            self.before_batch_query = None
            action(self)
        documents = copy.deepcopy(self.documents)
        if query:
            field_name, predicate = next(iter(query.items()))
            documents = self._apply_gt(documents, field_name, predicate)
        if sort:
            field_name, direction = sort[0]
            documents = self._server_sort(documents, field_name, direction)
        if projection is not None:
            documents = self._apply_projection(documents, projection)
        if limit:
            documents = documents[:limit]
        return iter(documents)

    def _apply_gt(
        self,
        documents: list[dict[str, Any]],
        field_name: str,
        predicate: object,
    ) -> list[dict[str, Any]]:
        """양의 정수 안정 키의 $gt predicate를 적용한다.

        Args:
            documents: 정렬 전 fake documents.
            field_name: 안정 키 필드.
            predicate: MongoDB predicate mapping.

        Returns:
            cursor보다 큰 안정 키를 가진 documents.
        """
        if not isinstance(predicate, Mapping):
            return documents
        value = predicate.get("$gt")
        selected: list[dict[str, Any]] = []
        for document in documents:
            key_value = document.get(field_name)
            if type(key_value) is int and type(value) is int and key_value > value:
                selected.append(document)
        return selected

    def _server_sort(
        self,
        documents: list[dict[str, Any]],
        field_name: str,
        direction: int,
    ) -> list[dict[str, Any]]:
        """MongoDB 서버 정렬을 흉내 내고 혼합 타입도 결정적으로 정렬한다.

        Args:
            documents: 정렬할 fake documents.
            field_name: 안정 키 필드.
            direction: 1 또는 -1 정렬 방향.

        Returns:
            정렬된 fake documents.
        """

        def sort_key(document: dict[str, Any]) -> tuple[int, object]:
            """안전하지 않은 타입도 테스트용으로 비교 가능한 키로 바꾼다."""
            value = document.get(field_name)
            if type(value) is int:
                return (0, value)
            if value is None:
                return (1, "")
            return (2, f"{type(value).__name__}:{value}")

        return sorted(documents, key=sort_key, reverse=direction < 0)

    def _apply_projection(
        self,
        documents: list[dict[str, Any]],
        projection: Mapping[str, int],
    ) -> list[dict[str, Any]]:
        """포함 projection으로 원본이 아닌 preflight 문서를 만든다.

        Args:
            documents: fake Atlas documents.
            projection: MongoDB 포함/제외 projection.

        Returns:
            projection이 적용된 새 documents.
        """
        included_fields: list[str] = []
        for name, enabled in projection.items():
            if name != "_id" and enabled == 1:
                included_fields.append(name)
        projected_documents: list[dict[str, Any]] = []
        for document in documents:
            projected: dict[str, Any] = {}
            for name in included_fields:
                if name in document:
                    projected[name] = document[name]
            projected_documents.append(projected)
        return projected_documents


class FakeClient:
    """lazy client factory가 network 없이 열었는지 검증할 최소 fake다."""

    def __init__(self, collection: FakeCollection) -> None:
        """database/collection index 체인을 위한 fake를 초기화한다.

        Args:
            collection: 반환할 fake collection.
        """
        self.collection = collection
        self.closed = False

    def __getitem__(self, name: str) -> Any:
        """database와 collection 이름 접근을 모두 허용한다.

        Args:
            name: database 또는 collection 이름.

        Returns:
            다음 index 단계 또는 fake collection.
        """
        if name == "test_database":
            return {"test_records": self.collection}
        return self.collection

    def close(self) -> None:
        """reader가 만든 fake client가 닫혔음을 표시한다."""
        self.closed = True


def _settings() -> AtlasSettings:
    """실제 Atlas에 연결되지 않는 테스트용 설정을 만든다.

    Returns:
        안정 키가 record_id인 AtlasSettings.
    """
    return AtlasSettings(
        uri="mongodb://atlas.invalid",
        database="test_database",
        collection="test_records",
    )


def _documents(count: int) -> list[dict[str, Any]]:
    """원래 입력은 일부러 역순인 안전한 fake Atlas documents를 만든다.

    Args:
        count: 생성할 원본 문서 수.

    Returns:
        `_id`와 추가 원본 필드를 가진 documents.
    """
    documents: list[dict[str, Any]] = []
    for record_id in range(count, 0, -1):
        documents.append(
            {
                "_id": ObjectId(),
                "record_id": record_id,
                "unmodified_value": f"value-{record_id}",
            }
        )
    return documents


def _metadata() -> dict[str, str]:
    """raw manifest에 넣을 비밀 없는 컬렉션 metadata를 만든다.

    Returns:
        RawBatchStore 계약의 metadata.
    """
    return {
        "database": "test_database",
        "collection": "test_records",
        "stable_key": "record_id",
    }


def test_reader_is_lazy_and_public_configuration_is_immutable() -> None:
    """reader 생성은 client factory를 부르지 않고 frozen settings를 유지한다."""
    collection = FakeCollection(_documents(2))
    client = FakeClient(collection)
    factory_calls: list[str] = []

    def client_factory(uri: str) -> FakeClient:
        """호출 시점만 기록하는 fake client factory다."""
        factory_calls.append(uri)
        return client

    settings = _settings()
    reader = AtlasSourceReader(settings, client_factory=client_factory)

    assert factory_calls == []
    with pytest.raises(FrozenInstanceError):
        settings.database = "changed"  # type: ignore[misc]

    report = reader.probe_shape()

    assert report.document_count == 2
    assert factory_calls == ["mongodb://atlas.invalid"]
    reader.close()
    assert client.closed is True


def test_record_id_is_the_only_i1_stable_key_contract() -> None:
    """승인되지 않은 alternate stable key는 settings와 cursor에서 모두 거부한다."""
    with pytest.raises(ValueError):
        AtlasSettings(
            uri="mongodb://atlas.invalid",
            database="test_database",
            collection="test_records",
            stable_key="alternate_id",
        )
    with pytest.raises(ValueError):
        AtlasCursor("alternate_id", 1, "a" * 64)


def test_cli_accepts_only_resume_manifest_and_rejects_direct_cursor_inputs() -> None:
    """cross-process restart CLI는 finalized manifest만 받고 직접 cursor 입력을 거부한다."""
    base_arguments = [
        "--dataset-id",
        "hr",
        "--snapshot-id",
        "snapshot-1",
        "--run-id",
        "run-1",
        "--batch-sequence",
        "1",
    ]

    with pytest.raises(SystemExit):
        parse_args([*base_arguments, "--cursor", "2"])
    with pytest.raises(SystemExit):
        parse_args([*base_arguments, "--cursor-fingerprint", "a" * 64])

    parsed = parse_args([*base_arguments, "--resume-manifest", "prior/1.json"])

    assert parsed.resume_manifest == Path("prior/1.json")


def test_default_and_boundary_limits_preserve_original_id_and_stable_order() -> None:
    """기본 1000 상한과 limit=1 경계가 서버 정렬 원본을 보존한다."""
    documents = _documents(DEFAULT_BATCH_LIMIT + 1)
    collection = FakeCollection(documents)
    reader = AtlasSourceReader(_settings(), collection=collection)

    default_batch = reader.read_batch()
    one_row_batch = reader.read_batch(limit=1)

    assert len(default_batch.records) == DEFAULT_BATCH_LIMIT
    assert default_batch.has_more is True
    assert [row["record_id"] for row in default_batch.records[:3]] == [1, 2, 3]
    assert default_batch.records[0]["_id"] == documents[-1]["_id"]
    assert default_batch.records[0]["unmodified_value"] == "value-1"
    assert len(one_row_batch.records) == 1
    assert one_row_batch.next_cursor is not None
    assert one_row_batch.next_cursor.stable_key == "record_id"
    assert one_row_batch.next_cursor.last_value == 1
    assert one_row_batch.next_cursor.stable_key_fingerprint is not None
    assert one_row_batch.next_cursor.stable_key_prefix_fingerprint is not None
    assert one_row_batch.records[0]["_id"] == documents[-1]["_id"]

    with pytest.raises(ValueError):
        reader.read_batch(limit=0)
    with pytest.raises(ValueError):
        reader.read_batch(limit=True)


def test_cursor_replay_restarts_without_skip_or_duplicate_records() -> None:
    """다음 cursor는 원본 cursor를 바꾸지 않고 $gt keyset 재시작을 보장한다."""
    collection = FakeCollection(_documents(5))
    reader = AtlasSourceReader(_settings(), collection=collection)

    first = reader.read_batch(limit=2)
    original_cursor = first.next_cursor
    assert original_cursor is not None
    second = reader.read_batch(limit=2, cursor=original_cursor)
    assert second.next_cursor is not None
    third = reader.read_batch(limit=2, cursor=second.next_cursor)

    assert [row["record_id"] for row in first.records] == [1, 2]
    assert [row["record_id"] for row in second.records] == [3, 4]
    assert [row["record_id"] for row in third.records] == [5]
    assert first.next_cursor is not None
    assert first.next_cursor.stable_key == "record_id"
    assert first.next_cursor.last_value == 2
    assert (
        first.next_cursor.stable_key_fingerprint
        == original_cursor.stable_key_fingerprint
    )
    assert (
        first.next_cursor.stable_key_prefix_fingerprint
        == original_cursor.stable_key_prefix_fingerprint
    )
    assert collection.calls[4]["query"] == {"record_id": {"$gt": 2}}
    assert collection.calls[7]["query"] == {"record_id": {"$gt": 4}}
    for call in collection.calls:
        assert "skip" not in call
        assert call["sort"] == [("record_id", 1)]


def test_iter_batches_reads_from_start_to_end_in_requested_sizes() -> None:
    """전체 iterator는 cursor 입력 없이 시작해 n건 단위로 terminal까지 읽는다."""
    collection = FakeCollection(_documents(2505))
    reader = AtlasSourceReader(_settings(), collection=collection)

    batches = tuple(reader.iter_batches(limit=1000))

    assert [len(batch.records) for batch in batches] == [1000, 1000, 505]
    assert [record["record_id"] for batch in batches for record in batch.records] == (
        list(range(1, 2506))
    )
    assert [batch.has_more for batch in batches] == [True, True, False]
    batch_queries = [
        call["query"] for call in collection.calls if call["projection"] is None
    ]
    assert batch_queries == [
        {},
        {"record_id": {"$gt": 1000}},
        {"record_id": {"$gt": 2000}},
    ]


def test_empty_preflight_fingerprint_is_deterministic_and_value_free() -> None:
    """빈 source도 동일한 전체 stable-key-set fingerprint를 만든다."""
    first_report = AtlasSourceReader(
        _settings(), collection=FakeCollection([])
    ).preflight()
    second_report = AtlasSourceReader(
        _settings(), collection=FakeCollection([])
    ).preflight()

    expected_fingerprint = hashlib.sha256(b"atlas-stable-key-set-v1\x00").hexdigest()

    assert first_report.stable_key_fingerprint == expected_fingerprint
    assert second_report.stable_key_fingerprint == expected_fingerprint
    assert first_report.document_count == 0


def test_lower_id_backfill_invalidates_resume_fingerprint_before_batch_query() -> None:
    """첫 batch 뒤 lower-ID backfill은 $gt query 전에 fail-closed로 막는다."""
    collection = FakeCollection(
        [
            {"_id": ObjectId(), "record_id": 1},
            {"_id": ObjectId(), "record_id": 3},
            {"_id": ObjectId(), "record_id": 4},
            {"_id": ObjectId(), "record_id": 5},
        ]
    )
    reader = AtlasSourceReader(_settings(), collection=collection)
    first_batch = reader.read_batch(limit=2)
    assert first_batch.next_cursor is not None
    collection.documents.append({"_id": ObjectId(), "record_id": 2})
    call_count_before_restart = len(collection.calls)

    with pytest.raises(AtlasSourceShapeError):
        reader.read_batch(limit=2, cursor=first_batch.next_cursor)

    assert len(collection.calls) == call_count_before_restart + 1
    assert collection.calls[-1]["projection"] == {"record_id": 1, "_id": 0}
    assert collection.calls[-1]["query"] == {}


def test_forged_last_value_with_genuine_full_fingerprint_fails_before_batch_query() -> (
    None
):
    """전체 hash가 진짜여도 다른 last_value의 prefix proof가 아니면 재시작을 막는다."""
    collection = FakeCollection(_documents(5))
    reader = AtlasSourceReader(_settings(), collection=collection)
    first_batch = reader.read_batch(limit=2)
    assert first_batch.next_cursor is not None
    forged_cursor = AtlasCursor(
        "record_id",
        4,
        first_batch.next_cursor.stable_key_fingerprint,
        first_batch.next_cursor.stable_key_prefix_fingerprint,
    )
    call_count_before_restart = len(collection.calls)

    with pytest.raises(AtlasSourceShapeError):
        reader.read_batch(limit=2, cursor=forged_cursor)

    assert len(collection.calls) == call_count_before_restart + 1
    assert collection.calls[-1]["projection"] == {"record_id": 1, "_id": 0}
    assert collection.calls[-1]["query"] == {}


def test_postflight_fence_rejects_lower_id_backfill_during_batch_query() -> None:
    """$gt query 직전의 lower-ID backfill은 postflight 전까지 batch를 반환하지 않는다."""
    collection = FakeCollection(
        [
            {"_id": ObjectId(), "record_id": 1},
            {"_id": ObjectId(), "record_id": 3},
            {"_id": ObjectId(), "record_id": 4},
            {"_id": ObjectId(), "record_id": 5},
        ]
    )
    reader = AtlasSourceReader(_settings(), collection=collection)
    first_batch = reader.read_batch(limit=2)
    assert first_batch.next_cursor is not None

    def inject_lower_backfill(target: FakeCollection) -> None:
        """batch query 직전에 기존 cursor보다 작은 record_id를 추가한다."""
        target.documents.append({"_id": ObjectId(), "record_id": 2})

    collection.before_batch_query = inject_lower_backfill
    call_count_before_restart = len(collection.calls)

    with pytest.raises(AtlasSourceShapeError):
        reader.read_batch(limit=2, cursor=first_batch.next_cursor)

    assert len(collection.calls) == call_count_before_restart + 3
    assert collection.calls[-2]["query"] == {"record_id": {"$gt": 3}}
    assert collection.calls[-1]["projection"] == {"record_id": 1, "_id": 0}


def test_provenance_less_cursor_is_rejected_before_batch_query() -> None:
    """legacy last-record-ID만 가진 cursor는 preflight 뒤 batch query 없이 거부한다."""
    collection = FakeCollection(_documents(4))
    reader = AtlasSourceReader(_settings(), collection=collection)
    legacy_cursor = AtlasCursor("record_id", 2)

    with pytest.raises(AtlasSourceShapeError):
        reader.read_batch(limit=2, cursor=legacy_cursor)

    assert len(collection.calls) == 1
    assert collection.calls[0]["projection"] == {"record_id": 1, "_id": 0}
    assert collection.calls[0]["query"] == {}


@pytest.mark.parametrize(
    ("documents", "expected_attribute"),
    [
        ([{"_id": ObjectId()}], "missing_key_count"),
        ([{"_id": ObjectId(), "record_id": None}], "null_or_empty_key_count"),
        (
            [
                {"_id": ObjectId(), "record_id": 1},
                {"_id": ObjectId(), "record_id": 1},
            ],
            "duplicate_key_count",
        ),
        (
            [
                {"_id": ObjectId(), "record_id": 1},
                {"_id": ObjectId(), "record_id": "2"},
            ],
            "invalid_key_type_count",
        ),
        ([{"_id": ObjectId(), "record_id": []}], "invalid_key_type_count"),
    ],
)
def test_invalid_or_unverified_shape_fails_closed_without_raw_values(
    documents: list[dict[str, Any]],
    expected_attribute: str,
) -> None:
    """누락·중복·null·혼합·정렬 불가 안정 키는 원본 없이 실패한다."""
    documents[0]["secret_value"] = "do-not-leak"
    collection = FakeCollection(documents)
    reader = AtlasSourceReader(_settings(), collection=collection)

    with pytest.raises(AtlasSourceShapeError) as error:
        reader.read_batch(limit=2)

    assert error.value.report is not None
    assert getattr(error.value.report, expected_attribute) > 0
    assert error.value.report.is_resumable is False
    assert "do-not-leak" not in str(error.value)
    assert len(collection.calls) == 1
    assert collection.calls[0]["projection"] == {"record_id": 1, "_id": 0}


def test_raw_store_preserves_bson_id_checksum_and_row_locators(tmp_path: Path) -> None:
    """raw JSONL은 `_id` 포함 원본을 보존하고 manifest checksum/locator를 남긴다."""
    documents = [
        {
            "_id": ObjectId(),
            "record_id": 1,
            "nested": {"source_field": "unchanged"},
        },
        {
            "_id": ObjectId(),
            "record_id": 2,
            "nested": {"source_field": "also-unchanged"},
        },
    ]
    store = RawBatchStore(tmp_path / "data" / "runs")

    artifact = store.persist_batch(
        dataset_id="hr",
        snapshot_id="snapshot-1",
        run_id="run-1",
        batch_sequence=1,
        records=documents,
        collection_metadata=_metadata(),
        resume_cursor={
            "stable_key": "record_id",
            "last_value": 2,
            "stable_key_fingerprint": "a" * 64,
            "stable_key_prefix_fingerprint": "b" * 64,
        },
    )

    expected_path = (
        tmp_path
        / "data"
        / "runs"
        / "hr"
        / "snapshot-1"
        / "run-1"
        / "bronze"
        / "batches"
        / "1.jsonl"
    )
    raw_bytes = artifact.batch_path.read_bytes()
    raw_documents = []
    for raw_line in raw_bytes.splitlines():
        raw_documents.append(json_util.loads(raw_line.decode("utf-8")))
    manifest = json.loads(artifact.manifest_path.read_text(encoding="utf-8"))

    assert artifact.batch_path == expected_path
    assert raw_documents == documents
    assert documents[0]["nested"] == {"source_field": "unchanged"}
    assert artifact.row_count == 2
    assert artifact.sha256 == hashlib.sha256(raw_bytes).hexdigest()
    assert manifest["sha256"] == artifact.sha256
    assert manifest["row_count"] == 2
    assert manifest["collection_metadata"] == _metadata()
    assert manifest["resume_cursor"] == {
        "stable_key": "record_id",
        "last_value": 2,
        "stable_key_fingerprint": "a" * 64,
        "stable_key_prefix_fingerprint": "b" * 64,
    }
    assert manifest["batch_path"] == "batches/1.jsonl"
    assert manifest["row_locators"] == [
        {
            "line_number": 1,
            "byte_offset": 0,
            "locator": "batches/1.jsonl#L1",
        },
        {
            "line_number": 2,
            "byte_offset": len(raw_bytes.splitlines(keepends=True)[0]),
            "locator": "batches/1.jsonl#L2",
        },
    ]
    assert "payload" not in manifest
    assert "source_record_sha256" not in manifest


def test_finalized_manifest_restores_actual_next_cursor_for_resume(
    tmp_path: Path,
) -> None:
    """정상 raw/manifest pair는 실제 다음 cursor를 복원해 reader 재시작에 사용한다."""
    collection = FakeCollection(_documents(4))
    reader = AtlasSourceReader(_settings(), collection=collection)
    first_batch = reader.read_batch(limit=2)
    assert first_batch.next_cursor is not None
    store = RawBatchStore(tmp_path / "data" / "runs")
    artifact = store.persist_batch(
        dataset_id="hr",
        snapshot_id="snapshot-1",
        run_id="run-1",
        batch_sequence=1,
        records=first_batch.records,
        collection_metadata=_metadata(),
        resume_cursor={
            "stable_key": first_batch.next_cursor.stable_key,
            "last_value": first_batch.next_cursor.last_value,
            "stable_key_fingerprint": first_batch.next_cursor.stable_key_fingerprint,
            "stable_key_prefix_fingerprint": first_batch.next_cursor.stable_key_prefix_fingerprint,
        },
    )

    restored = load_finalized_resume_cursor(
        artifact.manifest_path,
        dataset_id="hr",
        snapshot_id="snapshot-1",
        run_id="run-1",
        collection_metadata=_metadata(),
    )
    resumed_batch = reader.read_batch(
        limit=2,
        cursor=AtlasCursor(
            stable_key=restored.stable_key,
            last_value=restored.last_value,
            stable_key_fingerprint=restored.stable_key_fingerprint,
            stable_key_prefix_fingerprint=restored.stable_key_prefix_fingerprint,
        ),
    )

    assert restored.last_value == 2
    assert (
        restored.stable_key_prefix_fingerprint
        == first_batch.next_cursor.stable_key_prefix_fingerprint
    )
    assert [record["record_id"] for record in resumed_batch.records] == [3, 4]


def test_checksum_mismatched_resume_manifest_is_rejected_before_reader_query(
    tmp_path: Path,
) -> None:
    """raw bytes가 manifest checksum과 다르면 cursor 복원 전에 fail-closed로 중단한다."""
    store = RawBatchStore(tmp_path / "data" / "runs")
    artifact = store.persist_batch(
        dataset_id="hr",
        snapshot_id="snapshot-1",
        run_id="run-1",
        batch_sequence=1,
        records=[{"_id": ObjectId(), "record_id": 1}],
        collection_metadata=_metadata(),
        resume_cursor={
            "stable_key": "record_id",
            "last_value": 1,
            "stable_key_fingerprint": "a" * 64,
            "stable_key_prefix_fingerprint": "b" * 64,
        },
    )
    artifact.batch_path.write_bytes(b"forged-raw-bytes\n")
    collection = FakeCollection(_documents(4))

    with pytest.raises(ValueError, match="checksum"):
        load_finalized_resume_cursor(
            artifact.manifest_path,
            dataset_id="hr",
            snapshot_id="snapshot-1",
            run_id="run-1",
            collection_metadata=_metadata(),
        )

    assert collection.calls == []


def test_cli_rejects_checksum_mismatched_manifest_before_reader_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI는 forged resume manifest를 Atlas reader 생성·batch query 전에 거부한다."""
    store = RawBatchStore(tmp_path / "data" / "runs")
    artifact = store.persist_batch(
        dataset_id="hr",
        snapshot_id="snapshot-1",
        run_id="run-1",
        batch_sequence=1,
        records=[{"_id": ObjectId(), "record_id": 1}],
        collection_metadata=_metadata(),
        resume_cursor={
            "stable_key": "record_id",
            "last_value": 1,
            "stable_key_fingerprint": "a" * 64,
            "stable_key_prefix_fingerprint": "b" * 64,
        },
    )
    artifact.batch_path.write_bytes(b"forged-raw-bytes\n")
    monkeypatch.setenv("MONGODB_URI", "mongodb://atlas.invalid")
    monkeypatch.setenv("MONGODB_DATABASE", "test_database")
    monkeypatch.setenv("MONGODB_COLLECTION", "test_records")

    class ReaderMustNotStart:
        """invalid manifest가 reader 생성 전 거부되는지 확인하는 fake다."""

        def __init__(self, *args: object, **kwargs: object) -> None:
            """이 생성자가 호출되면 CLI 검증 순서가 잘못된 것이다."""
            raise AssertionError(
                "Atlas reader must not start for invalid resume manifest"
            )

    monkeypatch.setattr(atlas_download_module, "AtlasSourceReader", ReaderMustNotStart)

    with pytest.raises(ValueError, match="checksum"):
        atlas_download_module.main(
            [
                "--dataset-id",
                "hr",
                "--snapshot-id",
                "snapshot-1",
                "--run-id",
                "run-1",
                "--batch-sequence",
                "2",
                "--resume-manifest",
                str(artifact.manifest_path),
                "--output-root",
                str(tmp_path / "output"),
            ]
        )


def test_raw_store_refuses_overwrite_of_finalized_artifact(tmp_path: Path) -> None:
    """동일 batch sequence는 raw 또는 manifest가 있으면 overwrite 없이 실패한다."""
    store = RawBatchStore(tmp_path / "data" / "runs")
    records = [{"_id": ObjectId(), "record_id": 1}]
    first = store.write_batch(
        dataset_id="hr",
        snapshot_id="snapshot-1",
        run_id="run-1",
        batch_sequence=1,
        records=records,
        collection_metadata=_metadata(),
    )
    original_bytes = first.batch_path.read_bytes()

    with pytest.raises(FileExistsError):
        store.persist_batch(
            dataset_id="hr",
            snapshot_id="snapshot-1",
            run_id="run-1",
            batch_sequence=1,
            records=records,
            collection_metadata=_metadata(),
        )

    assert first.batch_path.read_bytes() == original_bytes


def test_raw_store_rolls_back_raw_final_when_manifest_publication_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """manifest final publish 실패는 raw-only final을 남기지 않고 재시도를 허용한다."""
    store = RawBatchStore(tmp_path / "data" / "runs")
    records = [{"_id": ObjectId(), "record_id": 1}]
    original_publish = raw_store_module._publish_exclusive

    def fail_manifest_publish(staged_path: Path, final_path: Path) -> None:
        """manifest의 exclusive final publish만 실패시키는 fake hook이다."""
        if final_path.suffix == ".json":
            raise OSError("simulated manifest publication failure")
        original_publish(staged_path, final_path)

    monkeypatch.setattr(raw_store_module, "_publish_exclusive", fail_manifest_publish)

    with pytest.raises(OSError, match="simulated manifest publication failure"):
        store.persist_batch(
            dataset_id="hr",
            snapshot_id="snapshot-1",
            run_id="run-1",
            batch_sequence=1,
            records=records,
            collection_metadata=_metadata(),
        )

    bronze_directory = (
        tmp_path / "data" / "runs" / "hr" / "snapshot-1" / "run-1" / "bronze"
    )
    assert not (bronze_directory / "batches" / "1.jsonl").exists()
    assert not (bronze_directory / "manifests" / "1.json").exists()
    assert not (bronze_directory / ".staging").exists()

    monkeypatch.setattr(raw_store_module, "_publish_exclusive", original_publish)
    artifact = store.persist_batch(
        dataset_id="hr",
        snapshot_id="snapshot-1",
        run_id="run-1",
        batch_sequence=1,
        records=records,
        collection_metadata=_metadata(),
    )

    assert artifact.batch_path.exists()
    assert artifact.manifest_path.exists()


def test_raw_store_blocks_manifest_only_preexisting_artifact(tmp_path: Path) -> None:
    """manifest만 남은 불완전 artifact는 overwrite 없이 recovery blocker가 된다."""
    store = RawBatchStore(tmp_path / "data" / "runs")
    bronze_directory = (
        tmp_path / "data" / "runs" / "hr" / "snapshot-1" / "run-1" / "bronze"
    )
    manifest_path = bronze_directory / "manifests" / "1.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("existing-manifest", encoding="utf-8")

    with pytest.raises(FileExistsError, match="불완전한 Bronze artifact"):
        store.persist_batch(
            dataset_id="hr",
            snapshot_id="snapshot-1",
            run_id="run-1",
            batch_sequence=1,
            records=[{"_id": ObjectId(), "record_id": 1}],
            collection_metadata=_metadata(),
        )

    assert manifest_path.read_text(encoding="utf-8") == "existing-manifest"
    assert not (bronze_directory / "batches" / "1.jsonl").exists()


def test_raw_store_blocks_raw_only_preexisting_artifact(tmp_path: Path) -> None:
    """raw만 남은 불완전 artifact도 overwrite 없이 recovery blocker가 된다."""
    store = RawBatchStore(tmp_path / "data" / "runs")
    bronze_directory = (
        tmp_path / "data" / "runs" / "hr" / "snapshot-1" / "run-1" / "bronze"
    )
    batch_path = bronze_directory / "batches" / "1.jsonl"
    batch_path.parent.mkdir(parents=True)
    batch_path.write_bytes(b'{"record_id":1}\n')

    with pytest.raises(FileExistsError, match="불완전한 Bronze artifact"):
        store.persist_batch(
            dataset_id="hr",
            snapshot_id="snapshot-1",
            run_id="run-1",
            batch_sequence=1,
            records=[{"_id": ObjectId(), "record_id": 1}],
            collection_metadata=_metadata(),
        )

    assert batch_path.read_bytes() == b'{"record_id":1}\n'
    assert not (bronze_directory / "manifests" / "1.json").exists()
