"""누적 Flat accept CSV에서 네 모델 snapshot과 정규화 Reject를 게시한다."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from .contracts import (
    FLAT_INPUT_FIELDS,
    MODEL_SPECS,
    NormalizationContractError,
)
from .model_output import publish_normalization_outputs
from .projections import build_normalization_projection


@dataclass(frozen=True, slots=True)
class NormalizationRunSummary:
    """누적 Flat 전체를 기준으로 게시한 정규화 결과 요약."""

    input_source_count: int
    accepted_source_count: int
    rejected_source_count: int
    reject_row_count: int
    model_row_counts: tuple[tuple[str, int], ...]
    normalization_reject_path: Path
    model_paths: tuple[tuple[str, Path], ...]
    orphan_counts: tuple[tuple[str, int], ...]


def materialize_normalized_outputs(temp_dir: Path) -> NormalizationRunSummary:
    """게시 완료된 누적 ``accept.csv`` 전체에서 정규화 출력을 재생성한다."""
    resolved_temp_dir = Path(temp_dir)
    flat_rows = _read_flat_accept_rows(resolved_temp_dir / "accept.csv")
    projection = build_normalization_projection(flat_rows)
    publish_normalization_outputs(resolved_temp_dir, projection)

    return NormalizationRunSummary(
        input_source_count=projection.input_source_count,
        accepted_source_count=projection.accepted_source_count,
        rejected_source_count=projection.rejected_source_count,
        reject_row_count=len(projection.rejects),
        model_row_counts=tuple(
            (spec.name, len(projection.model_rows[spec.name]))
            for spec in MODEL_SPECS.values()
        ),
        normalization_reject_path=resolved_temp_dir / "normalization_reject.csv",
        model_paths=tuple(
            (spec.name, resolved_temp_dir / "models" / spec.filename)
            for spec in MODEL_SPECS.values()
        ),
        orphan_counts=tuple(projection.orphan_counts.items()),
    )


def _read_flat_accept_rows(path: Path) -> tuple[dict[str, str], ...]:
    """고정 Flat 계약을 만족하는 누적 accept 행을 읽는다."""
    if not path.exists():
        raise NormalizationContractError(f"누적 Flat accept.csv가 없습니다: {path}")

    with path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        actual_fields = tuple(reader.fieldnames or ())
        if actual_fields != FLAT_INPUT_FIELDS:
            raise NormalizationContractError(
                "누적 accept.csv header가 고정 Flat 계약과 다릅니다."
            )

        rows: list[dict[str, str]] = []
        for row_number, row in enumerate(reader, start=2):
            if None in row or any(
                row.get(field) is None for field in FLAT_INPUT_FIELDS
            ):
                raise NormalizationContractError(
                    f"누적 accept.csv 컬럼 수가 올바르지 않습니다: row={row_number}"
                )
            normalized = {field: row[field] for field in FLAT_INPUT_FIELDS}
            if not normalized["source_id"].strip():
                raise NormalizationContractError(
                    f"누적 accept.csv source_id가 비어 있습니다: row={row_number}"
                )
            if not normalized["record_id"].strip():
                raise NormalizationContractError(
                    f"누적 accept.csv record_id가 비어 있습니다: row={row_number}"
                )
            rows.append(normalized)
    return tuple(rows)


__all__ = ["NormalizationRunSummary", "materialize_normalized_outputs"]
