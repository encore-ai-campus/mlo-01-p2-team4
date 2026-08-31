"""정규화 Reject와 네 개 Silver 모델 snapshot을 함께 publish한다."""

from __future__ import annotations

import csv
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from .contracts import (
    MODEL_KEY_MISSING,
    MODEL_SPECS,
    NORMALIZATION_REJECT_FIELDS,
    NORMALIZATION_REJECT_STAGE,
    ModelSpec,
    NormalizationContractError,
    NormalizationProjection,
)


def publish_normalization_outputs(
    temp_dir: Path,
    projection: NormalizationProjection,
) -> None:
    """정규화 결과 다섯 파일을 staging에 완성한 뒤 함께 게시한다."""
    _validate_projection(projection)

    temp_dir.mkdir(parents=True, exist_ok=True)
    models_dir = temp_dir / "models"
    models_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        prefix=".silver-normalization-",
        dir=temp_dir,
    ) as temporary:
        staging_dir = Path(temporary)
        staged_models_dir = staging_dir / "models"
        staged_models_dir.mkdir()

        staged_reject = staging_dir / "normalization_reject.csv"
        _write_csv(
            staged_reject,
            fieldnames=NORMALIZATION_REJECT_FIELDS,
            rows=tuple(reject.as_row() for reject in projection.rejects),
        )

        publication: list[tuple[Path, Path]] = [
            (staged_reject, temp_dir / "normalization_reject.csv")
        ]
        for spec in MODEL_SPECS.values():
            staged_model = staged_models_dir / spec.filename
            _write_csv(
                staged_model,
                fieldnames=spec.fields,
                rows=projection.model_rows[spec.name],
            )
            publication.append((staged_model, models_dir / spec.filename))

        _publish_group(publication, backup_dir=staging_dir / "backups")


def _validate_projection(projection: NormalizationProjection) -> None:
    source_counts = (
        projection.input_source_count,
        projection.accepted_source_count,
        projection.rejected_source_count,
    )
    if any(count < 0 for count in source_counts) or (
        projection.input_source_count
        != projection.accepted_source_count + projection.rejected_source_count
    ):
        raise NormalizationContractError(
            "정규화 source accounting이 일치하지 않습니다."
        )

    if tuple(projection.model_rows) != tuple(MODEL_SPECS):
        raise NormalizationContractError("네 개 모델 출력 순서가 고정 계약과 다릅니다.")

    for spec in MODEL_SPECS.values():
        rows = projection.model_rows[spec.name]
        serialized_keys: list[tuple[str, ...]] = []
        for row in rows:
            if tuple(row) != spec.fields:
                raise NormalizationContractError(
                    f"{spec.name} 컬럼 순서가 고정 계약과 다릅니다."
                )
            serialized_keys.append(_model_key(spec, row))
        if len(serialized_keys) != len(set(serialized_keys)):
            raise NormalizationContractError(f"{spec.name} 모델 key가 중복되었습니다.")
        if serialized_keys != sorted(serialized_keys):
            raise NormalizationContractError(
                f"{spec.name} 모델 행이 key 순서로 정렬되지 않았습니다."
            )

    reject_identities: set[tuple[str, str, str, str]] = set()
    rejected_source_ids: set[str] = set()
    for reject in projection.rejects:
        row = reject.as_row()
        if tuple(row) != NORMALIZATION_REJECT_FIELDS:
            raise NormalizationContractError(
                "정규화 Reject 컬럼 순서가 고정 계약과 다릅니다."
            )
        if row["reject_stage"] != NORMALIZATION_REJECT_STAGE:
            raise NormalizationContractError(
                "정규화 Reject stage가 고정 계약과 다릅니다."
            )
        if not reject.source_id.strip():
            raise NormalizationContractError("정규화 Reject source_id가 비었습니다.")
        spec = MODEL_SPECS.get(reject.model_name)
        if spec is None:
            raise NormalizationContractError(
                "정규화 Reject model_name이 고정 계약과 다릅니다."
            )
        if reject.reason_code not in {
            MODEL_KEY_MISSING,
            spec.conflict_reason_code,
        }:
            raise NormalizationContractError(
                "정규화 Reject reason_code가 고정 계약과 다릅니다."
            )
        identity = (
            reject.source_id,
            reject.model_name,
            reject.model_key,
            reject.reason_code,
        )
        if identity in reject_identities:
            raise NormalizationContractError("정규화 Reject identity가 중복되었습니다.")
        reject_identities.add(identity)
        rejected_source_ids.add(reject.source_id)

    if projection.rejected_source_count != len(rejected_source_ids):
        raise NormalizationContractError(
            "정규화 rejected source accounting이 일치하지 않습니다."
        )


def _model_key(
    spec: ModelSpec,
    row: Mapping[str, object],
) -> tuple[str, ...]:
    values: list[str] = []
    for field in spec.key_fields:
        value = row[field]
        if value is None or (type(value) is str and not value.strip()):
            raise NormalizationContractError(
                f"{spec.name} 모델 key가 비었습니다: field={field}"
            )
        values.append(str(value))
    return tuple(values)


def _write_csv(
    path: Path,
    *,
    fieldnames: Sequence[str],
    rows: Sequence[Mapping[str, object]],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
            extrasaction="raise",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _publish_group(
    publication: Sequence[tuple[Path, Path]],
    *,
    backup_dir: Path,
) -> None:
    backup_dir.mkdir(parents=True)
    previous: dict[Path, Path | None] = {}
    for index, (_, target) in enumerate(publication):
        if not target.exists():
            previous[target] = None
            continue
        backup = backup_dir / f"{index:02d}-{target.name}"
        shutil.copy2(target, backup)
        previous[target] = backup

    try:
        for staged, target in publication:
            os.replace(staged, target)
    except BaseException as publication_error:
        restore_errors = _restore_previous_outputs(previous)
        if restore_errors:
            details = "; ".join(restore_errors)
            raise RuntimeError(
                "정규화 출력 게시와 이전 출력 복원에 실패했습니다: " + details
            ) from publication_error
        raise


def _restore_previous_outputs(previous: Mapping[Path, Path | None]) -> list[str]:
    errors: list[str] = []
    for target, backup in previous.items():
        try:
            if backup is None:
                target.unlink(missing_ok=True)
            else:
                shutil.copy2(backup, target)
        except OSError as error:
            errors.append(f"{target}: {error}")
    return errors


__all__ = ["publish_normalization_outputs"]
