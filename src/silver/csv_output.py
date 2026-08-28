"""기존 CSV를 보존하며 새 Silver 결과를 staging에 publish한다."""

from __future__ import annotations

import csv
import json
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from .normalizer import Violation

FIRST_REJECT_STAGE = "FIRST_STAGE"
SECOND_REJECT_STAGE = "SECOND_STAGE"
LINEAGE_FIELDS = ("source_id", "record_id")
REJECT_FIELDS = (
    "source_id",
    "record_id",
    "reject_stage",
    "reason_codes",
    "reason_details",
    "raw_json",
)


@dataclass(frozen=True, slots=True)
class ExistingCsvOutputs:
    """증분 실행 전에 읽은 기존 accept/reject 상태다."""

    accept_rows: tuple[dict[str, str], ...]
    reject_rows: tuple[dict[str, str], ...]
    source_ids: frozenset[str]
    accept_exists: bool
    reject_exists: bool

    @property
    def initialized(self) -> bool:
        """두 최종 CSV가 모두 존재하는지 반환한다."""
        return self.accept_exists and self.reject_exists


@dataclass(frozen=True, slots=True)
class SilverRunSummary:
    """한 번의 증분 Silver 실행 결과."""

    batch_count: int
    input_count: int
    accepted_count: int
    rejected_count: int
    first_rejected_count: int
    duplicate_rejected_count: int
    replayed_count: int
    accept_path: Path
    reject_path: Path
    source_ids: tuple[str, ...]
    normalization_input_source_count: int = 0
    normalization_accepted_source_count: int = 0
    normalization_rejected_source_count: int = 0
    normalization_reject_row_count: int = 0
    model_row_counts: tuple[tuple[str, int], ...] = ()
    normalization_reject_path: Path | None = None
    model_paths: tuple[tuple[str, Path], ...] = ()
    orphan_counts: tuple[tuple[str, int], ...] = ()


def load_existing_outputs(
    temp_dir: Path,
    *,
    output_fields: Sequence[str],
) -> ExistingCsvOutputs:
    """기존 CSV 행, accept 중복 기준, 이미 반영된 source ID를 읽는다."""
    accept_path = temp_dir / "accept.csv"
    reject_path = temp_dir / "reject.csv"
    accept_exists = accept_path.exists()
    reject_exists = reject_path.exists()
    if accept_exists != reject_exists:
        raise ValueError("기존 accept/reject CSV pair 중 한 파일만 존재합니다.")
    accept_rows = _read_csv_rows(
        accept_path,
        required_fields=("record_id", *output_fields),
    )
    reject_rows = _read_csv_rows(
        reject_path,
        required_fields=("record_id", "reason_codes", "reason_details", "raw_json"),
    )
    source_ids = _validate_existing_rows(accept_rows, reject_rows)
    return ExistingCsvOutputs(
        accept_rows=accept_rows,
        reject_rows=reject_rows,
        source_ids=source_ids,
        accept_exists=accept_exists,
        reject_exists=reject_exists,
    )


