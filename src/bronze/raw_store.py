"""Atlas 원본 문서를 append-only Bronze JSONL과 manifest로 보존한다."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bson import json_util
from bson.json_util import RELAXED_JSON_OPTIONS


@dataclass(frozen=True, slots=True)
class RawRowLocator:
    """원본 JSONL 안의 한 행을 원본값 없이 가리키는 위치 정보다."""

    line_number: int
    byte_offset: int
    locator: str


@dataclass(frozen=True, slots=True)
class RawBatchArtifact:
    """finalized append-only Bronze batch와 manifest의 검증 가능한 결과다."""

    batch_path: Path
    manifest_path: Path
    row_count: int
    sha256: str
    row_locators: tuple[RawRowLocator, ...]


@dataclass(frozen=True, slots=True)
class RawResumeCursor:
    """검증된 finalized manifest에서만 복원하는 다음 Bronze 재시작 위치다."""

    stable_key: str
    last_value: int
    stable_key_fingerprint: str
    stable_key_prefix_fingerprint: str


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


def _validate_path_component(name: str, value: str) -> str:
    """dataset/snapshot/run 식별자가 Bronze root 밖으로 나가지 않게 검증한다.

    Args:
        name: 오류 메시지에 사용할 식별자 이름.
        value: 디렉터리명으로 사용할 식별자.

    Returns:
        검증된 식별자 문자열.

    Raises:
        ValueError: 빈 값, 경로 구분자, 현재/상위 디렉터리 표기가 있을 때.
    """
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name}은 비어 있지 않은 문자열이어야 합니다.")
    if value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError(f"{name}은 단일 경로 구성요소여야 합니다.")
    return value


def _validate_collection_metadata(metadata: Mapping[str, object]) -> dict[str, str]:
    """manifest에 필요한 비밀 없는 컬렉션 식별 metadata만 허용한다.

    Args:
        metadata: database, collection, stable_key를 담은 mapping.

    Returns:
        manifest에 기록할 검증된 metadata 사본.

    Raises:
        ValueError: 필수 metadata가 없거나 문자열이 아닐 때.
    """
    sanitized: dict[str, str] = {}
    for name in ("database", "collection", "stable_key"):
        value = metadata.get(name)
        if type(value) is not str or not value.strip():
            raise ValueError(f"collection metadata {name}이 유효하지 않습니다.")
        if name == "stable_key" and value != "record_id":
            raise ValueError("I1 Atlas Bronze 안정 키는 record_id로 고정됩니다.")
        sanitized[name] = value
    return sanitized


def _validate_resume_cursor(
    resume_cursor: Mapping[str, object] | None,
) -> dict[str, object] | None:
    """cross-process 재시작에 필요한 안전한 cursor provenance를 검증한다.

    Args:
        resume_cursor: stable_key, last_value, stable_key_fingerprint를 담은 mapping.

    Returns:
        manifest에 기록할 검증된 cursor metadata 또는 None.

    Raises:
        TypeError: cursor provenance가 mapping이 아닐 때.
        ValueError: cursor provenance 값이나 SHA-256 형식이 유효하지 않을 때.
    """
    if resume_cursor is None:
        return None
    if not isinstance(resume_cursor, Mapping):
        raise TypeError("resume cursor는 mapping이어야 합니다.")
    stable_key = resume_cursor.get("stable_key")
    last_value = resume_cursor.get("last_value")
    fingerprint = resume_cursor.get("stable_key_fingerprint")
    if type(stable_key) is not str or not stable_key.strip():
        raise ValueError("resume cursor stable_key가 유효하지 않습니다.")
    if stable_key != "record_id":
        raise ValueError("I1 Atlas Bronze cursor 안정 키는 record_id로 고정됩니다.")
    if type(last_value) is not int or last_value < 1:
        raise ValueError("resume cursor last_value가 유효하지 않습니다.")
    prefix_fingerprint = resume_cursor.get("stable_key_prefix_fingerprint")
    if not _is_sha256(fingerprint):
        raise ValueError("resume cursor stable_key_fingerprint가 유효하지 않습니다.")
    if not _is_sha256(prefix_fingerprint):
        raise ValueError(
            "resume cursor stable_key_prefix_fingerprint가 유효하지 않습니다."
        )
    return {
        "stable_key": stable_key,
        "last_value": last_value,
        "stable_key_fingerprint": fingerprint,
        "stable_key_prefix_fingerprint": prefix_fingerprint,
    }


def _serialize_raw_document(document: Mapping[str, Any]) -> bytes:
    """BSON 타입을 Extended JSON으로 안전하게 직렬화하되 원본 필드를 유지한다.

    Args:
        document: Atlas에서 읽은 원본 mapping.

    Returns:
        UTF-8 newline을 포함한 JSONL 한 행 bytes.

    Raises:
        TypeError: document가 mapping이 아닐 때.
        ValueError: BSON-safe JSON 직렬화가 불가능할 때.
    """
    if not isinstance(document, Mapping):
        raise TypeError("Bronze raw document는 mapping이어야 합니다.")
    try:
        serialized = json_util.dumps(
            document,
            json_options=RELAXED_JSON_OPTIONS,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise ValueError(
            "Bronze raw document를 BSON-safe JSON으로 직렬화할 수 없습니다."
        ) from error
    return serialized.encode("utf-8") + b"\n"


def _assert_final_paths_available(batch_path: Path, manifest_path: Path) -> None:
    """기존 pair 또는 일방 artifact가 append-only publication을 막게 한다.

    Args:
        batch_path: final raw JSONL 경로.
        manifest_path: final manifest 경로.

    Raises:
        FileExistsError: 완성 pair 또는 복구가 필요한 일방 artifact가 존재할 때.
    """
    batch_exists = batch_path.exists()
    manifest_exists = manifest_path.exists()
    if batch_exists and manifest_exists:
        raise FileExistsError(
            "finalized Bronze raw batch artifact를 덮어쓸 수 없습니다."
        )
    if batch_exists or manifest_exists:
        raise FileExistsError(
            "불완전한 Bronze artifact가 있어 안전한 복구 전에는 재시도할 수 없습니다."
        )


def _write_staged_file(path: Path, content: bytes) -> None:
    """private staging 파일을 exclusive mode로 작성한다.

    Args:
        path: 같은 filesystem staging 경로.
        content: 작성할 완전한 bytes.
    """
    with path.open("xb") as staged_file:
        staged_file.write(content)


def _publish_exclusive(staged_path: Path, final_path: Path) -> None:
    """hard link로 staged bytes를 overwrite 없이 final 경로에 publish한다.

    Args:
        staged_path: 같은 filesystem의 완성 staging 파일.
        final_path: 아직 존재하지 않아야 하는 final 경로.

    Raises:
        OSError: hard link publication이 실패하거나 final 경로가 이미 있을 때.
    """
    os.link(staged_path, final_path)


def _remove_staging_directory(staging_directory: Path, staging_root: Path) -> None:
    """완료 또는 실패한 private staging 상태를 정리한다.

    Args:
        staging_directory: 이번 publication에만 사용한 staging 디렉터리.
        staging_root: 비어 있으면 제거할 공통 staging 디렉터리.
    """
    shutil.rmtree(staging_directory, ignore_errors=True)
    try:
        staging_root.rmdir()
    except OSError:
        pass


def _manifest_sequence_and_batch_path(manifest_path: Path) -> tuple[int, Path]:
    """정확한 Bronze manifest 위치에서 대응하는 raw JSONL 경로를 계산한다.

    Args:
        manifest_path: 사용자가 전달한 resume manifest 경로.

    Returns:
        양의 batch sequence와 대응하는 raw JSONL 경로.

    Raises:
        ValueError: manifest가 Bronze finalized 위치/이름 계약을 따르지 않을 때.
    """
    if manifest_path.suffix != ".json":
        raise ValueError("resume manifest 확장자가 유효하지 않습니다.")
    if manifest_path.parent.name != "manifests":
        raise ValueError("resume manifest는 Bronze manifests 디렉터리에 있어야 합니다.")
    bronze_directory = manifest_path.parent.parent
    if bronze_directory.name != "bronze":
        raise ValueError("resume manifest Bronze 경로가 유효하지 않습니다.")
    sequence_text = manifest_path.stem
    if not sequence_text.isdecimal():
        raise ValueError("resume manifest batch sequence가 유효하지 않습니다.")
    batch_sequence = int(sequence_text)
    if batch_sequence < 1 or str(batch_sequence) != sequence_text:
        raise ValueError("resume manifest batch sequence가 유효하지 않습니다.")
    return batch_sequence, bronze_directory / "batches" / f"{batch_sequence}.jsonl"


def _sha256_file(path: Path) -> str:
    """large raw artifact도 일정한 메모리로 SHA-256을 계산한다.

    Args:
        path: checksum을 계산할 bytes 파일.

    Returns:
        파일 전체 bytes의 소문자 SHA-256 hex.
    """
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while True:
            chunk = file.read(65536)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _validate_raw_record_ids(batch_path: Path, expected_row_count: int) -> int | None:
    """finalized raw JSONL의 행 수와 record_id 정렬을 resume 경계로 검증한다.

    Args:
        batch_path: 대응하는 raw JSONL 경로.
        expected_row_count: manifest가 선언한 행 수.

    Returns:
        비어 있지 않은 raw batch의 마지막 record_id, 아니면 None.

    Raises:
        TypeError: raw JSONL 문서 또는 manifest 구조가 mapping이 아닐 때.
        ValueError: raw JSONL 구조, 행 수, record_id keyset이 계약과 다를 때.
    """
    record_count = 0
    previous_record_id: int | None = None
    try:
        with batch_path.open("rb") as raw_file:
            for raw_line in raw_file:
                if not raw_line.endswith(b"\n"):
                    raise ValueError("finalized raw JSONL 줄바꿈이 유효하지 않습니다.")
                try:
                    document = json_util.loads(raw_line.decode("utf-8"))
                except (UnicodeDecodeError, ValueError, TypeError) as error:
                    raise ValueError(
                        "finalized raw JSONL 문서를 읽을 수 없습니다."
                    ) from error
                if not isinstance(document, Mapping):
                    raise TypeError(
                        "finalized raw JSONL 문서 구조가 유효하지 않습니다."
                    )
                record_id = document.get("record_id")
                if type(record_id) is not int or record_id < 1:
                    raise ValueError(
                        "finalized raw JSONL record_id가 유효하지 않습니다."
                    )
                if previous_record_id is not None and record_id <= previous_record_id:
                    raise ValueError(
                        "finalized raw JSONL record_id 정렬이 유효하지 않습니다."
                    )
                previous_record_id = record_id
                record_count += 1
    except OSError as error:
        raise ValueError("finalized raw JSONL을 읽을 수 없습니다.") from error
    if record_count != expected_row_count:
        raise ValueError("finalized raw JSONL 행 수가 manifest와 다릅니다.")
    return previous_record_id


def load_finalized_resume_cursor(
    manifest_path: Path,
    *,
    dataset_id: str,
    snapshot_id: str,
    run_id: str,
    collection_metadata: Mapping[str, object],
) -> RawResumeCursor:
    """finalized manifest/raw pair를 검증하고 신뢰 가능한 resume cursor만 복원한다.

    Args:
        manifest_path: 이전 batch의 finalized manifest 경로.
        dataset_id: 현재 CLI 실행의 dataset 경계.
        snapshot_id: 현재 CLI 실행의 snapshot 경계.
        run_id: 현재 CLI 실행의 run 경계.
        collection_metadata: 현재 Atlas settings에서 만든 컬렉션 식별 metadata.

    Returns:
        checksum·경계·raw 마지막 record_id가 검증된 resume cursor.

    Raises:
        TypeError: manifest 또는 collection metadata 구조가 mapping이 아닐 때.
        ValueError: manifest/raw pair, checksum, 경계, cursor provenance가 유효하지 않을 때.
    """
    safe_dataset_id = _validate_path_component("dataset_id", dataset_id)
    safe_snapshot_id = _validate_path_component("snapshot_id", snapshot_id)
    safe_run_id = _validate_path_component("run_id", run_id)
    expected_metadata = _validate_collection_metadata(collection_metadata)
    resolved_manifest_path = Path(manifest_path)
    batch_sequence, batch_path = _manifest_sequence_and_batch_path(
        resolved_manifest_path
    )
    bronze_directory = resolved_manifest_path.parent.parent
    run_directory = bronze_directory.parent
    snapshot_directory = run_directory.parent
    dataset_directory = snapshot_directory.parent
    if (
        run_directory.name != safe_run_id
        or snapshot_directory.name != safe_snapshot_id
        or dataset_directory.name != safe_dataset_id
    ):
        raise ValueError("resume manifest artifact 경계가 현재 실행과 다릅니다.")
    if resolved_manifest_path.is_symlink() or batch_path.is_symlink():
        raise ValueError("resume manifest/raw symlink는 허용하지 않습니다.")
    try:
        manifest = json.loads(resolved_manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("finalized resume manifest를 읽을 수 없습니다.") from error
    if not isinstance(manifest, Mapping):
        raise TypeError("finalized resume manifest 구조가 유효하지 않습니다.")
    if manifest.get("artifact_type") != "bronze_raw_batch":
        raise ValueError("resume manifest artifact type이 유효하지 않습니다.")
    if manifest.get("dataset_id") != safe_dataset_id:
        raise ValueError("resume manifest dataset 경계가 다릅니다.")
    if manifest.get("snapshot_id") != safe_snapshot_id:
        raise ValueError("resume manifest snapshot 경계가 다릅니다.")
    if manifest.get("run_id") != safe_run_id:
        raise ValueError("resume manifest run 경계가 다릅니다.")
    if manifest.get("batch_sequence") != batch_sequence:
        raise ValueError("resume manifest batch sequence가 다릅니다.")
    if manifest.get("batch_path") != f"batches/{batch_sequence}.jsonl":
        raise ValueError("resume manifest raw 경로가 유효하지 않습니다.")
    row_count = manifest.get("row_count")
    if type(row_count) is not int or row_count < 0:
        raise ValueError("resume manifest row_count가 유효하지 않습니다.")
    row_locators = manifest.get("row_locators")
    if not isinstance(row_locators, list) or len(row_locators) != row_count:
        raise ValueError("resume manifest row locator가 유효하지 않습니다.")
    metadata = manifest.get("collection_metadata")
    if not isinstance(metadata, Mapping):
        raise TypeError("resume manifest collection metadata가 유효하지 않습니다.")
    if _validate_collection_metadata(metadata) != expected_metadata:
        raise ValueError("resume manifest collection metadata 경계가 다릅니다.")
    expected_checksum = manifest.get("sha256")
    if not _is_sha256(expected_checksum):
        raise ValueError("resume manifest SHA-256이 유효하지 않습니다.")
    try:
        actual_checksum = _sha256_file(batch_path)
    except OSError as error:
        raise ValueError("finalized resume raw artifact를 읽을 수 없습니다.") from error
    if actual_checksum != expected_checksum:
        raise ValueError("resume manifest와 raw artifact checksum이 다릅니다.")
    actual_last_value = _validate_raw_record_ids(batch_path, row_count)
    resume_cursor = _validate_resume_cursor(manifest.get("resume_cursor"))
    if resume_cursor is None or actual_last_value is None:
        raise ValueError("resume manifest에 재시작 가능한 cursor가 없습니다.")
    if resume_cursor["last_value"] != actual_last_value:
        raise ValueError("resume manifest cursor 위치가 raw artifact와 다릅니다.")
    return RawResumeCursor(
        stable_key=resume_cursor["stable_key"],
        last_value=resume_cursor["last_value"],
        stable_key_fingerprint=resume_cursor["stable_key_fingerprint"],
        stable_key_prefix_fingerprint=resume_cursor["stable_key_prefix_fingerprint"],
    )


@dataclass(frozen=True, slots=True)
class RawBatchStore:
    """지정된 data/runs root 아래에 finalized raw Bronze batch만 추가한다."""

    root: Path = Path("data/runs")

    def __post_init__(self) -> None:
        """path-like root를 Path로 고정한다.

        Raises:
            TypeError: root가 path-like 값이 아닐 때.
        """
        try:
            object.__setattr__(self, "root", Path(self.root))
        except TypeError as error:
            raise TypeError("Bronze root는 경로여야 합니다.") from error

    def persist_batch(
        self,
        *,
        dataset_id: str,
        snapshot_id: str,
        run_id: str,
        batch_sequence: int,
        records: Sequence[Mapping[str, Any]],
        collection_metadata: Mapping[str, object],
        resume_cursor: Mapping[str, object] | None = None,
    ) -> RawBatchArtifact:
        """원본 문서를 JSONL과 checksum manifest로 append-only 저장한다.

        Args:
            dataset_id: 수집 dataset 식별자.
            snapshot_id: 수집 시점 snapshot 식별자.
            run_id: 재실행을 구분하는 run 식별자.
            batch_sequence: run 내 1부터 시작하는 batch 순서.
            records: 필드를 제거·정규화하지 않은 원본 문서들.
            collection_metadata: database, collection, stable_key 식별 정보.
            resume_cursor: 다음 재시작용 안정 키와 source fingerprint metadata.

        Returns:
            raw JSONL, manifest, checksum, row locator를 가진 artifact.

        Raises:
            ValueError: 식별자·sequence·metadata가 유효하지 않을 때.
            FileExistsError: 기존 raw 또는 manifest artifact가 있어 overwrite 위험이 있을 때.
        """
        safe_dataset_id = _validate_path_component("dataset_id", dataset_id)
        safe_snapshot_id = _validate_path_component("snapshot_id", snapshot_id)
        safe_run_id = _validate_path_component("run_id", run_id)
        if type(batch_sequence) is not int or batch_sequence < 1:
            raise ValueError("batch_sequence는 1 이상의 정수여야 합니다.")
        safe_metadata = _validate_collection_metadata(collection_metadata)
        safe_resume_cursor = _validate_resume_cursor(resume_cursor)

        encoded_rows: list[bytes] = []
        for record in records:
            encoded_rows.append(_serialize_raw_document(record))

        bronze_directory = (
            self.root / safe_dataset_id / safe_snapshot_id / safe_run_id / "bronze"
        )
        batch_directory = bronze_directory / "batches"
        manifest_directory = bronze_directory / "manifests"
        batch_path = batch_directory / f"{batch_sequence}.jsonl"
        manifest_path = manifest_directory / f"{batch_sequence}.json"
        batch_directory.mkdir(parents=True, exist_ok=True)
        manifest_directory.mkdir(parents=True, exist_ok=True)
        _assert_final_paths_available(batch_path, manifest_path)
        checksum = hashlib.sha256()
        byte_offset = 0
        row_locators: list[RawRowLocator] = []
        relative_batch_path = batch_path.relative_to(bronze_directory).as_posix()
        for index, encoded_row in enumerate(encoded_rows, start=1):
            row_locators.append(
                RawRowLocator(
                    line_number=index,
                    byte_offset=byte_offset,
                    locator=f"{relative_batch_path}#L{index}",
                )
            )
            checksum.update(encoded_row)
            byte_offset += len(encoded_row)

        manifest = {
            "artifact_type": "bronze_raw_batch",
            "dataset_id": safe_dataset_id,
            "snapshot_id": safe_snapshot_id,
            "run_id": safe_run_id,
            "batch_sequence": batch_sequence,
            "row_count": len(encoded_rows),
            "sha256": checksum.hexdigest(),
            "collection_metadata": safe_metadata,
            "resume_cursor": safe_resume_cursor,
            "batch_path": relative_batch_path,
            "row_locators": [
                {
                    "line_number": locator.line_number,
                    "byte_offset": locator.byte_offset,
                    "locator": locator.locator,
                }
                for locator in row_locators
            ],
        }
        manifest_bytes = (
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        staging_root = bronze_directory / ".staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        staging_directory = Path(
            tempfile.mkdtemp(prefix=f"batch-{batch_sequence}-", dir=staging_root)
        )
        staged_batch_path = staging_directory / batch_path.name
        staged_manifest_path = staging_directory / manifest_path.name
        published_batch = False
        try:
            _write_staged_file(staged_batch_path, b"".join(encoded_rows))
            _write_staged_file(staged_manifest_path, manifest_bytes)
            _publish_exclusive(staged_batch_path, batch_path)
            published_batch = True
            _publish_exclusive(staged_manifest_path, manifest_path)
        except Exception:
            if published_batch:
                try:
                    batch_path.unlink()
                except FileNotFoundError:
                    pass
            raise
        finally:
            _remove_staging_directory(staging_directory, staging_root)

        return RawBatchArtifact(
            batch_path=batch_path,
            manifest_path=manifest_path,
            row_count=len(encoded_rows),
            sha256=checksum.hexdigest(),
            row_locators=tuple(row_locators),
        )

    def write_batch(
        self,
        *,
        dataset_id: str,
        snapshot_id: str,
        run_id: str,
        batch_sequence: int,
        records: Sequence[Mapping[str, Any]],
        collection_metadata: Mapping[str, object],
        resume_cursor: Mapping[str, object] | None = None,
    ) -> RawBatchArtifact:
        """persist_batch의 의미를 드러내는 호환 가능한 쓰기 이름을 제공한다.

        Args:
            dataset_id: 수집 dataset 식별자.
            snapshot_id: 수집 시점 snapshot 식별자.
            run_id: 재실행을 구분하는 run 식별자.
            batch_sequence: run 내 batch 순서.
            records: 원본 문서들.
            collection_metadata: 비밀 없는 컬렉션 metadata.
            resume_cursor: 다음 재시작용 안정 키와 source fingerprint metadata.

        Returns:
            append-only raw Bronze artifact.
        """
        return self.persist_batch(
            dataset_id=dataset_id,
            snapshot_id=snapshot_id,
            run_id=run_id,
            batch_sequence=batch_sequence,
            records=records,
            collection_metadata=collection_metadata,
            resume_cursor=resume_cursor,
        )


__all__ = [
    "RawBatchArtifact",
    "RawBatchStore",
    "RawResumeCursor",
    "RawRowLocator",
    "load_finalized_resume_cursor",
]
