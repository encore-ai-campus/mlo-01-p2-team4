"""Atlas 원본부터 Silver 모델 생성과 MySQL 적재까지 순서대로 실행한다."""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path


if __package__ in {None, ""}:
    repository_root = str(Path(__file__).resolve().parents[1])
    if repository_root not in sys.path:
        sys.path.insert(0, repository_root)

from src.bronze.environment import load_dotenv  # noqa: E402
from src.logging_config import (  # noqa: E402
    configure_pipeline_logging,
    get_pipeline_logger,
)
from src.silver import flat_pipeline, mysql_loader  # noqa: E402


DEFAULT_BATCH_SIZE = 1_000
DEFAULT_CHUNK_SIZE = 1_000
DEFAULT_INTERVAL_SECONDS = 30
DEFAULT_TEMP_DIR = Path("temp")
INTERVAL_SECONDS_ENV = "PIPELINE_INTERVAL_SECONDS"
LOGGER = get_pipeline_logger("main")


def _positive_integer(value: str) -> int:
    """CLI의 양의 정수 값을 검증한다."""
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("1 이상의 정수가 필요합니다.") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("1 이상의 정수가 필요합니다.")
    return parsed


def _interval_seconds_default() -> str:
    """프로세스 환경과 ``src/.env``에서 기본 반복 주기를 읽는다."""
    load_dotenv()
    return os.environ.get(INTERVAL_SECONDS_ENV, str(DEFAULT_INTERVAL_SECONDS))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Atlas→Silver→MySQL 통합 실행 인자를 해석한다."""
    parser = argparse.ArgumentParser(
        description="Atlas 원본을 Silver 모델로 변환한 뒤 MySQL 적재기를 실행"
    )
    parser.add_argument(
        "--batch-size",
        type=_positive_integer,
        default=DEFAULT_BATCH_SIZE,
    )
    parser.add_argument(
        "--temp-dir",
        type=Path,
        default=DEFAULT_TEMP_DIR,
    )
    parser.add_argument(
        "--chunk-size",
        type=_positive_integer,
        default=DEFAULT_CHUNK_SIZE,
    )
    parser.add_argument(
        "--interval-seconds",
        type=_positive_integer,
        default=_interval_seconds_default(),
        help=(
            "반복 실행 간격(초)입니다. CLI, PIPELINE_INTERVAL_SECONDS, "
            "30초 순서로 결정합니다."
        ),
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="파이프라인을 한 번만 실행합니다.",
    )
    parser.add_argument(
        "--init-schema",
        action="store_true",
        help="허용된 네 MySQL 테이블을 생성하고 스키마를 검증합니다.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Silver 모델 CSV로 기존 네 MySQL 테이블 데이터를 교체합니다.",
    )
    return parser.parse_args(argv)


def _completion_mode(*, init_schema: bool, apply: bool) -> str:
    """명시적으로 실행한 MySQL 동작을 완료 메시지용 mode로 반환한다."""
    if init_schema and apply:
        return "init-schema+apply"
    if init_schema:
        return "init-schema"
    if apply:
        return "apply"
    return "dry-run"


def run_once(
    args: argparse.Namespace,
    *,
    init_schema: bool | None = None,
) -> int:
    """Silver와 MySQL 적재기를 한 cycle 실행한다."""
    configure_pipeline_logging()
    should_init_schema = args.init_schema if init_schema is None else init_schema
    LOGGER.info(
        "pipeline_cycle_started=true batch_size=%s chunk_size=%s "
        "temp_dir=%s init_schema=%s apply=%s",
        args.batch_size,
        args.chunk_size,
        args.temp_dir,
        str(should_init_schema).lower(),
        str(args.apply).lower(),
    )
    silver_exit_code = flat_pipeline.main(
        [
            "--batch-size",
            str(args.batch_size),
            "--temp-dir",
            str(args.temp_dir),
        ]
    )
    if silver_exit_code != 0:
        LOGGER.error(
            "pipeline_stage_failed=true stage=silver exit_code=%s",
            silver_exit_code,
        )
        return silver_exit_code

    loader_argv = [
        "--models-dir",
        str(args.temp_dir / "models"),
        "--chunk-size",
        str(args.chunk_size),
    ]
    if should_init_schema:
        loader_argv.append("--init-schema")
    if args.apply:
        loader_argv.append("--apply")

    loader_exit_code = mysql_loader.main(loader_argv)
    if loader_exit_code != 0:
        LOGGER.error(
            "pipeline_stage_failed=true stage=mysql exit_code=%s",
            loader_exit_code,
        )
        return loader_exit_code

    mode = _completion_mode(init_schema=should_init_schema, apply=args.apply)
    LOGGER.info(
        "pipeline_completed=true mode=%s temp_dir=%s",
        mode,
        args.temp_dir,
    )
    return 0


def run_loop(
    args: argparse.Namespace,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """cycle이 겹치지 않도록 시작 시각 기준 간격으로 반복 실행한다."""
    first_cycle = True
    while True:
        cycle_started_at = monotonic()
        exit_code = run_once(
            args,
            init_schema=args.init_schema if first_cycle else False,
        )
        if exit_code != 0:
            return exit_code

        first_cycle = False
        elapsed_seconds = monotonic() - cycle_started_at
        sleep(max(0.0, args.interval_seconds - elapsed_seconds))


def main(argv: Sequence[str] | None = None) -> int:
    """한 번 또는 반복해서 Atlas→Silver→MySQL 파이프라인을 실행한다."""
    try:
        args = parse_args(argv)
        configure_pipeline_logging()
        if args.once:
            return run_once(args)
        return run_loop(args)
    except KeyboardInterrupt:
        LOGGER.warning("pipeline_interrupted=true")
        return 130


def _run_cli(argv: Sequence[str] | None = None) -> int:
    """처리되지 않은 CLI 예외를 기록하고 종료 코드 1로 변환한다."""
    try:
        return main(argv)
    except Exception:
        configure_pipeline_logging()
        LOGGER.exception("pipeline_failed=true")
        return 1


__all__ = ["main", "parse_args", "run_loop", "run_once"]


if __name__ == "__main__":
    raise SystemExit(_run_cli())
