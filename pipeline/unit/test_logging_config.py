"""통합 파이프라인의 터미널·회전 파일 로깅 계약을 검증한다."""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Iterator

import pytest

from src.logging_config import (
    DEFAULT_LOG_PATH,
    LOGGER_NAMESPACE,
    PROJECT_ROOT,
    configure_pipeline_logging,
    get_pipeline_logger,
)


@pytest.fixture(autouse=True)
def close_pipeline_handlers() -> Iterator[None]:
    """각 테스트가 독립된 stream과 파일 handler를 사용하도록 정리한다."""
    yield
    logger = logging.getLogger(LOGGER_NAMESPACE)
    for handler in tuple(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    if hasattr(logger, "_mlo_pipeline_configuration"):
        delattr(logger, "_mlo_pipeline_configuration")


def test_default_log_path_is_repository_output_logs() -> None:
    assert DEFAULT_LOG_PATH == PROJECT_ROOT / "output" / "logs" / "pipeline.log"


def test_logging_mirrors_info_and_warning_to_expected_streams_and_file(
    tmp_path: Path,
) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    log_path = tmp_path / "output" / "logs" / "pipeline.log"
    configure_pipeline_logging(
        log_path=log_path,
        stdout=stdout,
        stderr=stderr,
    )
    logger = get_pipeline_logger("test")

    logger.info("처리완료=true")
    logger.warning("중단=true")

    assert "처리완료=true" in stdout.getvalue()
    assert "중단=true" not in stdout.getvalue()
    assert "처리완료=true" not in stderr.getvalue()
    assert "중단=true" in stderr.getvalue()
    file_text = log_path.read_text(encoding="utf-8")
    assert "INFO mlo.pipeline.test 처리완료=true" in file_text
    assert "WARNING mlo.pipeline.test 중단=true" in file_text


def test_repeated_configuration_does_not_duplicate_handlers_or_messages(
    tmp_path: Path,
) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    log_path = tmp_path / "output" / "logs" / "pipeline.log"

    first = configure_pipeline_logging(
        log_path=log_path,
        stdout=stdout,
        stderr=stderr,
    )
    second = configure_pipeline_logging(
        log_path=log_path,
        stdout=stdout,
        stderr=stderr,
    )
    get_pipeline_logger("test").info("single_event=true")

    assert first is second
    assert len(first.handlers) == 3
    assert stdout.getvalue().count("single_event=true") == 1
    assert log_path.read_text(encoding="utf-8").count("single_event=true") == 1


def test_file_handler_rotates_and_keeps_bounded_backups(tmp_path: Path) -> None:
    log_path = tmp_path / "output" / "logs" / "pipeline.log"
    configure_pipeline_logging(
        log_path=log_path,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        max_bytes=180,
        backup_count=2,
    )
    logger = get_pipeline_logger("test")

    for sequence in range(20):
        logger.info("sequence=%s payload=%s", sequence, "x" * 80)

    log_files = tuple(log_path.parent.glob("pipeline.log*"))
    assert log_path.exists()
    assert log_path.with_name("pipeline.log.1").exists()
    assert len(log_files) <= 3
