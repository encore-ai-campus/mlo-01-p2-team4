"""고정 MySQL 스키마와 fake-only Silver snapshot 적재를 검증한다."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

import src.silver.mysql_loader as mysql_loader_module
from src.silver.mysql_csv_reader import MySQLModelSnapshot
from src.silver.mysql_loader import MySQLLoadError, load_models_to_mysql
from src.silver.mysql_schema import (
    FOREIGN_KEY_SCHEMAS,
    MySQLSchemaError,
    TABLE_SCHEMAS,
    create_mysql_tables,
    ensure_mysql_foreign_keys,
    validate_mysql_schema,
)
from src.silver.mysql_settings import MySQLSettings


_TABLE_ORDER = (
    "silver_employee",
    "silver_area",
    "silver_parent_area",
    "silver_area_join_reference",
)
_DELETE_ORDER = tuple(reversed(_TABLE_ORDER))

_EXPECTED_COLUMNS: dict[
    str,
    tuple[tuple[str, str, int | None, bool], ...],
] = {
    "silver_employee": (
        ("employee_id", "VARCHAR", 20, False),
        ("employee_name", "VARCHAR", 100, False),
        ("employee_department_name", "VARCHAR", 100, False),
        ("employee_position_name", "VARCHAR", 100, False),
        ("employee_hire_datetime", "DATETIME", None, False),
        ("employee_status_code", "VARCHAR", 20, False),
    ),
    "silver_area": (
        ("area_id", "VARCHAR", 20, False),
        ("area_name", "VARCHAR", 100, False),
        ("parent_area_id", "VARCHAR", 20, True),
        ("employee_id", "VARCHAR", 20, False),
        ("area_registration_date", "DATETIME", None, False),
    ),
    "silver_parent_area": (
        ("top_area_id", "VARCHAR", 20, False),
        ("top_area_name", "VARCHAR", 100, False),
        ("top_area_level_code", "VARCHAR", 20, False),
        ("top_area_registration_date", "DATETIME", None, False),
    ),
    "silver_area_join_reference": (
        ("area_id", "VARCHAR", 20, False),
        ("parent_area_id", "VARCHAR", 20, True),
        ("parent_area_name", "VARCHAR", 100, True),
        ("employee_id", "VARCHAR", 20, False),
        ("employee_name", "VARCHAR", 100, False),
        ("employee_department_name", "VARCHAR", 100, False),
        ("employee_position_name", "VARCHAR", 100, False),
        ("employee_hire_datetime", "DATETIME", None, False),
        ("employee_status_code", "VARCHAR", 20, False),
    ),
}

_EXPECTED_PRIMARY_KEYS = {
    "silver_employee": ("employee_id",),
    "silver_area": ("area_id",),
    "silver_parent_area": ("top_area_id",),
    "silver_area_join_reference": ("area_id", "employee_id"),
}

_EXPECTED_FOREIGN_KEYS = (
    (
        "fk_silver_area_employee_id_silver_employee",
        "silver_area",
        "employee_id",
        "silver_employee",
        "employee_id",
    ),
    (
        "fk_silver_area_join_reference_area_id_silver_area",
        "silver_area_join_reference",
        "area_id",
        "silver_area",
        "area_id",
    ),
    (
        "fk_silver_area_join_reference_employee_id_silver_employee",
        "silver_area_join_reference",
        "employee_id",
        "silver_employee",
        "employee_id",
    ),
)


def _table_rows() -> tuple[tuple[object, ...], ...]:
    return tuple((table_name,) for table_name in _TABLE_ORDER)


def _column_rows() -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            table_name,
            column_name,
            ordinal_position,
            data_type.lower(),
            length,
            "YES" if nullable else "NO",
        )
        for table_name in _TABLE_ORDER
        for ordinal_position, (
            column_name,
            data_type,
            length,
            nullable,
        ) in enumerate(_EXPECTED_COLUMNS[table_name], start=1)
    )


def _primary_key_rows() -> tuple[tuple[object, ...], ...]:
    return tuple(
        (table_name, column_name, ordinal_position)
        for table_name in _TABLE_ORDER
        for ordinal_position, column_name in enumerate(
            _EXPECTED_PRIMARY_KEYS[table_name],
            start=1,
        )
    )


def _foreign_key_rows() -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            table_name,
            constraint_name,
            column_name,
            1,
            referenced_table,
            referenced_column,
            "CASCADE",
            "CASCADE",
        )
        for (
            constraint_name,
            table_name,
            column_name,
            referenced_table,
            referenced_column,
        ) in _EXPECTED_FOREIGN_KEYS
    )


class FakeInformationSchemaCursor:
    """mysql-connector tuple metadata와 cursor close 경계를 흉내 낸다."""

    def __init__(
        self,
        *,
        column_rows: Sequence[Sequence[object]] | None = None,
        foreign_key_rows: Sequence[Sequence[object]] | None = None,
        events: list[tuple[object, ...]] | None = None,
    ) -> None:
        self.table_rows = _table_rows()
        self.column_rows = tuple(column_rows or _column_rows())
        self.primary_key_rows = _primary_key_rows()
        self.foreign_key_rows = (
            _foreign_key_rows()
            if foreign_key_rows is None
            else tuple(tuple(row) for row in foreign_key_rows)
        )
        self.events = events
        self.executions: list[tuple[str, tuple[object, ...] | None]] = []
        self.current_statement = ""
        self.closed = False

    def execute(
        self,
        statement: str,
        params: Sequence[object] | None = None,
    ) -> None:
        normalized_params = None if params is None else tuple(params)
        self.current_statement = statement
        self.executions.append((statement, normalized_params))
        if self.events is not None:
            self.events.append(("schema_execute", statement, normalized_params))

    def fetchall(self) -> tuple[tuple[object, ...], ...]:
        if "information_schema.TABLES" in self.current_statement:
            return self.table_rows
        if "information_schema.COLUMNS" in self.current_statement:
            return self.column_rows
        if "information_schema.REFERENTIAL_CONSTRAINTS" in self.current_statement:
            return self.foreign_key_rows
        if "information_schema.KEY_COLUMN_USAGE" in self.current_statement:
            return self.primary_key_rows
        raise AssertionError(f"unexpected metadata query: {self.current_statement}")

    def close(self) -> None:
        self.closed = True


class FakeSchemaConnection:
    """스키마 함수가 사용할 단일 cursor와 commit 호출 기록을 제공한다."""

    def __init__(self, cursor: FakeInformationSchemaCursor) -> None:
        self.fake_cursor = cursor
        self.cursor_calls = 0
        self.commit_calls = 0

    def cursor(self) -> FakeInformationSchemaCursor:
        self.cursor_calls += 1
        return self.fake_cursor

    def commit(self) -> None:
        self.commit_calls += 1


def _snapshot() -> MySQLModelSnapshot:
    hired_at = datetime(2021, 12, 1, 5, 30, 46)
    registered_at = datetime(2018, 10, 25, 9, 31, 19)
    top_registered_at = datetime(2019, 11, 4, 0, 52, 2)
    rows: dict[str, tuple[tuple[object, ...], ...]] = {
        "silver_employee": tuple(
            (
                f"EMP{index:06d}",
                f"직원{index}",
                "분석팀",
                "팀장",
                hired_at,
                "ACTIVE",
            )
            for index in range(1, 4)
        ),
        "silver_area": (
            ("BIZ_00001", "기획", None, "EMP000001", registered_at),
            ("BIZ_00002", "분석", "BIZ_00001", "EMP000002", registered_at),
        ),
        "silver_parent_area": (("BIZ_00001", "기획", "TOP_LEVEL", top_registered_at),),
        "silver_area_join_reference": (
            (
                "BIZ_00001",
                None,
                None,
                "EMP000001",
                "직원1",
                "분석팀",
                "팀장",
                hired_at,
                "ACTIVE",
            ),
            (
                "BIZ_00002",
                "BIZ_00001",
                "기획",
                "EMP000002",
                "직원2",
                "분석팀",
                "팀장",
                hired_at,
                "ACTIVE",
            ),
        ),
    }
    columns = {
        table_name: tuple(column[0] for column in _EXPECTED_COLUMNS[table_name])
        for table_name in _TABLE_ORDER
    }
    return MySQLModelSnapshot(
        rows=rows,
        columns=columns,
        row_counts={table_name: len(rows[table_name]) for table_name in _TABLE_ORDER},
    )


def _quoted_table(statement: str) -> str:
    parts = statement.split("`")
    if len(parts) < 3:
        raise AssertionError(f"fixed table identifier is not quoted: {statement}")
    return parts[1]


class FakeDataCursor:
    """DELETE, chunk INSERT와 COUNT 결과를 메모리에서 재현한다."""

    def __init__(
        self,
        snapshot: MySQLModelSnapshot,
        *,
        events: list[tuple[object, ...]],
        fail_insert_table: str | None = None,
        count_mismatch_table: str | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.events = events
        self.fail_insert_table = fail_insert_table
        self.count_mismatch_table = count_mismatch_table
        self.loaded_counts = {table_name: 0 for table_name in _TABLE_ORDER}
        self.deleted_counts = {
            "silver_area_join_reference": 7,
            "silver_parent_area": 3,
            "silver_area": 5,
            "silver_employee": 11,
        }
        self.current_count_table: str | None = None
        self.rowcount = -1
        self.closed = False

    def execute(
        self,
        statement: str,
        params: Sequence[object] | None = None,
    ) -> None:
        normalized_params = None if params is None else tuple(params)
        self.events.append(("execute", statement, normalized_params))
        table_name = _quoted_table(statement)
        if statement.startswith("DELETE FROM "):
            self.rowcount = self.deleted_counts[table_name]
            return
        if statement.startswith("SELECT COUNT(*) FROM "):
            self.current_count_table = table_name
            return
        raise AssertionError(f"unexpected data statement: {statement}")

    def executemany(
        self,
        statement: str,
        rows: Sequence[Sequence[object]],
    ) -> None:
        normalized_rows = tuple(tuple(row) for row in rows)
        self.events.append(("executemany", statement, normalized_rows))
        table_name = _quoted_table(statement)
        if table_name == self.fail_insert_table:
            raise RuntimeError("simulated insert failure")
        self.loaded_counts[table_name] += len(normalized_rows)

    def fetchone(self) -> tuple[int]:
        if self.current_count_table is None:
            raise AssertionError("COUNT fetch without SELECT")
        count = self.loaded_counts[self.current_count_table]
        if self.current_count_table == self.count_mismatch_table:
            count += 1
        return (count,)

    def close(self) -> None:
        self.closed = True


class FakeLoaderConnection:
    """스키마 cursor와 data cursor를 분리하고 트랜잭션 경계를 기록한다."""

    def __init__(
        self,
        snapshot: MySQLModelSnapshot,
        *,
        fail_insert_table: str | None = None,
        count_mismatch_table: str | None = None,
    ) -> None:
        self.events: list[tuple[object, ...]] = []
        self.schema_cursor = FakeInformationSchemaCursor(events=self.events)
        self.data_cursor = FakeDataCursor(
            snapshot,
            events=self.events,
            fail_insert_table=fail_insert_table,
            count_mismatch_table=count_mismatch_table,
        )
        self.cursor_calls = 0
        self.start_transaction_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0

    def start_transaction(self) -> None:
        self.start_transaction_calls += 1
        self.events.append(("start_transaction",))

    def cursor(self) -> FakeInformationSchemaCursor | FakeDataCursor:
        self.cursor_calls += 1
        if self.cursor_calls == 1:
            return self.schema_cursor
        if self.cursor_calls == 2:
            return self.data_cursor
        raise AssertionError("loader opened an unexpected extra cursor")

    def commit(self) -> None:
        self.commit_calls += 1
        self.events.append(("commit",))

    def rollback(self) -> None:
        self.rollback_calls += 1
        self.events.append(("rollback",))

    def close(self) -> None:
        self.close_calls += 1
        self.events.append(("close",))


def _settings() -> MySQLSettings:
    return MySQLSettings(
        database="silver_test",
        user="loader",
        password="test-password",
        host="mysql.invalid",
        port=3306,
    )


def test_main_logs_dry_run_snapshot_counts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """MySQL dry-run 요약이 공통 logger에 모델별 행 수로 전달된다."""
    messages: list[str] = []
    configure_calls = 0
    snapshot = _snapshot()

    class CapturingLogger:
        def info(self, message: str, *args: object) -> None:
            messages.append(message % args)

    def configure() -> None:
        nonlocal configure_calls
        configure_calls += 1

    def read_snapshot(models_dir: Path) -> MySQLModelSnapshot:
        assert models_dir == tmp_path
        return snapshot

    monkeypatch.setattr(mysql_loader_module, "configure_pipeline_logging", configure)
    monkeypatch.setattr(mysql_loader_module, "LOGGER", CapturingLogger())
    monkeypatch.setattr(mysql_loader_module, "from_environment", _settings)
    monkeypatch.setattr(
        mysql_loader_module,
        "read_mysql_model_snapshot",
        read_snapshot,
    )

    assert mysql_loader_module.main(["--models-dir", str(tmp_path)]) == 0
    assert configure_calls == 1
    assert messages == [
        "mode=dry-run "
        "model_rows=silver_employee:3,silver_area:2,silver_parent_area:1,"
        "silver_area_join_reference:2"
    ]
    assert "test-password" not in "\n".join(messages)


def _connection_factory(
    connection: FakeLoaderConnection,
) -> tuple[Any, list[dict[str, object]]]:
    calls: list[dict[str, object]] = []

    def factory(**kwargs: object) -> FakeLoaderConnection:
        calls.append(dict(kwargs))
        return connection

    return factory, calls


def _data_events(
    connection: FakeLoaderConnection,
    event_name: str,
) -> list[tuple[object, ...]]:
    return [event for event in connection.events if event[0] == event_name]


def test_create_mysql_tables_emits_only_fixed_safe_create_ddl() -> None:
    """정확한 네 테이블과 고정 FK를 생성하고 commit은 호출자에게 맡긴다."""
    cursor = FakeInformationSchemaCursor()
    connection = FakeSchemaConnection(cursor)

    create_mysql_tables(connection)

    assert tuple(TABLE_SCHEMAS) == _TABLE_ORDER
    assert connection.cursor_calls == 1
    assert cursor.closed
    assert connection.commit_calls == 0
    assert len(cursor.executions) == len(_TABLE_ORDER)

    for table_name, (statement, params) in zip(
        _TABLE_ORDER,
        cursor.executions,
        strict=True,
    ):
        schema = TABLE_SCHEMAS[table_name]
        assert (
            tuple(
                (column.name, column.data_type, column.length, column.nullable)
                for column in schema.columns
            )
            == _EXPECTED_COLUMNS[table_name]
        )
        assert schema.primary_key == _EXPECTED_PRIMARY_KEYS[table_name]
        assert params is None
        assert statement.startswith(f"CREATE TABLE IF NOT EXISTS `{table_name}` (")
        assert statement.count("CREATE TABLE IF NOT EXISTS") == 1
        assert "ENGINE=InnoDB" in statement
        assert "DEFAULT CHARSET=utf8mb4" in statement
        assert ";" not in statement
        normalized = statement.upper()
        assert all(
            forbidden not in normalized
            for forbidden in (
                "CREATE DATABASE",
                "ALTER TABLE",
                "DROP TABLE",
                "TRUNCATE TABLE",
            )
        )
        for foreign_key in schema.foreign_keys:
            assert foreign_key.constraint_sql in statement
            assert "ON DELETE CASCADE ON UPDATE CASCADE" in statement

    all_create_sql = "\n".join(statement for statement, _ in cursor.executions)
    assert all_create_sql.count("FOREIGN KEY") == len(_EXPECTED_FOREIGN_KEYS)


def test_ensure_mysql_foreign_keys_adds_only_missing_fixed_constraints() -> None:
    """기존 네 테이블에는 누락된 세 FK만 고정 ALTER로 추가한다."""
    cursor = FakeInformationSchemaCursor(foreign_key_rows=())
    connection = FakeSchemaConnection(cursor)

    ensure_mysql_foreign_keys(connection, "silver_test")

    assert cursor.closed
    assert len(cursor.executions) == 1 + len(_EXPECTED_FOREIGN_KEYS)
    metadata_statement, metadata_params = cursor.executions[0]
    assert "information_schema.REFERENTIAL_CONSTRAINTS" in metadata_statement
    assert metadata_params is not None
    assert metadata_params[0] == "silver_test"
    assert tuple(
        statement for statement, params in cursor.executions[1:] if params is None
    ) == tuple(foreign_key.add_sql for foreign_key in FOREIGN_KEY_SCHEMAS.values())


def test_ensure_mysql_foreign_keys_does_not_readd_existing_constraints() -> None:
    """이미 정확한 FK가 있으면 ALTER를 실행하지 않는다."""
    cursor = FakeInformationSchemaCursor()
    connection = FakeSchemaConnection(cursor)

    ensure_mysql_foreign_keys(connection, "silver_test")

    assert cursor.closed
    assert len(cursor.executions) == 1


def test_initialize_mysql_schema_preserves_original_error_cause() -> None:
    """스키마 초기화 실패가 원본 MySQL 예외를 traceback cause로 보존한다."""
    original_error = RuntimeError("simulated schema failure")

    def failing_factory(**kwargs: object) -> None:
        del kwargs
        raise original_error

    with pytest.raises(MySQLLoadError) as captured:
        mysql_loader_module._initialize_mysql_schema(
            _settings(),
            connection_factory=failing_factory,
        )

    assert captured.value.__cause__ is original_error


def test_validate_mysql_schema_accepts_exact_parameterized_tuple_metadata() -> None:
    """정확한 tuple metadata를 허용하고 DB명·테이블명은 값으로 binding한다."""
    cursor = FakeInformationSchemaCursor()
    connection = FakeSchemaConnection(cursor)

    validate_mysql_schema(connection, "silver_test")

    assert cursor.closed
    assert len(cursor.executions) == 4
    for index, (statement, params) in enumerate(cursor.executions):
        assert params is not None
        assert "silver_test" not in statement
        assert params[0] == "silver_test"
        if index < 3:
            assert params[1:5] == _TABLE_ORDER
        if index < 2:
            assert len(params) == 5
        elif index == 2:
            assert params[-1] == "PRIMARY"
            assert len(params) == 6
        else:
            assert "information_schema.REFERENTIAL_CONSTRAINTS" in statement
            assert params[1:5] == _TABLE_ORDER
            assert params[5:] == tuple(FOREIGN_KEY_SCHEMAS)


def test_validate_mysql_schema_rejects_wrong_cascade_rule() -> None:
    """FK가 있어도 CASCADE 규칙이 다르면 스키마 불일치로 거부한다."""
    mismatched_rows = list(_foreign_key_rows())
    mismatched_rows[0] = (*mismatched_rows[0][:-1], "RESTRICT")
    cursor = FakeInformationSchemaCursor(foreign_key_rows=mismatched_rows)
    connection = FakeSchemaConnection(cursor)

    with pytest.raises(MySQLSchemaError, match="foreign-key mismatch"):
        validate_mysql_schema(connection, "silver_test")

    assert cursor.closed


def test_validate_mysql_schema_rejects_column_contract_mismatch() -> None:
    """기존 테이블의 길이가 다르면 schema validation을 fail-closed한다."""
    mismatched_rows = list(_column_rows())
    employee_name_index = next(
        index
        for index, row in enumerate(mismatched_rows)
        if row[0:2] == ("silver_employee", "employee_name")
    )
    row = mismatched_rows[employee_name_index]
    mismatched_rows[employee_name_index] = (*row[:4], 99, row[5])
    cursor = FakeInformationSchemaCursor(column_rows=mismatched_rows)
    connection = FakeSchemaConnection(cursor)

    with pytest.raises(
        MySQLSchemaError,
        match="silver_employee.*length.*mismatch",
    ):
        validate_mysql_schema(connection, "silver_test")

    assert cursor.closed


def test_load_models_to_mysql_commits_verified_parameterized_chunks() -> None:
    """명시적 트랜잭션에서 child-first 삭제와 고정 순서 적재를 검증한다."""
    snapshot = _snapshot()
    connection = FakeLoaderConnection(snapshot)
    factory, factory_calls = _connection_factory(connection)

    summary = load_models_to_mysql(
        _settings(),
        snapshot,
        chunk_size=2,
        connection_factory=factory,
    )

    assert len(factory_calls) == 1
    assert factory_calls[0]["database"] == "silver_test"
    assert factory_calls[0]["autocommit"] is False
    assert connection.events[0] == ("start_transaction",)
    assert connection.start_transaction_calls == 1
    assert connection.schema_cursor.closed
    assert connection.data_cursor.closed
    assert connection.commit_calls == 1
    assert connection.rollback_calls == 0
    assert connection.close_calls == 1

    execute_events = _data_events(connection, "execute")
    delete_events = [
        event for event in execute_events if str(event[1]).startswith("DELETE FROM ")
    ]
    assert tuple(_quoted_table(str(event[1])) for event in delete_events) == (
        _DELETE_ORDER
    )
    assert all(event[2] is None for event in delete_events)

    insert_events = _data_events(connection, "executemany")
    expected_chunks = (
        ("silver_employee", snapshot.rows["silver_employee"][:2]),
        ("silver_employee", snapshot.rows["silver_employee"][2:]),
        ("silver_area", snapshot.rows["silver_area"]),
        ("silver_parent_area", snapshot.rows["silver_parent_area"]),
        (
            "silver_area_join_reference",
            snapshot.rows["silver_area_join_reference"],
        ),
    )
    assert len(insert_events) == len(expected_chunks)
    for event, (table_name, expected_rows) in zip(
        insert_events,
        expected_chunks,
        strict=True,
    ):
        statement = str(event[1])
        columns = tuple(column[0] for column in _EXPECTED_COLUMNS[table_name])
        assert _quoted_table(statement) == table_name
        assert statement.startswith(f"INSERT INTO `{table_name}` (")
        assert statement.count("%s") == len(columns)
        assert "VALUES (" in statement
        assert event[2] == expected_rows
        assert all(str(row[0]) not in statement for row in expected_rows)

    count_events = [
        event
        for event in execute_events
        if str(event[1]).startswith("SELECT COUNT(*) FROM ")
    ]
    assert tuple(_quoted_table(str(event[1])) for event in count_events) == _TABLE_ORDER
    assert summary.model_row_counts == tuple(
        (table_name, snapshot.row_counts[table_name]) for table_name in _TABLE_ORDER
    )
    assert summary.deleted_row_counts == tuple(
        (table_name, connection.data_cursor.deleted_counts[table_name])
        for table_name in _DELETE_ORDER
    )


@pytest.mark.parametrize("failure_stage", ("insert", "count"))
def test_load_failure_rolls_back_without_commit_and_closes(
    failure_stage: str,
) -> None:
    """INSERT 예외와 COUNT 불일치 모두 전체 transaction을 rollback한다."""
    snapshot = _snapshot()
    connection = FakeLoaderConnection(
        snapshot,
        fail_insert_table=("silver_area" if failure_stage == "insert" else None),
        count_mismatch_table=("silver_employee" if failure_stage == "count" else None),
    )
    factory, _ = _connection_factory(connection)

    with pytest.raises(MySQLLoadError):
        load_models_to_mysql(
            _settings(),
            snapshot,
            chunk_size=2,
            connection_factory=factory,
        )

    assert connection.events[0] == ("start_transaction",)
    assert connection.start_transaction_calls == 1
    assert connection.commit_calls == 0
    assert connection.rollback_calls == 1
    assert connection.schema_cursor.closed
    assert connection.data_cursor.closed
    assert connection.close_calls == 1
