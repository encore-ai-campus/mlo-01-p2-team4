"""Atlas 원본을 keyset 방식으로 읽어 Bronze 저장소에 전달하는 모듈.

이 모듈은 Atlas에 접속하기 전에는 네트워크 작업을 하지 않는다. 호출 시에는
안정 키의 전체 구조를 먼저 검증하고, 검증된 키를 기준으로만 다음 batch를 읽는다.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path
from typing import Any

from pymongo import ASCENDING, MongoClient

from .environment import load_dotenv
from .raw_store import RawBatchStore, load_finalized_resume_cursor

DEFAULT_BATCH_LIMIT = 1000
DEFAULT_STABLE_KEY = "record_id"


def _is_sha256(value: object) -> bool:
    """값이 64자리 소문자 SHA-256 hex인지 판별한다.

    Args:
        value: 검증할 임의 값.

    Returns:
        SHA-256 hex 형식이면 True.
    """
    allowed_characters = "0123456789abcdef"
    return (
        type(value) is str
        and len(value) == 64
        and all(character in allowed_characters for character in value)
    )


@dataclass(frozen=True, slots=True)
class AtlasSettings:
    """Atlas 읽기 대상과 안정 키를 비밀값 출력 없이 보관한다."""

    uri: str = field(repr=False)
    database: str
    collection: str
    stable_key: str = DEFAULT_STABLE_KEY

    def __post_init__(self) -> None:
        """설정의 필수 문자열과 Mongo field 경계를 검증한다.

        Raises:
            ValueError: 연결 대상이나 안정 키가 비어 있거나 안전하지 않을 때.
        """
        for name, value in (
            ("uri", self.uri),
            ("database", self.database),
            ("collection", self.collection),
            ("stable_key", self.stable_key),
        ):
            if type(value) is not str or not value.strip():
                raise ValueError(f"{name}은 비어 있지 않은 문자열이어야 합니다.")
        if "." in self.stable_key or self.stable_key.startswith("$"):
            raise ValueError("stable_key는 MongoDB 단일 필드 이름이어야 합니다.")
        if self.stable_key != DEFAULT_STABLE_KEY:
            raise ValueError("I1 Atlas Bronze 안정 키는 record_id로 고정됩니다.")

    @classmethod
    def from_environment(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> AtlasSettings:
        """실행 시점 환경변수에서 Atlas 읽기 설정을 만든다.

        Args:
            environ: 테스트에서 주입할 환경변수 mapping. 없으면 현재 환경을 사용한다.

        Returns:
            검증된 AtlasSettings 객체.

        Raises:
            ValueError: 필수 환경변수가 없거나 설정 검증에 실패할 때.
        """
        if environ is None:
            load_dotenv()
            values = os.environ
        else:
            values = environ
        required_names = (
            "MONGODB_URI",
            "MONGODB_DATABASE",
            "MONGODB_COLLECTION",
        )
        missing_names: list[str] = []
        for name in required_names:
            value = values.get(name)
            if type(value) is not str or not value.strip():
                missing_names.append(name)
        if missing_names:
            joined_names = ", ".join(missing_names)
            raise ValueError(f"필수 Atlas 환경변수가 없습니다: {joined_names}")
        return cls(
            uri=values["MONGODB_URI"],
            database=values["MONGODB_DATABASE"],
            collection=values["MONGODB_COLLECTION"],
            stable_key=DEFAULT_STABLE_KEY,
        )

    def collection_metadata(self) -> dict[str, str]:
        """Raw manifest에 기록할 비밀 없는 컬렉션 식별 정보를 만든다.

        Returns:
            database, collection, stable_key만 포함한 metadata.
        """
        return {
            "database": self.database,
            "collection": self.collection,
            "stable_key": self.stable_key,
        }


@dataclass(frozen=True, slots=True)
class AtlasCursor:
    """검증된 안정 키와 source fingerprint에서 재시작할 위치를 표현한다."""

    stable_key: str
    last_value: int
    stable_key_fingerprint: str | None = None
    stable_key_prefix_fingerprint: str | None = None

    def __post_init__(self) -> None:
        """cursor가 default Bronze keyset과 provenance 형식을 지키는지 확인한다.

        Raises:
            ValueError: 키 이름이나 마지막 안정 키 값이 유효하지 않을 때.
        """
        if type(self.stable_key) is not str or not self.stable_key.strip():
            raise ValueError("cursor stable_key는 비어 있지 않은 문자열이어야 합니다.")
        if self.stable_key != DEFAULT_STABLE_KEY:
            raise ValueError("I1 Atlas Bronze cursor 안정 키는 record_id로 고정됩니다.")
        if type(self.last_value) is not int or self.last_value < 1:
            raise ValueError("cursor last_value는 1 이상의 정수여야 합니다.")
        fingerprint = self.stable_key_fingerprint
        if fingerprint is not None and not _is_sha256(fingerprint):
            raise ValueError(
                "cursor stable_key_fingerprint는 64자리 소문자 SHA-256이어야 합니다."
            )
        prefix_fingerprint = self.stable_key_prefix_fingerprint
        if prefix_fingerprint is not None and not _is_sha256(prefix_fingerprint):
            raise ValueError(
                "cursor stable_key_prefix_fingerprint는 64자리 소문자 SHA-256이어야 합니다."
            )

    @property
    def value(self) -> int:
        """호환성 있는 읽기 전용 마지막 안정 키 값을 반환한다.

        Returns:
            마지막으로 안전하게 확인한 양의 정수 안정 키.
        """
        return self.last_value


@dataclass(frozen=True, slots=True)
class AtlasPreflight:
    """원본 값을 노출하지 않는 Atlas 안정 키 구조 검증 결과다."""

    stable_key: str
    document_count: int
    missing_key_count: int
    null_or_empty_key_count: int
    duplicate_key_count: int
    invalid_key_type_count: int
    unordered_key_count: int
    observed_key_type_names: tuple[str, ...]
    is_resumable: bool
    stable_key_fingerprint: str | None = None
    cursor_last_value_present: bool | None = None
    cursor_prefix_fingerprint: str | None = None


class AtlasSourceShapeError(RuntimeError):
    """Atlas 원본 구조가 keyset 재시작 계약을 충족하지 않을 때 발생한다."""

    def __init__(self, message: str, report: AtlasPreflight | None = None) -> None:
        """원본 값을 포함하지 않는 실패 메시지와 구조 report를 보관한다.

        Args:
            message: 비밀·원본 값이 없는 실패 요약.
            report: 집계/구조 정보만 포함한 preflight 결과.
        """
        self.report = report
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class AtlasBatch:
    """검증된 Atlas 원본 문서와 다음 keyset cursor를 묶는다."""

    records: tuple[Mapping[str, Any], ...]
    next_cursor: AtlasCursor | None
    has_more: bool
    preflight: AtlasPreflight

    def __post_init__(self) -> None:
        """batch가 검증된 preflight와 불변 tuple 경계를 갖는지 확인한다.

        Raises:
            TypeError: records나 has_more 타입이 계약과 다를 때.
            ValueError: 검증되지 않은 preflight 결과를 batch로 만들 때.
        """
        object.__setattr__(self, "records", tuple(self.records))
        if type(self.has_more) is not bool:
            raise TypeError("has_more는 bool이어야 합니다.")
        if not isinstance(self.preflight, AtlasPreflight):
            raise TypeError("preflight는 AtlasPreflight여야 합니다.")
        if not self.preflight.is_resumable:
            raise ValueError("검증되지 않은 Atlas 원본으로 batch를 만들 수 없습니다.")


def _new_mongo_client(uri: str) -> Any:
    """실제 읽기 호출 직전에만 lazy MongoClient를 생성한다.

    Args:
        uri: Atlas 연결 문자열.

    Returns:
        아직 서버 선택을 시도하지 않은 MongoClient.
    """
    return MongoClient(uri, connect=False, serverSelectionTimeoutMS=30000)


@dataclass(frozen=True, slots=True)
class AtlasSourceReader:
    """사전 검증 뒤 keyset pagination으로 원본 문서를 읽는 reader다."""

    settings: AtlasSettings
    collection: Any | None = field(default=None, repr=False, compare=False)
    client_factory: Callable[[str], Any] = field(
        default=_new_mongo_client,
        repr=False,
        compare=False,
    )
    _client: Any | None = field(default=None, init=False, repr=False, compare=False)
    _opened_collection: Any | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        """reader 의존성을 확인하되 Atlas 접속은 시작하지 않는다.

        Raises:
            TypeError: settings 또는 client_factory가 reader 계약과 다를 때.
        """
        if not isinstance(self.settings, AtlasSettings):
            raise TypeError("settings는 AtlasSettings여야 합니다.")
        if not callable(self.client_factory):
            raise TypeError("client_factory는 호출 가능해야 합니다.")

    def _get_collection(self) -> Any:
        """주입된 fake 또는 lazy client에서 컬렉션을 한 번만 연다.

        Returns:
            MongoDB Collection 호환 객체.

        Raises:
            AtlasSourceShapeError: 컬렉션 객체를 안전하게 만들 수 없을 때.
        """
        if self.collection is not None:
            return self.collection
        if self._opened_collection is not None:
            return self._opened_collection
        try:
            client = self.client_factory(self.settings.uri)
            opened_collection = client[self.settings.database][self.settings.collection]
        except Exception as error:
            raise AtlasSourceShapeError(
                "Atlas 컬렉션 객체를 만들 수 없습니다."
            ) from error
        object.__setattr__(self, "_client", client)
        object.__setattr__(self, "_opened_collection", opened_collection)
        return opened_collection

    def _find_for_preflight(self, collection: Any) -> Any:
        """안정 키만 projection한 서버 정렬 preflight cursor를 요청한다.

        Args:
            collection: MongoDB Collection 호환 객체.

        Returns:
            안정 키 오름차순 cursor.

        Raises:
            AtlasSourceShapeError: 정렬된 preflight cursor를 요청할 수 없을 때.
        """
        projection = {self.settings.stable_key: 1, "_id": 0}
        try:
            return collection.find(
                {},
                projection,
                sort=[(self.settings.stable_key, ASCENDING)],
            )
        except Exception as error:
            raise AtlasSourceShapeError(
                "Atlas 안정 키 preflight 질의를 실행할 수 없습니다."
            ) from error

    def preflight(self, cursor: AtlasCursor | None = None) -> AtlasPreflight:
        """전체 안정 키 구조와 선택 cursor 위치의 provenance를 검증한다.

        Args:
            cursor: prefix provenance를 계산할 선택적 trusted cursor.

        Returns:
            원본 값 없이 count/type/order만 담은 검증 report.

        Raises:
            AtlasSourceShapeError: 안정 키 누락·중복·null·타입·정렬 문제가 있을 때.
        """
        if cursor is not None and not isinstance(cursor, AtlasCursor):
            raise ValueError("cursor는 AtlasCursor 또는 None이어야 합니다.")
        if cursor is not None and cursor.stable_key != self.settings.stable_key:
            raise ValueError("cursor stable_key가 reader stable_key와 다릅니다.")
        collection = self._get_collection()
        source_cursor = self._find_for_preflight(collection)
        document_count = 0
        missing_key_count = 0
        null_or_empty_key_count = 0
        duplicate_key_count = 0
        invalid_key_type_count = 0
        unordered_key_count = 0
        observed_key_type_names: set[str] = set()
        seen_values: set[int] = set()
        previous_value: int | None = None
        fingerprint = hashlib.sha256()
        fingerprint.update(b"atlas-stable-key-set-v1\x00")
        prefix_fingerprint = hashlib.sha256()
        prefix_fingerprint.update(b"atlas-stable-key-prefix-v1\x00")
        cursor_last_value_present = False

        try:
            for document in source_cursor:
                document_count += 1
                if not isinstance(document, Mapping):
                    missing_key_count += 1
                    continue
                if self.settings.stable_key not in document:
                    missing_key_count += 1
                    continue
                value = document[self.settings.stable_key]
                observed_key_type_names.add(type(value).__name__)
                if value is None or value == "":
                    null_or_empty_key_count += 1
                if type(value) is not int or value < 1:
                    invalid_key_type_count += 1
                    continue
                encoded_value = str(value).encode("ascii")
                fingerprint.update(len(encoded_value).to_bytes(8, "big"))
                fingerprint.update(encoded_value)
                if cursor is not None and value <= cursor.last_value:
                    prefix_fingerprint.update(len(encoded_value).to_bytes(8, "big"))
                    prefix_fingerprint.update(encoded_value)
                if cursor is not None and value == cursor.last_value:
                    cursor_last_value_present = True
                if value in seen_values:
                    duplicate_key_count += 1
                seen_values.add(value)
                if previous_value is not None and value <= previous_value:
                    unordered_key_count += 1
                previous_value = value
        except AtlasSourceShapeError:
            raise
        except Exception as error:
            raise AtlasSourceShapeError(
                "Atlas 안정 키 preflight 결과를 읽을 수 없습니다."
            ) from error

        is_resumable = (
            missing_key_count == 0
            and null_or_empty_key_count == 0
            and duplicate_key_count == 0
            and invalid_key_type_count == 0
            and unordered_key_count == 0
        )
        report = AtlasPreflight(
            stable_key=self.settings.stable_key,
            document_count=document_count,
            missing_key_count=missing_key_count,
            null_or_empty_key_count=null_or_empty_key_count,
            duplicate_key_count=duplicate_key_count,
            invalid_key_type_count=invalid_key_type_count,
            unordered_key_count=unordered_key_count,
            observed_key_type_names=tuple(sorted(observed_key_type_names)),
            is_resumable=is_resumable,
            stable_key_fingerprint=fingerprint.hexdigest() if is_resumable else None,
            cursor_last_value_present=(
                cursor_last_value_present if cursor is not None else None
            ),
            cursor_prefix_fingerprint=(
                prefix_fingerprint.hexdigest()
                if is_resumable and cursor is not None and cursor_last_value_present
                else None
            ),
        )
        if not report.is_resumable:
            raise AtlasSourceShapeError(
                "Atlas 안정 키 구조가 안전한 재시작을 보장하지 못합니다.",
                report,
            )
        return report

    def probe_shape(self) -> AtlasPreflight:
        """명시적인 shape probe 이름으로 preflight를 호출한다.

        Returns:
            원본 값을 포함하지 않는 안전한 구조 검증 report.

        Raises:
            AtlasSourceShapeError: 원본 구조가 keyset 재시작에 안전하지 않을 때.
        """
        return self.preflight()

    @staticmethod
    def _source_fence_signature(report: AtlasPreflight) -> tuple[object, ...]:
        """pre/post 사이에 source 구조나 전체 stable keyset이 바뀌었는지 비교한다.

        Args:
            report: cursor-specific 값 없이 비교할 preflight 결과.

        Returns:
            구조와 full stable-key fingerprint만 담은 비교용 tuple.
        """
        return (
            report.stable_key,
            report.document_count,
            report.missing_key_count,
            report.null_or_empty_key_count,
            report.duplicate_key_count,
            report.invalid_key_type_count,
            report.unordered_key_count,
            report.observed_key_type_names,
            report.is_resumable,
            report.stable_key_fingerprint,
        )

    def _validate_cursor_provenance(
        self,
        cursor: AtlasCursor,
        report: AtlasPreflight,
    ) -> None:
        """current source preflight가 trusted cursor의 전체·prefix provenance와 맞는지 확인한다.

        Args:
            cursor: 이전 finalized batch에서 복원하거나 메모리에서 신뢰한 cursor.
            report: 해당 cursor 위치까지 prefix를 계산한 현재 preflight 결과.

        Raises:
            AtlasSourceShapeError: full keyset, cursor 위치, prefix provenance가 다를 때.
        """
        if cursor.stable_key_fingerprint is None:
            raise AtlasSourceShapeError(
                "Atlas 재시작 cursor에 source fingerprint가 없습니다."
            )
        if cursor.stable_key_prefix_fingerprint is None:
            raise AtlasSourceShapeError(
                "Atlas 재시작 cursor에 prefix fingerprint가 없습니다."
            )
        if report.stable_key_fingerprint is None:
            raise AtlasSourceShapeError(
                "Atlas preflight fingerprint를 만들 수 없습니다."
            )
        if cursor.stable_key_fingerprint != report.stable_key_fingerprint:
            raise AtlasSourceShapeError(
                "Atlas source fingerprint가 재시작 cursor와 일치하지 않습니다."
            )
        if not report.cursor_last_value_present:
            raise AtlasSourceShapeError(
                "Atlas 재시작 cursor 위치가 현재 source에 없습니다."
            )
        if report.cursor_prefix_fingerprint is None:
            raise AtlasSourceShapeError(
                "Atlas cursor prefix fingerprint를 만들 수 없습니다."
            )
        if cursor.stable_key_prefix_fingerprint != report.cursor_prefix_fingerprint:
            raise AtlasSourceShapeError(
                "Atlas cursor prefix fingerprint가 현재 source와 일치하지 않습니다."
            )

    def _find_batch(
        self, collection: Any, query: Mapping[str, object], limit: int
    ) -> Any:
        """skip 없이 안정 키 오름차순 batch cursor를 요청한다.

        Args:
            collection: MongoDB Collection 호환 객체.
            query: 빈 query 또는 verified key의 $gt cursor predicate.
            limit: has_more 판별을 포함한 서버 조회 상한.

        Returns:
            서버 정렬과 limit가 적용된 cursor.

        Raises:
            AtlasSourceShapeError: keyset batch 질의를 요청할 수 없을 때.
        """
        try:
            return collection.find(
                dict(query),
                sort=[(self.settings.stable_key, ASCENDING)],
                limit=limit,
            )
        except Exception as error:
            raise AtlasSourceShapeError(
                "Atlas keyset batch 질의를 실행할 수 없습니다."
            ) from error

    def _validate_read_documents(
        self,
        documents: Sequence[object],
        cursor: AtlasCursor | None,
    ) -> None:
        """preflight 이후 변경된 원본이 batch에 섞이지 않도록 다시 확인한다.

        Args:
            documents: limit+1 이하로 읽은 원본 문서 후보.
            cursor: 호출자가 전달한 이전 재시작 위치.

        Raises:
            AtlasSourceShapeError: 문서 구조·순서·cursor predicate가 변했을 때.
        """
        previous_value = cursor.last_value if cursor is not None else None
        for document in documents:
            if not isinstance(document, Mapping):
                raise AtlasSourceShapeError(
                    "preflight 이후 Atlas 문서 구조가 변경되었습니다."
                )
            if self.settings.stable_key not in document:
                raise AtlasSourceShapeError(
                    "preflight 이후 Atlas 안정 키가 누락되었습니다."
                )
            value = document[self.settings.stable_key]
            if type(value) is not int or value < 1:
                raise AtlasSourceShapeError(
                    "preflight 이후 Atlas 안정 키 타입이 변경되었습니다."
                )
            if previous_value is not None and value <= previous_value:
                raise AtlasSourceShapeError(
                    "Atlas keyset 결과가 안전하게 재시작될 수 없습니다."
                )
            previous_value = value

    def read_batch(
        self,
        limit: int = DEFAULT_BATCH_LIMIT,
        cursor: AtlasCursor | None = None,
    ) -> AtlasBatch:
        """검증된 안정 키 기준으로 원본 문서를 최대 limit개 읽는다.

        Args:
            limit: 반환할 최대 문서 수. 기본값은 1000이다.
            cursor: 이전 batch의 next_cursor. 전달하지 않으면 처음부터 읽는다.

        Returns:
            원본 `_id`와 모든 필드를 보존한 AtlasBatch.

        Raises:
            ValueError: limit 또는 cursor가 안정 키 계약과 다를 때.
            AtlasSourceShapeError: preflight 또는 batch 재검증이 실패할 때.
        """
        if type(limit) is not int or limit < 1:
            raise ValueError("limit은 1 이상의 정수여야 합니다.")
        if cursor is not None and not isinstance(cursor, AtlasCursor):
            raise ValueError("cursor는 AtlasCursor 또는 None이어야 합니다.")
        if cursor is not None and cursor.stable_key != self.settings.stable_key:
            raise ValueError("cursor stable_key가 reader stable_key와 다릅니다.")

        pre_report = self.preflight(cursor=cursor)
        if cursor is not None:
            self._validate_cursor_provenance(cursor, pre_report)
        query: dict[str, object] = {}
        if cursor is not None:
            query[self.settings.stable_key] = {"$gt": cursor.last_value}

        collection = self._get_collection()
        source_cursor = self._find_batch(collection, query, limit + 1)
        try:
            documents = list(islice(source_cursor, limit + 1))
        except Exception as error:
            raise AtlasSourceShapeError(
                "Atlas keyset batch 결과를 읽을 수 없습니다."
            ) from error
        self._validate_read_documents(documents, cursor)

        has_more = len(documents) > limit
        records = tuple(documents[:limit])
        candidate_cursor: AtlasCursor | None = None
        if records:
            candidate_cursor = AtlasCursor(
                stable_key=self.settings.stable_key,
                last_value=records[-1][self.settings.stable_key],
                stable_key_fingerprint=pre_report.stable_key_fingerprint,
            )
        post_report = self.preflight(cursor=candidate_cursor)
        if self._source_fence_signature(pre_report) != self._source_fence_signature(
            post_report
        ):
            raise AtlasSourceShapeError(
                "Atlas source가 batch read 전후에 변경되었습니다."
            )
        next_cursor: AtlasCursor | None = None
        if candidate_cursor is not None:
            if post_report.stable_key_fingerprint is None:
                raise AtlasSourceShapeError(
                    "Atlas post-read fingerprint를 만들 수 없습니다."
                )
            if not post_report.cursor_last_value_present:
                raise AtlasSourceShapeError(
                    "Atlas post-read cursor 위치가 source에 없습니다."
                )
            if post_report.cursor_prefix_fingerprint is None:
                raise AtlasSourceShapeError(
                    "Atlas post-read prefix fingerprint를 만들 수 없습니다."
                )
            next_cursor = AtlasCursor(
                stable_key=self.settings.stable_key,
                last_value=candidate_cursor.last_value,
                stable_key_fingerprint=post_report.stable_key_fingerprint,
                stable_key_prefix_fingerprint=post_report.cursor_prefix_fingerprint,
            )
        return AtlasBatch(
            records=records,
            next_cursor=next_cursor,
            has_more=has_more,
            preflight=post_report,
        )

    def iter_batches(
        self,
        limit: int = DEFAULT_BATCH_LIMIT,
    ) -> Iterator[AtlasBatch]:
        """처음부터 마지막까지 원본을 최대 limit건씩 순서대로 반환한다.

        Args:
            limit: 한 번에 반환할 최대 문서 수. 기본값은 1000이다.

        Yields:
            원본 순서를 유지한 nonempty AtlasBatch.

        Raises:
            ValueError: limit이 양의 정수가 아닐 때.
            AtlasSourceShapeError: 다음 batch cursor를 안전하게 만들 수 없을 때.
        """
        if type(limit) is not int or limit < 1:
            raise ValueError("limit은 1 이상의 정수여야 합니다.")

        cursor: AtlasCursor | None = None
        while True:
            batch = self.read_batch(limit=limit, cursor=cursor)
            if batch.records:
                yield batch
            if not batch.has_more:
                return
            if batch.next_cursor is None:
                raise AtlasSourceShapeError(
                    "다음 Atlas batch를 위한 검증된 cursor가 없습니다."
                )
            cursor = batch.next_cursor

    def close(self) -> None:
        """reader가 만든 client만 닫고 주입된 fake collection은 건드리지 않는다."""
        client = self._client
        if client is not None:
            close = getattr(client, "close", None)
            if callable(close):
                close()
        object.__setattr__(self, "_client", None)
        object.__setattr__(self, "_opened_collection", None)


def _positive_integer_argument(value: str) -> int:
    """argparse용 양의 정수 옵션을 검증한다.

    Args:
        value: command line에서 받은 문자열.

    Returns:
        1 이상의 정수.

    Raises:
        argparse.ArgumentTypeError: 정수가 아니거나 0 이하일 때.
    """
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("1 이상의 정수가 필요합니다.") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("1 이상의 정수가 필요합니다.")
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """원본 값·비밀 없이 Bronze collection 실행 옵션을 해석한다.

    Args:
        argv: 테스트 또는 실행기가 전달할 선택적 인자 목록.

    Returns:
        검증된 argparse Namespace.
    """
    parser = argparse.ArgumentParser(description="Atlas 원본 Bronze batch 수집")
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--batch-sequence", required=True, type=_positive_integer_argument
    )
    parser.add_argument(
        "--limit", default=DEFAULT_BATCH_LIMIT, type=_positive_integer_argument
    )
    parser.add_argument("--resume-manifest", type=Path)
    parser.add_argument("--output-root", default=Path("data/runs"), type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Atlas preflight, keyset read, append-only raw write를 순서대로 실행한다.

    Args:
        argv: command line 인자. 없으면 현재 process 인자를 사용한다.

    Returns:
        성공 시 0.

    Raises:
        AtlasSourceShapeError: source preflight 또는 batch 검증이 실패할 때.
        FileExistsError: 동일한 finalized Bronze artifact가 이미 있을 때.
    """
    args = parse_args(argv)
    settings = AtlasSettings.from_environment()
    input_cursor = None
    if args.resume_manifest is not None:
        resume_cursor = load_finalized_resume_cursor(
            args.resume_manifest,
            dataset_id=args.dataset_id,
            snapshot_id=args.snapshot_id,
            run_id=args.run_id,
            collection_metadata=settings.collection_metadata(),
        )
        input_cursor = AtlasCursor(
            stable_key=resume_cursor.stable_key,
            last_value=resume_cursor.last_value,
            stable_key_fingerprint=resume_cursor.stable_key_fingerprint,
            stable_key_prefix_fingerprint=resume_cursor.stable_key_prefix_fingerprint,
        )
    reader = AtlasSourceReader(settings)
    try:
        reader.preflight(cursor=input_cursor)
        batch = reader.read_batch(limit=args.limit, cursor=input_cursor)
        store = RawBatchStore(args.output_root)
        resume_cursor = None
        if batch.next_cursor is not None:
            resume_cursor = {
                "stable_key": batch.next_cursor.stable_key,
                "last_value": batch.next_cursor.last_value,
                "stable_key_fingerprint": batch.next_cursor.stable_key_fingerprint,
                "stable_key_prefix_fingerprint": batch.next_cursor.stable_key_prefix_fingerprint,
            }
        artifact = store.persist_batch(
            dataset_id=args.dataset_id,
            snapshot_id=args.snapshot_id,
            run_id=args.run_id,
            batch_sequence=args.batch_sequence,
            records=batch.records,
            collection_metadata=settings.collection_metadata(),
            resume_cursor=resume_cursor,
        )
    finally:
        reader.close()
    print(
        f"rows={artifact.row_count} artifact={artifact.batch_path} "
        f"checksum={artifact.sha256}"
    )
    return 0


__all__ = [
    "DEFAULT_BATCH_LIMIT",
    "DEFAULT_STABLE_KEY",
    "AtlasBatch",
    "AtlasCursor",
    "AtlasPreflight",
    "AtlasSettings",
    "AtlasSourceReader",
    "AtlasSourceShapeError",
    "main",
    "parse_args",
]


if __name__ == "__main__":
    raise SystemExit(main())
