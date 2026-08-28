"""Silver 구성요소를 증분 cleaned-flat 파이프라인으로 조립한다."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from src.bronze.atlas_download import DEFAULT_BATCH_LIMIT, AtlasSettings
from src.logging_config import configure_pipeline_logging, get_pipeline_logger

from .csv_output import (
    FIRST_REJECT_STAGE,
    SECOND_REJECT_STAGE,
    CsvOutputTransaction,
    SilverRunSummary,
    load_existing_outputs,
)
from .deduplicator import FullRowDeduplicator
from .normalizer import (
    FlatNormalizer,
    NormalizationResult,
    SEOUL,
    Violation,
    extract_source_id,
)
from .modeling.materializer import materialize_normalized_outputs
from .preprocessor import BasicPreprocessor
from .rules import SilverRuleError, SilverRules, StandardField


LOGGER = get_pipeline_logger("silver")


class AtlasBatch(Protocol):
    """Bronze 증분 batch에서 Silver가 사용하는 최소 속성."""

    records: Sequence[Mapping[str, Any]]


class AtlasIncrementalSource(Protocol):
    """`AtlasIncrementalPipeline`과 맞닿는 최소 실행 계약."""

    def iter_batches(self, *, limit: int) -> Iterable[AtlasBatch]: ...

    def mark_processed(self, source_ids: Sequence[str]) -> None: ...

    def close(self) -> None: ...


PipelineFactory = Callable[..., AtlasIncrementalSource]


def write_clean_flat_data(
    batches: Iterable[Sequence[Mapping[str, Any]]],
    *,
    temp_dir: Path = Path("temp"),
    validation_now: datetime | None = None,
    rules: SilverRules | None = None,
) -> SilverRunSummary:
    """새 source만 전처리·1차 Reject·중복 2차 Reject 후 CSV에 반영한다."""
    active_rules = SilverRules.load_default() if rules is None else rules
    preprocessor = BasicPreprocessor()
    normalizer = FlatNormalizer(active_rules, validation_now=validation_now)
    existing = load_existing_outputs(
        temp_dir,
        output_fields=active_rules.output_fields,
    )
    deduplicator = FullRowDeduplicator(active_rules.output_fields)
    deduplicator.seed(existing.accept_rows)

    batch_count = 0
    input_count = 0
    accepted_count = 0
    first_rejected_count = 0
    duplicate_rejected_count = 0
    replayed_count = 0
    source_ids: list[str] = []
    observed_source_ids: set[str] = set()
    newly_routed_source_ids: set[str] = set()

    with CsvOutputTransaction(
        temp_dir,
        output_fields=active_rules.output_fields,
        existing=existing,
    ) as output:
        for records in batches:
            batch_count += 1
            for raw_record in records:
                input_count += 1
                source_id = extract_source_id(raw_record)
                if source_id is None:
                    raise ValueError("Atlas 원본의 MongoDB _id가 유효하지 않습니다.")
                if source_id not in observed_source_ids:
                    observed_source_ids.add(source_id)
                    source_ids.append(source_id)

                if (
                    source_id in existing.source_ids
                    or source_id in newly_routed_source_ids
                ):
                    replayed_count += 1
                    continue
                newly_routed_source_ids.add(source_id)

                preprocessed = preprocessor.preprocess(raw_record)
                result = normalizer.normalize(
                    preprocessed,
                    raw_record=raw_record,
                )
                if result.source_id != source_id:
                    raise RuntimeError("전처리 과정에서 source _id가 변경되었습니다.")

                if result.accepted is None:
                    output.write_reject(
                        source_id=source_id,
                        record_id=result.record_id,
                        reject_stage=FIRST_REJECT_STAGE,
                        violations=result.violations,
                        raw_json=result.raw_json,
                    )
                    first_rejected_count += 1
                    continue

                duplicate = deduplicator.check_and_add(
                    result.accepted,
                    source_id=source_id,
                )
                if duplicate is not None:
                    first_reference = duplicate.first_source_id or "기존 accept 행"
                    output.write_reject(
                        source_id=source_id,
                        record_id=result.record_id,
                        reject_stage=SECOND_REJECT_STAGE,
                        violations=(
                            Violation(
                                "DUPLICATE_NORMALIZED_ROW",
                                "*",
                                "lineage를 제외한 전체 표준 필드값이 "
                                f"{first_reference}와 중복됩니다.",
                            ),
                        ),
                        raw_json=result.raw_json,
                    )
                    duplicate_rejected_count += 1
                    continue

                if result.record_id is None:
                    raise RuntimeError("accept 행의 record_id가 없습니다.")
                output.write_accept(
                    source_id=source_id,
                    record_id=result.record_id,
                    values=result.accepted,
                )
                accepted_count += 1

        rejected_count = first_rejected_count + duplicate_rejected_count
        if input_count != accepted_count + rejected_count + replayed_count:
            raise RuntimeError("Silver 증분 행 수 accounting이 일치하지 않습니다.")
        if accepted_count + rejected_count > 0 or not existing.initialized:
            output.publish()

    return SilverRunSummary(
        batch_count=batch_count,
        input_count=input_count,
        accepted_count=accepted_count,
        rejected_count=rejected_count,
        first_rejected_count=first_rejected_count,
        duplicate_rejected_count=duplicate_rejected_count,
        replayed_count=replayed_count,
        accept_path=temp_dir / "accept.csv",
        reject_path=temp_dir / "reject.csv",
        source_ids=tuple(source_ids),
    )


def run_atlas_cleaning(
    settings: AtlasSettings,
    *,
    batch_size: int = DEFAULT_BATCH_LIMIT,
    temp_dir: Path = Path("temp"),
    validation_now: datetime | None = None,
    processed_ids_path: Path | None = None,
    pipeline_factory: PipelineFactory | None = None,
) -> SilverRunSummary:
    """Bronze 미처리 batch를 순회하고 CSV 성공 후 source ID를 확정한다."""
    active_factory = (
        _default_pipeline_factory if pipeline_factory is None else pipeline_factory
    )
    checkpoint_path = (
        temp_dir / "processed_ids.json"
        if processed_ids_path is None
        else processed_ids_path
    )
    _validate_checkpoint_output_presence(
        checkpoint_path=checkpoint_path,
        temp_dir=temp_dir,
    )
    pipeline = active_factory(settings, processed_ids_path=checkpoint_path)
    try:
        batches = (batch.records for batch in pipeline.iter_batches(limit=batch_size))
        summary = write_clean_flat_data(
            batches,
            temp_dir=temp_dir,
            validation_now=validation_now,
        )
        normalization = materialize_normalized_outputs(temp_dir)
        summary = replace(
            summary,
            normalization_input_source_count=normalization.input_source_count,
            normalization_accepted_source_count=normalization.accepted_source_count,
            normalization_rejected_source_count=normalization.rejected_source_count,
            normalization_reject_row_count=normalization.reject_row_count,
            model_row_counts=normalization.model_row_counts,
            normalization_reject_path=normalization.normalization_reject_path,
            model_paths=normalization.model_paths,
            orphan_counts=normalization.orphan_counts,
        )
        if summary.source_ids:
            pipeline.mark_processed(summary.source_ids)
        return summary
    finally:
        pipeline.close()


def _validate_checkpoint_output_presence(
    *,
    checkpoint_path: Path,
    temp_dir: Path,
) -> None:
    """처리 상태만 남고 CSV pair가 사라진 복구 불가능 상태를 거부한다."""
    if not checkpoint_path.exists():
        return
    accept_exists = (temp_dir / "accept.csv").exists()
    reject_exists = (temp_dir / "reject.csv").exists()
    if not accept_exists and not reject_exists:
        raise RuntimeError(
            "processed IDs checkpoint는 있지만 accept/reject CSV pair가 없습니다."
        )


def _default_pipeline_factory(
    settings: AtlasSettings,
    *,
    processed_ids_path: Path,
) -> AtlasIncrementalSource:
    """실행 시점에 고정 Bronze 증분 구현을 불러온다."""
    from src.bronze.atlas_pipeline import AtlasIncrementalPipeline

    return AtlasIncrementalPipeline(
        settings,
        processed_ids_path=processed_ids_path,
    )


def _positive_integer(value: str) -> int:
    """CLI의 양의 정수 값을 검증한다."""
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("1 이상의 정수가 필요합니다.") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("1 이상의 정수가 필요합니다.")
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Silver 증분 실행 CLI 인자를 해석한다."""
    parser = argparse.ArgumentParser(
        description="Atlas 미처리 원본을 n건씩 읽어 Silver CSV에 증분 반영"
    )
    parser.add_argument(
        "--batch-size",
        type=_positive_integer,
        default=DEFAULT_BATCH_LIMIT,
    )
    parser.add_argument("--temp-dir", type=Path, default=Path("temp"))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """환경변수의 Atlas를 읽어 Silver 증분 파이프라인을 실행한다."""
    args = parse_args(argv)
    configure_pipeline_logging()
    summary = run_atlas_cleaning(
        AtlasSettings.from_environment(),
        batch_size=args.batch_size,
        temp_dir=args.temp_dir,
    )
    LOGGER.info(
        "batches=%s input=%s accepted=%s rejected=%s "
        "first_rejected=%s duplicate_rejected=%s replayed=%s",
        summary.batch_count,
        summary.input_count,
        summary.accepted_count,
        summary.rejected_count,
        summary.first_rejected_count,
        summary.duplicate_rejected_count,
        summary.replayed_count,
    )
    model_counts = ",".join(
        f"{model_name}:{count}" for model_name, count in summary.model_row_counts
    )
    orphan_counts = ",".join(
        f"{reference}:{count}" for reference, count in summary.orphan_counts
    )
    LOGGER.info(
        "normalization_input_sources=%s normalization_accepted_sources=%s "
        "normalization_rejected_sources=%s normalization_reject_rows=%s "
        "model_rows=%s orphan_counts=%s",
        summary.normalization_input_source_count,
        summary.normalization_accepted_source_count,
        summary.normalization_rejected_source_count,
        summary.normalization_reject_row_count,
        model_counts,
        orphan_counts,
    )
    LOGGER.info(
        "accept=%s reject=%s",
        summary.accept_path,
        summary.reject_path,
    )
    return 0


__all__ = [
    "BasicPreprocessor",
    "FlatNormalizer",
    "FullRowDeduplicator",
    "NormalizationResult",
    "SEOUL",
    "SilverRuleError",
    "SilverRules",
    "SilverRunSummary",
    "StandardField",
    "Violation",
    "main",
    "parse_args",
    "run_atlas_cleaning",
    "write_clean_flat_data",
]


if __name__ == "__main__":
    raise SystemExit(main())
