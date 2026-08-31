"""통합 파이프라인 로그를 터미널과 회전 파일에 함께 기록한다."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TextIO


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_DIR = PROJECT_ROOT / "output" / "logs"
DEFAULT_LOG_PATH = DEFAULT_LOG_DIR / "pipeline.log"
LOGGER_NAMESPACE = "mlo.pipeline"
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 5

_CONFIGURATION_ATTRIBUTE = "_mlo_pipeline_configuration"
_OWNED_HANDLER_ATTRIBUTE = "_mlo_pipeline_owned_handler"


class _MaximumLevelFilter(logging.Filter):
    """지정 level 이하의 레코드만 handler에 전달한다."""

    def __init__(self, maximum_level: int) -> None:
        super().__init__()
        self.maximum_level = maximum_level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno <= self.maximum_level


def get_pipeline_logger(component: str) -> logging.Logger:
    """통합 파이프라인 namespace 아래의 component logger를 반환한다."""
    normalized_component = component.strip(".")
    if not normalized_component:
        return logging.getLogger(LOGGER_NAMESPACE)
    return logging.getLogger(f"{LOGGER_NAMESPACE}.{normalized_component}")


def _close_owned_handlers(logger: logging.Logger) -> None:
    """이 모듈이 추가한 handler만 logger에서 제거하고 닫는다."""
    for handler in tuple(logger.handlers):
        if not getattr(handler, _OWNED_HANDLER_ATTRIBUTE, False):
            continue
        logger.removeHandler(handler)
        handler.close()


def _mark_owned(handler: logging.Handler) -> logging.Handler:
    setattr(handler, _OWNED_HANDLER_ATTRIBUTE, True)
    return handler


def configure_pipeline_logging(
    *,
    log_path: Path | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> logging.Logger:
    """INFO는 stdout, WARNING 이상은 stderr와 파일에 중복 없이 기록한다."""
    resolved_log_path = (
        DEFAULT_LOG_PATH if log_path is None else Path(log_path)
    ).resolve()
    active_stdout = sys.stdout if stdout is None else stdout
    active_stderr = sys.stderr if stderr is None else stderr
    configuration = (
        resolved_log_path,
        id(active_stdout),
        id(active_stderr),
        max_bytes,
        backup_count,
    )

    logger = logging.getLogger(LOGGER_NAMESPACE)
    if getattr(logger, _CONFIGURATION_ATTRIBUTE, None) == configuration:
        return logger

    resolved_log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stdout_handler = _mark_owned(logging.StreamHandler(active_stdout))
    stdout_handler.setLevel(logging.INFO)
    stdout_handler.addFilter(_MaximumLevelFilter(logging.INFO))
    stdout_handler.setFormatter(formatter)

    stderr_handler = _mark_owned(logging.StreamHandler(active_stderr))
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(formatter)

    file_handler = _mark_owned(
        RotatingFileHandler(
            resolved_log_path,
            mode="a",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    _close_owned_handlers(logger)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(stdout_handler)
    logger.addHandler(stderr_handler)
    logger.addHandler(file_handler)
    setattr(logger, _CONFIGURATION_ATTRIBUTE, configuration)
    return logger


__all__ = [
    "DEFAULT_BACKUP_COUNT",
    "DEFAULT_LOG_DIR",
    "DEFAULT_LOG_PATH",
    "DEFAULT_MAX_BYTES",
    "LOGGER_NAMESPACE",
    "PROJECT_ROOT",
    "configure_pipeline_logging",
    "get_pipeline_logger",
]
