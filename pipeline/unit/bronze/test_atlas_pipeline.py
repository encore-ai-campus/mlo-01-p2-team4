"""AtlasIncrementalPipeline의 fake-only 처리 확인 단위 테스트."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from bson import ObjectId
from pymongo import ASCENDING

import src.bronze.atlas_pipeline as atlas_pipeline_module
from src.bronze.atlas_download import AtlasSettings
from src.bronze.atlas_pipeline import (
    AtlasIncrementalBatch,
    AtlasIncrementalPipeline,
)


class FakeCollection:
    """빈 query와 ``_id`` 서버 정렬만 구현하는 Mongo collection fake."""

    def __init__(self, documents: list[dict[str, Any]]) -> None:
        """원본 객체를 그대로 보관해 mutation 여부를 검증할 수 있게 한다."""
        self.documents = documents
        self.calls: list[dict[str, object]] = []

    def find(
        self,
        query: Mapping[str, object],
        projection: Mapping[str, int] | None = None,
        *,
        sort: list[tuple[str, int]],
        limit: int = 0,
    ) -> Iterator[dict[str, Any]]:
        """PyMongo의 keyset query, projection, 정렬, limit을 흉내 낸다."""
        self.calls.append(
            {
                "query": dict(query),
                "projection": None if projection is None else dict(projection),
                "sort": list(sort),
                "limit": limit,
            }
        )
        field_name, direction = sort[0]

        def sort_key(document: Mapping[str, Any]) -> tuple[str, str]:
            """혼합 타입도 fake 내부에서는 비교 가능하도록 BSON 유사 순서를 만든다."""
            if field_name not in document:
                return ("", "")
            value = document[field_name]
            return (type(value).__name__, str(value))

        documents = sorted(
            self.documents,
            key=sort_key,
            reverse=direction < 0,
        )
        if query:
            predicate = query.get(field_name)
            if isinstance(predicate, Mapping) and "$gt" in predicate:
                lower_bound = predicate["$gt"]
                documents = [
                    document
                    for document in documents
                    if document.get(field_name) > lower_bound
                ]
        if projection is not None:
            documents = [
                {"_id": document["_id"]} if "_id" in document else {}
                for document in documents
            ]
        if limit:
            documents = documents[:limit]
        return iter(documents)


class FakeClient:
    """database/collection indexing과 close를 기록하는 Mongo client fake."""

    def __init__(self, collection: FakeCollection) -> None:
        """모든 collection 접근이 같은 fake를 반환하도록 초기화한다."""
        self.collection = collection
        self.closed = False

    def __getitem__(self, name: str) -> Any:
        """database 단계에는 mapping을, collection 단계에는 fake를 반환한다."""
        if name == "test_database":
            return {"test_records": self.collection}
        return self.collection

    def close(self) -> None:
        """실제 network 없이 close 호출 여부만 기록한다."""
        self.closed = True


def _settings() -> AtlasSettings:
    """실제 Atlas에 접속하지 않는 검증된 설정을 만든다."""
    return AtlasSettings(
        uri="mongodb://atlas.invalid",
        database="test_database",
        collection="test_records",
    )


def _object_id(number: int) -> ObjectId:
    """숫자 순서와 BSON ``_id`` 순서가 같은 결정적 ObjectId를 만든다."""
    return ObjectId(f"{number:024x}")


def _documents(*numbers: int) -> list[dict[str, Any]]:
    """호출 순서를 보존한 fake Atlas 원본 문서를 만든다."""
    return [
        {
            "_id": _object_id(number),
            "payload": {"value": f"raw-{number}"},
        }
        for number in numbers
    ]


def _install_fake_client(
    monkeypatch: pytest.MonkeyPatch,
    documents: list[dict[str, Any]],
) -> tuple[FakeCollection, list[FakeClient], list[str]]:
    """기존 lazy client factory를 매 호출 새 fake client로 교체한다."""
    collection = FakeCollection(documents)
    clients: list[FakeClient] = []
    factory_calls: list[str] = []

    def fake_client_factory(uri: str) -> FakeClient:
        """연결 문자열과 생성된 fake client를 기록한다."""
        factory_calls.append(uri)
        client = FakeClient(collection)
        clients.append(client)
        return client

    monkeypatch.setattr(atlas_pipeline_module, "_new_mongo_client", fake_client_factory)
    return collection, clients, factory_calls


def _batch_ids(batches: list[AtlasIncrementalBatch]) -> list[list[ObjectId]]:
    """batch별 ``_id`` 목록만 추출해 경계와 순서를 비교한다."""
    return [[record["_id"] for record in batch.records] for batch in batches]


def test_iter_batches_yields_oldest_ids_in_exact_boundaries_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """입력 순서와 무관하게 ``_id`` 오름차순 2/2/1 batch를 반환한다."""
    documents = _documents(5, 2, 4, 1, 3)
    original_documents = copy.deepcopy(documents)
    collection, clients, factory_calls = _install_fake_client(monkeypatch, documents)
    pipeline = AtlasIncrementalPipeline(_settings(), tmp_path / "processed.json")

    batches = list(pipeline.iter_batches(limit=2))

    assert _batch_ids(batches) == [
        [_object_id(1), _object_id(2)],
        [_object_id(3), _object_id(4)],
        [_object_id(5)],
    ]
    assert documents == original_documents
    assert collection.calls == [
        {
            "query": {},
            "projection": {"_id": 1},
            "sort": [("_id", ASCENDING)],
            "limit": 0,
        },
        {
            "query": {},
            "projection": None,
            "sort": [("_id", ASCENDING)],
            "limit": 2,
        },
        {
            "query": {"_id": {"$gt": _object_id(2)}},
            "projection": None,
            "sort": [("_id", ASCENDING)],
            "limit": 2,
        },
        {
            "query": {"_id": {"$gt": _object_id(4)}},
            "projection": None,
            "sort": [("_id", ASCENDING)],
            "limit": 2,
        },
    ]
    assert factory_calls == ["mongodb://atlas.invalid"]
    source_by_id = {document["_id"]: document for document in documents}
    for batch in batches:
        for record in batch.records:
            assert record is source_by_id[record["_id"]]
            assert type(record["_id"]) is ObjectId

    pipeline.close()
    assert clients[0].closed is True


def test_iter_batches_uses_server_pages_for_2505_documents(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """2,505건을 Atlas server limit 1,000의 1,000/1,000/505로 읽는다."""
    documents = _documents(*range(2505, 0, -1))
    collection, _, _ = _install_fake_client(monkeypatch, documents)
    pipeline = AtlasIncrementalPipeline(_settings(), tmp_path / "processed.json")

    batches = list(pipeline.iter_batches(limit=1000))

    assert [len(batch.records) for batch in batches] == [1000, 1000, 505]
    assert [record["_id"] for batch in batches for record in batch.records] == [
        _object_id(number) for number in range(1, 2506)
    ]
    page_calls = [call for call in collection.calls if call["projection"] is None]
    assert [call["limit"] for call in page_calls] == [1000, 1000, 1000]
    assert [call["query"] for call in page_calls] == [
        {},
        {"_id": {"$gt": _object_id(1000)}},
        {"_id": {"$gt": _object_id(2000)}},
    ]


def test_iter_batches_skips_ids_already_present_in_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """중간 처리 ID를 건너뛴 뒤 후속 원본을 읽어 마지막 전 batch를 채운다."""
    documents = _documents(5, 4, 3, 2, 1)
    _install_fake_client(monkeypatch, documents)
    state_path = tmp_path / "processed.json"
    state_path.write_text(
        json.dumps(sorted([str(_object_id(2)), str(_object_id(4))]), indent=2) + "\n",
        encoding="utf-8",
    )
    pipeline = AtlasIncrementalPipeline(_settings(), state_path)

    batches = list(pipeline.iter_batches(limit=2))

    assert _batch_ids(batches) == [
        [_object_id(1), _object_id(3)],
        [_object_id(5)],
    ]
    assert [len(batch.records) for batch in batches] == [2, 1]


def test_state_does_not_advance_until_mark_and_restart_then_skips(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """읽기만 반복하면 같은 첫 batch이고 성공 표시 뒤 재시작에서만 건너뛴다."""
    documents = _documents(4, 3, 2, 1)
    _install_fake_client(monkeypatch, documents)
    state_path = tmp_path / "processed.json"

    first_run = AtlasIncrementalPipeline(_settings(), state_path)
    first_batch = list(first_run.iter_batches(limit=2))[0]
    assert state_path.exists() is False

    retry_before_mark = AtlasIncrementalPipeline(_settings(), state_path)
    retry_batch = list(retry_before_mark.iter_batches(limit=2))[0]
    assert _batch_ids([retry_batch]) == [[_object_id(1), _object_id(2)]]

    first_run.mark_processed(record["_id"] for record in first_batch.records)
    restarted = AtlasIncrementalPipeline(_settings(), state_path)
    remaining_batches = list(restarted.iter_batches(limit=2))

    assert _batch_ids(remaining_batches) == [[_object_id(3), _object_id(4)]]
    assert json.loads(state_path.read_text(encoding="utf-8")) == [
        str(_object_id(1)),
        str(_object_id(2)),
    ]


def test_duplicate_mark_processed_is_idempotent_and_deterministic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """중복·역순 ID를 한 번씩만 사전순 저장하고 재호출 시 bytes를 유지한다."""
    _install_fake_client(monkeypatch, _documents())
    state_path = tmp_path / "nested" / "processed.json"
    pipeline = AtlasIncrementalPipeline(_settings(), state_path)

    pipeline.mark_processed([_object_id(3), _object_id(1), _object_id(3)])
    first_content = state_path.read_bytes()
    pipeline.mark_processed([_object_id(1), _object_id(3), _object_id(1)])

    assert state_path.read_bytes() == first_content
    assert json.loads(first_content) == [str(_object_id(1)), str(_object_id(3))]
    assert list(state_path.parent.glob(".processed.json.*.tmp")) == []


@pytest.mark.parametrize(
    "corrupt_content",
    [
        "{not-json",
        "{}",
        '["b", "a"]',
        '["a", "a"]',
        "[1]",
    ],
)
def test_corrupt_state_fails_closed_before_client_creation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    corrupt_content: str,
) -> None:
    """파싱·타입·중복·정렬 계약 위반 상태는 Atlas 연결 전에 중단한다."""
    _, _, factory_calls = _install_fake_client(monkeypatch, _documents(1))
    state_path = tmp_path / "processed.json"
    state_path.write_text(corrupt_content, encoding="utf-8")

    with pytest.raises(RuntimeError, match="processed IDs 상태 파일"):
        AtlasIncrementalPipeline(_settings(), state_path)

    assert factory_calls == []


@pytest.mark.parametrize("invalid_limit", [0, -1, True, 1.5])
def test_iter_batches_rejects_non_positive_integer_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    invalid_limit: object,
) -> None:
    """bool을 포함해 양의 정수가 아닌 limit은 Atlas 조회 전에 거부한다."""
    _, _, factory_calls = _install_fake_client(monkeypatch, _documents(1))
    pipeline = AtlasIncrementalPipeline(_settings(), tmp_path / "processed.json")

    with pytest.raises(ValueError, match="limit은 1 이상의 정수"):
        list(pipeline.iter_batches(limit=invalid_limit))  # type: ignore[arg-type]

    assert factory_calls == []


def test_empty_source_yields_no_batches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """빈 Atlas source는 빈 batch를 만들지 않고 정상 종료한다."""
    collection, _, _ = _install_fake_client(monkeypatch, _documents())
    pipeline = AtlasIncrementalPipeline(_settings(), tmp_path / "processed.json")

    assert list(pipeline.iter_batches(limit=3)) == []
    assert collection.calls == [
        {
            "query": {},
            "projection": {"_id": 1},
            "sort": [("_id", ASCENDING)],
            "limit": 0,
        },
        {
            "query": {},
            "projection": None,
            "sort": [("_id", ASCENDING)],
            "limit": 3,
        },
    ]


def test_missing_id_fails_closed_before_any_batch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """단 하나의 ``_id`` 누락도 전체 사전 검증에서 batch 반환 전에 중단한다."""
    documents = _documents(1, 2)
    documents.append({"payload": {"value": "missing-id"}})
    collection, _, _ = _install_fake_client(monkeypatch, documents)
    pipeline = AtlasIncrementalPipeline(_settings(), tmp_path / "processed.json")

    with pytest.raises(RuntimeError, match="_id가 없습니다"):
        list(pipeline.iter_batches(limit=1))

    assert len(collection.calls) == 1
    assert collection.calls[0]["projection"] == {"_id": 1}


def test_mixed_id_types_fail_closed_before_any_batch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """ObjectId와 문자열 ``_id`` 혼합은 전체 사전 검증에서 중단한다."""
    documents = _documents(1, 2)
    documents.append({"_id": "legacy-id", "payload": {"value": "mixed"}})
    collection, _, _ = _install_fake_client(monkeypatch, documents)
    pipeline = AtlasIncrementalPipeline(_settings(), tmp_path / "processed.json")

    with pytest.raises(RuntimeError, match="_id 타입이 섞여"):
        list(pipeline.iter_batches(limit=1))

    assert len(collection.calls) == 1
    assert collection.calls[0]["projection"] == {"_id": 1}
