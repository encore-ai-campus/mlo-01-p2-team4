"""Silver 모델 CSV snapshot을 검증된 MySQL 테이블에 적재한다."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.logging_config import configure_pipeline_logging, get_pipeline_logger

from .modeling.contracts import MODEL_SPECS
from .mysql_csv_reader import MySQLModelSnapshot, read_mysql_model_snapshot
from .mysql_schema import (
    TABLE_SCHEMAS,
    create_mysql_tables,
    ensure_mysql_foreign_keys,
    validate_mysql_schema,
)
from .mysql_settings import MySQLSettings, from_environment


DEFAULT_CHUNK_SIZE = 1_000
DEFAULT_MODELS_DIR = Path("temp/models")
LOGGER = get_pipeline_logger("mysql")

_INSERT_ORDER = (
    "silver_employee",
    "silver_area",
    "silver_parent_area",
    "silver_area_join_reference",
)
_DELETE_ORDER = (
    "silver_area_join_reference",
    "silver_parent_area",
    "silver_area",
    "silver_employee",
)

ConnectionFactory = Callable[..., Any]


class MySQLLoadError(RuntimeError):
    """MySQL 스키마 초기화 또는 모델 적재를 안전하게 완료하지 못한 경우."""


@dataclass(frozen=True, slots=True)
class MySQLLoadSummary:
    """커밋 전에 검증한 모델 행 수와 child-first DELETE 결과."""

    model_row_counts: tuple[tuple[str, int], ...]
    deleted_row_counts: tuple[tuple[str, int], ...]


def load_models_to_mysql(
    settings: MySQLSettings,
    snapshot: MySQLModelSnapshot,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    connection_factory: ConnectionFactory | None = None,
) -> MySQLLoadSummary:
    """네 모델을 한 트랜잭션에서 교체하고 DB 행 수를 검증한다.

    기존 스키마가 고정 계약과 일치하는지 먼저 확인한다. 이후 자식 테이블부터
    비우고 부모 테이블부터 적재하며, 네 테이블의 행 수가 입력 snapshot과 모두
    일치할 때만 commit한다.
    """
    if (
        not isinstance(chunk_size, int)
        or isinstance(chunk_size, bool)
        or chunk_size < 1
    ):
        raise ValueError("chunk_size는 1 이상의 정수여야 합니다.")
    _validate_snapshot(snapshot)

    connection: Any | None = None
    cursor: Any | None = None
    try:
        connection = _open_connection(settings, connection_factory)
        connection.start_transaction()
        validate_mysql_schema(connection, settings.database)
        cursor = connection.cursor()

        deleted_counts: list[tuple[str, int]] = []
        for table_name in _DELETE_ORDER:
            cursor.execute(f"DELETE FROM `{table_name}`")
            deleted_counts.append((table_name, _deleted_row_count(cursor, table_name)))

        for table_name in _INSERT_ORDER:
            statement = _insert_statement(table_name)
            for chunk in _iter_chunks(snapshot.rows[table_name], chunk_size):
                cursor.executemany(statement, chunk)

        verified_counts: list[tuple[str, int]] = []
        for table_name in _INSERT_ORDER:
            cursor.execute(f"SELECT COUNT(*) FROM `{table_name}`")
            actual_count = _read_count(cursor, table_name)
            expected_count = snapshot.row_counts[table_name]
            if actual_count != expected_count:
                raise MySQLLoadError(
                    f"{table_name} 행 수 검증에 실패했습니다: "
                    f"expected={expected_count}, actual={actual_count}"
                )
            verified_counts.append((table_name, actual_count))

        connection.commit()
        return MySQLLoadSummary(
            model_row_counts=tuple(verified_counts),
            deleted_row_counts=tuple(deleted_counts),
        )
    except Exception as error:
        _rollback_quietly(connection)
        if isinstance(error, MySQLLoadError):
            raise
        raise MySQLLoadError("MySQL 모델 적재에 실패했습니다.") from None
    finally:
        _close_quietly(cursor)
        _close_quietly(connection)


def _initialize_mysql_schema(
    settings: MySQLSettings,
    connection_factory: ConnectionFactory | None = None,
) -> None:
    """허용된 네 테이블을 생성하고 고정 스키마를 검증한 뒤 commit한다."""
    connection: Any | None = None
    try:
        connection = _open_connection(settings, connection_factory)
        create_mysql_tables(connection)
        ensure_mysql_foreign_keys(connection, settings.database)
        validate_mysql_schema(connection, settings.database)
        connection.commit()
    except Exception as error:
        _rollback_quietly(connection)
        raise MySQLLoadError("MySQL 스키마 초기화에 실패했습니다.") from error
    finally:
        _close_quietly(connection)


def _open_connection(
    settings: MySQLSettings,
    connection_factory: ConnectionFactory | None,
) -> Any:
    """비밀 값을 출력하지 않고 autocommit이 꺼진 connection을 연다."""
    factory = connection_factory or _default_connection_factory
    connection_kwargs = dict(settings.connection_kwargs())
    connection_kwargs["autocommit"] = False
    return factory(**connection_kwargs)


def _default_connection_factory(**connection_kwargs: object) -> Any:
    """실제 적재를 요청한 시점에만 선택 의존성을 불러온다."""
    try:
        import mysql.connector
    except ImportError:
        raise MySQLLoadError("MySQL 연결 드라이버가 설치되어 있지 않습니다.") from None
    return mysql.connector.connect(**connection_kwargs)


def _validate_snapshot(
    snapshot: MySQLModelSnapshot,
) -> None:
    """DB 연결 전에 네 모델의 고정 CSV 계약만 확인한다."""
    required_names = set(_INSERT_ORDER)
    if set(MODEL_SPECS) != required_names or set(TABLE_SCHEMAS) != required_names:
        raise MySQLLoadError("MySQL 테이블 계약이 네 Silver 모델과 다릅니다.")
    if (
        set(snapshot.rows) != required_names
        or set(snapshot.columns) != required_names
        or set(snapshot.row_counts) != required_names
    ):
        raise MySQLLoadError("MySQL 입력 snapshot의 모델 구성이 올바르지 않습니다.")

    for table_name in _INSERT_ORDER:
        expected_columns = MODEL_SPECS[table_name].fields
        if TABLE_SCHEMAS[table_name].column_names != expected_columns:
            raise MySQLLoadError(
                f"{table_name} CSV와 MySQL 스키마 컬럼 계약이 다릅니다."
            )
        if tuple(snapshot.columns[table_name]) != expected_columns:
            raise MySQLLoadError(f"{table_name} 입력 컬럼이 고정 계약과 다릅니다.")
        rows = snapshot.rows[table_name]
        expected_count = snapshot.row_counts[table_name]
        if (
            not isinstance(expected_count, int)
            or isinstance(expected_count, bool)
            or expected_count < 0
            or expected_count != len(rows)
        ):
            raise MySQLLoadError(f"{table_name} 입력 행 수 계약이 올바르지 않습니다.")
        if any(len(row) != len(expected_columns) for row in rows):
            raise MySQLLoadError(
                f"{table_name} 입력 행의 컬럼 수가 고정 계약과 다릅니다."
            )


def _insert_statement(table_name: str) -> str:
    """MODEL_SPECS 컬럼 순서로 고정 parameterized INSERT를 만든다."""
    columns = MODEL_SPECS[table_name].fields
    quoted_columns = ", ".join(f"`{column}`" for column in columns)
    placeholders = ", ".join("%s" for _ in columns)
    return f"INSERT INTO `{table_name}` ({quoted_columns}) VALUES ({placeholders})"


def _iter_chunks(
    rows: tuple[tuple[object, ...], ...],
    chunk_size: int,
) -> Iterator[tuple[tuple[object, ...], ...]]:
    """입력 순서를 유지하며 executemany용 고정 크기 chunk를 반환한다."""
    for start in range(0, len(rows), chunk_size):
        yield rows[start : start + chunk_size]


def _deleted_row_count(cursor: Any, table_name: str) -> int:
    """DELETE 결과 rowcount를 안전한 정수로 제한한다."""
    row_count = getattr(cursor, "rowcount", None)
    if not isinstance(row_count, int) or isinstance(row_count, bool) or row_count < 0:
        raise MySQLLoadError(f"{table_name} DELETE 행 수를 확인할 수 없습니다.")
    return row_count


def _read_count(cursor: Any, table_name: str) -> int:
    """SELECT COUNT(*)의 단일 비음수 정수 결과를 읽는다."""
    result = cursor.fetchone()
    if (
        not isinstance(result, (tuple, list))
        or len(result) != 1
        or not isinstance(result[0], int)
        or isinstance(result[0], bool)
        or result[0] < 0
    ):
        raise MySQLLoadError(f"{table_name} 행 수 결과가 올바르지 않습니다.")
    return result[0]


def _rollback_quietly(connection: Any | None) -> None:
    """원래 오류와 비밀 없는 오류 메시지를 보존하며 rollback을 시도한다."""
    if connection is None:
        return
    try:
        connection.rollback()
    except Exception:
        pass


def _close_quietly(resource: Any | None) -> None:
    """cursor 또는 connection의 close 실패가 본래 결과를 덮지 않게 한다."""
    if resource is None:
        return
    try:
        resource.close()
    except Exception:
        pass


def _positive_integer(value: str) -> int:
    """CLI의 양의 정수 값을 검증한다."""
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("1 이상의 정수가 필요합니다.") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("1 이상의 정수가 필요합니다.")
    return parsed


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Silver 모델 CSV snapshot을 MySQL에 검증 적재"
    )
    parser.add_argument(
        "--models-dir",
        type=Path,
        default=DEFAULT_MODELS_DIR,
    )
    parser.add_argument(
        "--chunk-size",
        type=_positive_integer,
        default=DEFAULT_CHUNK_SIZE,
    )
    parser.add_argument(
        "--init-schema",
        action="store_true",
        help="허용된 네 테이블을 생성하고 스키마를 검증합니다.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="CSV snapshot으로 기존 네 테이블 데이터를 교체합니다.",
    )
    return parser.parse_args(argv)


def _format_counts(counts: Sequence[tuple[str, int]]) -> str:
    return ",".join(f"{model_name}:{count}" for model_name, count in counts)


def _snapshot_counts(
    snapshot: MySQLModelSnapshot,
) -> tuple[tuple[str, int], ...]:
    return tuple(
        (table_name, snapshot.row_counts[table_name]) for table_name in _INSERT_ORDER
    )


def main(argv: Sequence[str] | None = None) -> int:
    """기본은 dry-run이며 명시한 경우에만 스키마 또는 데이터를 변경한다."""
    args = _parse_args(argv)
    configure_pipeline_logging()
    settings = from_environment()

    if not args.init_schema and not args.apply:
        snapshot = read_mysql_model_snapshot(args.models_dir)
        _validate_snapshot(snapshot)
        LOGGER.info(
            "mode=dry-run model_rows=%s",
            _format_counts(_snapshot_counts(snapshot)),
        )
        return 0

    if args.init_schema:
        _initialize_mysql_schema(settings)
        LOGGER.info("schema_initialized=true")

    if args.apply:
        snapshot = read_mysql_model_snapshot(args.models_dir)
        summary = load_models_to_mysql(
            settings,
            snapshot,
            chunk_size=args.chunk_size,
        )
        LOGGER.info(
            "mode=apply model_rows=%s deleted_rows=%s",
            _format_counts(summary.model_row_counts),
            _format_counts(summary.deleted_row_counts),
        )
    return 0


__all__ = [
    "MySQLLoadError",
    "MySQLLoadSummary",
    "load_models_to_mysql",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