class CsvOutputTransaction:
    """두 CSV의 전체 다음 상태를 staging에 만든 뒤 함께 publish한다."""

    def __init__(
        self,
        temp_dir: Path,
        *,
        output_fields: Sequence[str],
        existing: ExistingCsvOutputs,
    ) -> None:
        self.temp_dir = temp_dir
        self.output_fields = tuple(output_fields)
        self.accept_fields = (*LINEAGE_FIELDS, *self.output_fields)
        self.existing = existing
        self.accept_path = temp_dir / "accept.csv"
        self.reject_path = temp_dir / "reject.csv"
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._staging_dir: Path | None = None
        self._accept_file = None
        self._reject_file = None
        self._accept_writer: csv.DictWriter | None = None
        self._reject_writer: csv.DictWriter | None = None
        self._published = False

    def __enter__(self) -> CsvOutputTransaction:
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(
            prefix=".silver-flat-",
            dir=self.temp_dir,
        )
        self._staging_dir = Path(self._temporary.name)
        try:
            self._accept_file = (self._staging_dir / "accept.csv").open(
                "w", encoding="utf-8", newline=""
            )
            self._reject_file = (self._staging_dir / "reject.csv").open(
                "w", encoding="utf-8", newline=""
            )
            self._accept_writer = csv.DictWriter(
                self._accept_file,
                fieldnames=self.accept_fields,
            )
            self._reject_writer = csv.DictWriter(
                self._reject_file,
                fieldnames=REJECT_FIELDS,
            )
            self._accept_writer.writeheader()
            self._reject_writer.writeheader()
            for row in self.existing.accept_rows:
                self._accept_writer.writerow(
                    {field: row.get(field, "") for field in self.accept_fields}
                )
            for row in self.existing.reject_rows:
                reject_stage = row.get("reject_stage", "").strip()
                if not reject_stage:
                    reject_stage = _infer_reject_stage(row.get("reason_codes", ""))
                migrated = {field: row.get(field, "") for field in REJECT_FIELDS}
                migrated["reject_stage"] = reject_stage
                self._reject_writer.writerow(migrated)
        except BaseException:
            self._close_files()
            if self._temporary is not None:
                self._temporary.cleanup()
            raise
        return self

    def write_accept(
        self,
        *,
        source_id: str,
        record_id: int,
        values: Mapping[str, object],
    ) -> None:
        """새 accept 행을 staging에 추가한다."""
        writer = self._require_accept_writer()
        writer.writerow(
            {
                "source_id": source_id,
                "record_id": record_id,
                **{field: values.get(field) for field in self.output_fields},
            }
        )

    def write_reject(
        self,
        *,
        source_id: str,
        record_id: int | None,
        reject_stage: str,
        violations: Sequence[Violation],
        raw_json: str,
    ) -> None:
        """새 1차 또는 2차 Reject 행을 staging에 추가한다."""
        writer = self._require_reject_writer()
        details = [
            {
                "code": violation.code,
                "field": violation.field,
                "detail": violation.detail,
            }
            for violation in violations
        ]
        writer.writerow(
            {
                "source_id": source_id,
                "record_id": record_id,
                "reject_stage": reject_stage,
                "reason_codes": "|".join(violation.code for violation in violations),
                "reason_details": json.dumps(
                    details,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "raw_json": raw_json,
            }
        )

    def publish(self) -> None:
        """완성된 두 staging 파일을 rollback 가능한 순서로 최종 반영한다."""
        self._close_files()
        if self._staging_dir is None:
            raise RuntimeError("CSV output transaction이 시작되지 않았습니다.")
        _publish_pair(
            staged_accept=self._staging_dir / "accept.csv",
            staged_reject=self._staging_dir / "reject.csv",
            accept_path=self.accept_path,
            reject_path=self.reject_path,
            backup_dir=self._staging_dir,
        )
        self._published = True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._close_files()
        if self._temporary is not None:
            self._temporary.cleanup()

    def _require_accept_writer(self) -> csv.DictWriter:
        if self._accept_writer is None or self._published:
            raise RuntimeError("accept staging writer를 사용할 수 없습니다.")
        return self._accept_writer

    def _require_reject_writer(self) -> csv.DictWriter:
        if self._reject_writer is None or self._published:
            raise RuntimeError("reject staging writer를 사용할 수 없습니다.")
        return self._reject_writer

    def _close_files(self) -> None:
        if self._accept_file is not None and not self._accept_file.closed:
            self._accept_file.close()
        if self._reject_file is not None and not self._reject_file.closed:
            self._reject_file.close()


def _read_csv_rows(
    path: Path,
    *,
    required_fields: Sequence[str],
) -> tuple[dict[str, str], ...]:
    if not path.exists():
        return ()
    with path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames is None or not set(required_fields).issubset(
            reader.fieldnames
        ):
            raise ValueError(f"기존 {path.name} header가 Silver 출력과 맞지 않습니다.")
        return tuple(
            {
                field: "" if value is None else value
                for field, value in row.items()
                if field is not None
            }
            for row in reader
        )


def _validate_existing_rows(
    accept_rows: Sequence[Mapping[str, str]],
    reject_rows: Sequence[Mapping[str, str]],
) -> frozenset[str]:
    """기존 disposition의 stage와 source lineage 유일성을 검증한다."""
    source_locations: dict[str, str] = {}
    for output_name, rows in (("accept", accept_rows), ("reject", reject_rows)):
        for row_number, row in enumerate(rows, start=2):
            source_id = row.get("source_id", "").strip()
            if not source_id:
                continue
            previous = source_locations.get(source_id)
            if previous is not None:
                raise ValueError(
                    "기존 Silver CSV에 source_id가 중복되었습니다: "
                    f"{source_id} ({previous}, {output_name}:{row_number})"
                )
            source_locations[source_id] = f"{output_name}:{row_number}"

    for row_number, row in enumerate(reject_rows, start=2):
        reject_stage = row.get("reject_stage", "").strip()
        if reject_stage and reject_stage not in {
            FIRST_REJECT_STAGE,
            SECOND_REJECT_STAGE,
        }:
            raise ValueError(
                f"기존 reject.csv의 reject_stage가 유효하지 않습니다: row={row_number}"
            )
    return frozenset(source_locations)


def _infer_reject_stage(reason_codes: str) -> str:
    if "DUPLICATE_NORMALIZED_ROW" in reason_codes.split("|"):
        return SECOND_REJECT_STAGE
    return FIRST_REJECT_STAGE


def _publish_pair(
    *,
    staged_accept: Path,
    staged_reject: Path,
    accept_path: Path,
    reject_path: Path,
    backup_dir: Path,
) -> None:
    """두 파일 중 하나의 교체가 실패하면 기존 pair를 복원한다."""
    accept_existed = accept_path.exists()
    reject_existed = reject_path.exists()
    accept_backup = backup_dir / "accept.previous.csv"
    reject_backup = backup_dir / "reject.previous.csv"
    if accept_existed:
        shutil.copy2(accept_path, accept_backup)
    if reject_existed:
        shutil.copy2(reject_path, reject_backup)

    try:
        os.replace(staged_accept, accept_path)
        os.replace(staged_reject, reject_path)
    except BaseException:
        if accept_existed:
            shutil.copy2(accept_backup, accept_path)
        else:
            accept_path.unlink(missing_ok=True)
        if reject_existed:
            shutil.copy2(reject_backup, reject_path)
        else:
            reject_path.unlink(missing_ok=True)
        raise


__all__ = [
    "CsvOutputTransaction",
    "ExistingCsvOutputs",
    "FIRST_REJECT_STAGE",
    "REJECT_FIELDS",
    "SECOND_REJECT_STAGE",
    "SilverRunSummary",
    "load_existing_outputs",
]
