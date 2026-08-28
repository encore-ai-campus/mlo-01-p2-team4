"""Atlas 원본을 ``_id`` 순서로 Silver에 전달하고 처리 완료 ID를 보존한다.

이 모듈은 Atlas 문서를 읽는 동안 원본을 변경하지 않는다. 처리 상태는 Silver 등
호출자가 성공을 확정한 뒤 ``mark_processed()``를 호출할 때만 전진한다.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pymongo import ASCENDING

from .atlas_download import AtlasSettings, _new_mongo_client


@dataclass(frozen=True, slots=True)
class AtlasIncrementalBatch:
    """원본 mapping을 변경 없이 묶은 Silver 전달용 batch."""

    records: tuple[Mapping[str, Any], ...]

    def __post_init__(self) -> None:
        """호출자가 전달한 records를 불변 batch 경계인 tuple로 고정한다."""
        object.__setattr__(self, "records", tuple(self.records))


class AtlasIncrementalPipeline:
    """Atlas ``_id`` 오름차순 reader와 명시적 처리 확인 상태를 관리한다."""

    def __init__(
        self,
        settings: AtlasSettings,
        processed_ids_path: Path,
    ) -> None:
        """연결 설정과 처리 완료 ID 상태 파일 경계를 초기화한다.

        Atlas client는 실제 iteration 전까지 만들지 않는다. 상태 파일이 있으면
        생성 시점에 먼저 검증하므로, 손상된 상태로 Atlas를 읽지 않는다.

        Args:
            settings: ``atlas_download.py``의 검증된 Atlas 연결 설정.
            processed_ids_path: 처리 완료 ``_id`` 문자열을 보존할 JSON 파일.

        Raises:
            TypeError: 인자가 공개 생성자 계약과 다를 때.
            RuntimeError: 기존 상태 파일이 손상되었거나 읽을 수 없을 때.
        """
        if not isinstance(settings, AtlasSettings):
            raise TypeError("settings는 AtlasSettings여야 합니다.")
        if not isinstance(processed_ids_path, Path):
            raise TypeError("processed_ids_path는 Path여야 합니다.")

        self.settings = settings
        self.processed_ids_path = processed_ids_path
        self._client: Any | None = None
        self._collection: Any | None = None
        self._processed_ids = self._load_processed_ids()

    def _load_processed_ids(self) -> set[str]:
        """상태 파일을 엄격히 검증하고 처리 완료 ID 집합을 읽는다.

        Returns:
            중복 없는 처리 완료 ``_id`` 문자열 집합. 파일이 없으면 빈 집합.

        Raises:
            RuntimeError: JSON, 타입, 중복 또는 정렬 계약이 손상되었을 때.
        """
        try:
            with self.processed_ids_path.open(encoding="utf-8") as state_file:
                payload = json.load(state_file)
        except FileNotFoundError:
            return set()
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RuntimeError(
                "processed IDs 상태 파일을 안전하게 읽을 수 없습니다."
            ) from error

        if type(payload) is not list or any(
            type(value) is not str for value in payload
        ):
            raise RuntimeError("processed IDs 상태 파일 형식이 올바르지 않습니다.")
        if len(payload) != len(set(payload)):
            raise RuntimeError("processed IDs 상태 파일에 중복 ID가 있습니다.")
        if payload != sorted(payload):
            raise RuntimeError("processed IDs 상태 파일 순서가 결정적이지 않습니다.")
        return set(payload)

    def _write_processed_ids(self, processed_ids: set[str]) -> None:
        """처리 완료 ID를 같은 디렉터리의 임시 파일로 쓴 뒤 원자 교체한다.

        Args:
            processed_ids: 문자열 직렬화와 중복 제거가 끝난 전체 상태.

        Raises:
            RuntimeError: 디렉터리 생성, 임시 파일 기록 또는 원자 교체가 실패할 때.
        """
        parent = self.processed_ids_path.parent
        temporary_path: Path | None = None
        descriptor: int | None = None
        try:
            parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                dir=parent,
                prefix=f".{self.processed_ids_path.name}.",
                suffix=".tmp",
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(
                descriptor, "w", encoding="utf-8", newline="\n"
            ) as state_file:
                descriptor = None
                json.dump(
                    sorted(processed_ids),
                    state_file,
                    ensure_ascii=False,
                    indent=2,
                )
                state_file.write("\n")
                state_file.flush()
                os.fsync(state_file.fileno())
            os.replace(temporary_path, self.processed_ids_path)
            temporary_path = None
        except OSError as error:
            raise RuntimeError(
                "processed IDs 상태 파일을 원자적으로 저장할 수 없습니다."
            ) from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass

    def _get_collection(self) -> Any:
        """기존 Atlas lazy client 구현으로 대상 collection을 한 번만 연다.

        Returns:
            설정된 database와 collection의 PyMongo 호환 객체.

        Raises:
            RuntimeError: client 또는 collection을 안전하게 만들 수 없을 때.
        """
        if self._collection is not None:
            return self._collection

        client: Any | None = None
        try:
            client = _new_mongo_client(self.settings.uri)
            collection = client[self.settings.database][self.settings.collection]
        except Exception as error:
            if client is not None:
                close = getattr(client, "close", None)
                if callable(close):
                    close()
            raise RuntimeError("Atlas 컬렉션 객체를 만들 수 없습니다.") from error

        self._client = client
        self._collection = collection
        return collection

    def _find_source_page(self, *, after_id: object | None, limit: int) -> Any:
        """MongoDB 서버에 ``_id`` keyset 조건과 조회 상한을 적용한다.

        Args:
            after_id: 직전 physical page의 마지막 ``_id``. 첫 page이면 None.
            limit: Atlas가 한 번에 반환할 최대 원본 문서 수.

        Returns:
            ``_id`` 오름차순과 server-side limit가 적용된 cursor.
        """
        query: dict[str, object] = {}
        if after_id is not None:
            query["_id"] = {"$gt": after_id}
        try:
            return self._get_collection().find(
                query,
                sort=[("_id", ASCENDING)],
                limit=limit,
            )
        except Exception as error:
            raise RuntimeError(
                "Atlas _id keyset page 질의를 실행할 수 없습니다."
            ) from error

    def _probe_source_id_type(self) -> type[Any] | None:
        """batch 반환 전에 전체 source의 ``_id`` 존재와 단일 타입을 검증한다.

        Returns:
            빈 source이면 None, 문서가 있으면 모든 ``_id``가 공유하는 정확한 타입.

        Raises:
            RuntimeError: ``_id``가 누락되거나 둘 이상의 타입이 섞였을 때.
        """
        try:
            source_cursor = self._get_collection().find(
                {},
                {"_id": 1},
                sort=[("_id", ASCENDING)],
            )
        except Exception as error:
            raise RuntimeError(
                "Atlas _id 구조 검증 질의를 실행할 수 없습니다."
            ) from error

        expected_type: type[Any] | None = None
        try:
            for document in source_cursor:
                if not isinstance(document, Mapping) or "_id" not in document:
                    raise RuntimeError("Atlas 원본 문서에 _id가 없습니다.")
                current_type = type(document["_id"])
                if expected_type is None:
                    expected_type = current_type
                elif current_type is not expected_type:
                    raise RuntimeError(
                        "Atlas 원본의 _id 타입이 섞여 있어 안전하게 정렬할 수 없습니다."
                    )
        except RuntimeError:
            raise
        except Exception as error:
            raise RuntimeError(
                "Atlas _id 구조 검증 결과를 읽을 수 없습니다."
            ) from error
        finally:
            close = getattr(source_cursor, "close", None)
            if callable(close):
                close()
        return expected_type

    def iter_batches(self, limit: int) -> Iterator[AtlasIncrementalBatch]:
        """미처리 원본을 ``_id`` 오름차순으로 최대 ``limit``건씩 반환한다.

        이 메서드는 처리 상태를 자동으로 변경하지 않는다. 호출자는 downstream
        처리가 성공한 batch의 ``_id``만 ``mark_processed()``에 전달해야 한다.

        Args:
            limit: batch 하나에 포함할 최대 미처리 문서 수.

        Yields:
            원본 mapping과 ``_id``를 그대로 참조하는 nonempty batch.

        Raises:
            ValueError: limit이 양의 정수가 아닐 때.
            RuntimeError: 상태 파일 또는 Atlas 원본을 안전하게 읽을 수 없을 때.
        """
        if type(limit) is not int or limit < 1:
            raise ValueError("limit은 1 이상의 정수여야 합니다.")

        # 실행 중 외부에서 상태가 손상되거나 갱신된 경우 Atlas 조회 전에 재검증한다.
        self._processed_ids = self._load_processed_ids()
        expected_id_type = self._probe_source_id_type()
        records: list[Mapping[str, Any]] = []
        after_id: object | None = None
        while True:
            source_cursor = self._find_source_page(after_id=after_id, limit=limit)
            try:
                page = list(source_cursor)
            except Exception as error:
                raise RuntimeError(
                    "Atlas _id keyset page를 읽을 수 없습니다."
                ) from error
            finally:
                close = getattr(source_cursor, "close", None)
                if callable(close):
                    close()

            if len(page) > limit:
                raise RuntimeError("Atlas page가 요청한 limit을 초과했습니다.")
            if not page:
                break

            previous_id = after_id
            for document in page:
                if not isinstance(document, Mapping) or "_id" not in document:
                    raise RuntimeError("Atlas 원본 문서에 _id가 없습니다.")
                source_id_value = document["_id"]
                if (
                    expected_id_type is None
                    or type(source_id_value) is not expected_id_type
                ):
                    raise RuntimeError(
                        "Atlas 원본의 _id 타입이 구조 검증 결과와 다릅니다."
                    )
                if previous_id is not None:
                    try:
                        is_strictly_increasing = source_id_value > previous_id
                    except TypeError as error:
                        raise RuntimeError(
                            "Atlas 원본의 _id를 안전하게 비교할 수 없습니다."
                        ) from error
                    if not is_strictly_increasing:
                        raise RuntimeError(
                            "Atlas _id keyset 결과가 오름차순이 아닙니다."
                        )
                previous_id = source_id_value
                after_id = source_id_value
                source_id = str(source_id_value)
                if source_id in self._processed_ids:
                    continue
                records.append(document)
                if len(records) == limit:
                    yield AtlasIncrementalBatch(records=tuple(records))
                    records = []

            if len(page) < limit:
                break

        if records:
            yield AtlasIncrementalBatch(records=tuple(records))

    def mark_processed(self, source_ids: Iterable[object]) -> None:
        """호출자가 성공을 확정한 source ``_id``만 처리 완료로 저장한다.

        Args:
            source_ids: 성공한 원본 문서의 ``_id`` iterable.

        Raises:
            TypeError: 문자열 하나를 iterable 대신 전달했거나 iterable이 아닐 때.
            RuntimeError: 현재 상태가 손상되었거나 원자 저장이 실패할 때.
        """
        if isinstance(source_ids, (str, bytes)):
            raise TypeError("source_ids는 단일 문자열이 아닌 ID iterable이어야 합니다.")
        try:
            serialized_ids = {str(source_id) for source_id in source_ids}
        except TypeError as error:
            raise TypeError("source_ids는 반복 가능한 값이어야 합니다.") from error
        if not serialized_ids:
            return

        current_ids = self._load_processed_ids()
        updated_ids = current_ids | serialized_ids
        self._processed_ids = current_ids
        if updated_ids == current_ids:
            return
        self._write_processed_ids(updated_ids)
        self._processed_ids = updated_ids

    def close(self) -> None:
        """pipeline이 만든 Atlas client를 닫고 재사용 가능한 lazy 상태로 되돌린다."""
        client = self._client
        try:
            if client is not None:
                close = getattr(client, "close", None)
                if callable(close):
                    close()
        finally:
            self._client = None
            self._collection = None


__all__ = ["AtlasIncrementalBatch", "AtlasIncrementalPipeline"]
