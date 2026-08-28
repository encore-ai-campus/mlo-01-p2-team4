"""MySQL DDL and validation for the four fixed Silver tables."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Protocol, Sequence


class MySQLSchemaError(RuntimeError):
    """The existing MySQL Silver schema does not match the fixed contract."""


@dataclass(frozen=True, slots=True)
class ColumnSchema:
    """One column in a fixed Silver MySQL table."""

    name: str
    data_type: str
    length: int | None
    nullable: bool

    @property
    def sql(self) -> str:
        """Return this fixed column definition for CREATE TABLE."""
        length_sql = f"({self.length})" if self.length is not None else ""
        nullability_sql = "NULL" if self.nullable else "NOT NULL"
        return f"`{self.name}` {self.data_type}{length_sql} {nullability_sql}"


@dataclass(frozen=True, slots=True)
class ForeignKeySchema:
    """One named foreign-key relationship in the fixed Silver schema."""

    name: str
    table_name: str
    columns: tuple[str, ...]
    referenced_table: str
    referenced_columns: tuple[str, ...]
    update_rule: str = "CASCADE"
    delete_rule: str = "CASCADE"

    @property
    def constraint_sql(self) -> str:
        """Return this relationship's fixed CREATE TABLE constraint clause."""
        columns_sql = ", ".join(f"`{name}`" for name in self.columns)
        referenced_columns_sql = ", ".join(
            f"`{name}`" for name in self.referenced_columns
        )
        return (
            f"CONSTRAINT `{self.name}` FOREIGN KEY ({columns_sql}) "
            f"REFERENCES `{self.referenced_table}` ({referenced_columns_sql}) "
            f"ON DELETE {self.delete_rule} ON UPDATE {self.update_rule}"
        )

    @property
    def add_sql(self) -> str:
        """Return the fixed ALTER statement used for an existing table."""
        return f"ALTER TABLE `{self.table_name}` ADD {self.constraint_sql}"


@dataclass(frozen=True, slots=True)
class TableSchema:
    """Columns, key, and storage settings for one fixed Silver table."""

    name: str
    columns: tuple[ColumnSchema, ...]
    primary_key: tuple[str, ...]
    foreign_keys: tuple[ForeignKeySchema, ...] = ()
    engine: str = "InnoDB"
    charset: str = "utf8mb4"

    @property
    def column_names(self) -> tuple[str, ...]:
        """Return the canonical INSERT and validation column order."""
        return tuple(column.name for column in self.columns)

    @property
    def create_sql(self) -> str:
        """Return the only DDL statement allowed for this table."""
        definitions = [column.sql for column in self.columns]
        primary_key_sql = ", ".join(f"`{name}`" for name in self.primary_key)
        definitions.append(f"PRIMARY KEY ({primary_key_sql})")
        definitions.extend(
            foreign_key.constraint_sql for foreign_key in self.foreign_keys
        )
        body = ",\n    ".join(definitions)
        return (
            f"CREATE TABLE IF NOT EXISTS `{self.name}` (\n"
            f"    {body}\n"
            f") ENGINE={self.engine} DEFAULT CHARSET={self.charset}"
        )


_FOREIGN_KEY_SCHEMAS = (
    ForeignKeySchema(
        name="fk_silver_area_employee_id_silver_employee",
        table_name="silver_area",
        columns=("employee_id",),
        referenced_table="silver_employee",
        referenced_columns=("employee_id",),
    ),
    ForeignKeySchema(
        name="fk_silver_area_join_reference_area_id_silver_area",
        table_name="silver_area_join_reference",
        columns=("area_id",),
        referenced_table="silver_area",
        referenced_columns=("area_id",),
    ),
    ForeignKeySchema(
        name="fk_silver_area_join_reference_employee_id_silver_employee",
        table_name="silver_area_join_reference",
        columns=("employee_id",),
        referenced_table="silver_employee",
        referenced_columns=("employee_id",),
    ),
)


def _foreign_keys_for(table_name: str) -> tuple[ForeignKeySchema, ...]:
    return tuple(
        foreign_key
        for foreign_key in _FOREIGN_KEY_SCHEMAS
        if foreign_key.table_name == table_name
    )


_TABLE_SCHEMAS = (
    TableSchema(
        name="silver_employee",
        columns=(
            ColumnSchema("employee_id", "VARCHAR", 20, False),
            ColumnSchema("employee_name", "VARCHAR", 100, False),
            ColumnSchema("employee_department_name", "VARCHAR", 100, False),
            ColumnSchema("employee_position_name", "VARCHAR", 100, False),
            ColumnSchema("employee_hire_datetime", "DATETIME", None, False),
            ColumnSchema("employee_status_code", "VARCHAR", 20, False),
        ),
        primary_key=("employee_id",),
    ),
    TableSchema(
        name="silver_area",
        columns=(
            ColumnSchema("area_id", "VARCHAR", 20, False),
            ColumnSchema("area_name", "VARCHAR", 100, False),
            ColumnSchema("parent_area_id", "VARCHAR", 20, True),
            ColumnSchema("employee_id", "VARCHAR", 20, False),
            ColumnSchema("area_registration_date", "DATETIME", None, False),
        ),
        primary_key=("area_id",),
        foreign_keys=_foreign_keys_for("silver_area"),
    ),
    TableSchema(
        name="silver_parent_area",
        columns=(
            ColumnSchema("top_area_id", "VARCHAR", 20, False),
            ColumnSchema("top_area_name", "VARCHAR", 100, False),
            ColumnSchema("top_area_level_code", "VARCHAR", 20, False),
            ColumnSchema("top_area_registration_date", "DATETIME", None, False),
        ),
        primary_key=("top_area_id",),
        foreign_keys=_foreign_keys_for("silver_parent_area"),
    ),
    TableSchema(
        name="silver_area_join_reference",
        columns=(
            ColumnSchema("area_id", "VARCHAR", 20, False),
            ColumnSchema("parent_area_id", "VARCHAR", 20, True),
            ColumnSchema("parent_area_name", "VARCHAR", 100, True),
            ColumnSchema("employee_id", "VARCHAR", 20, False),
            ColumnSchema("employee_name", "VARCHAR", 100, False),
            ColumnSchema("employee_department_name", "VARCHAR", 100, False),
            ColumnSchema("employee_position_name", "VARCHAR", 100, False),
            ColumnSchema("employee_hire_datetime", "DATETIME", None, False),
            ColumnSchema("employee_status_code", "VARCHAR", 20, False),
        ),
        primary_key=("area_id", "employee_id"),
        foreign_keys=_foreign_keys_for("silver_area_join_reference"),
    ),
)

TABLE_SCHEMAS: Mapping[str, TableSchema] = MappingProxyType(
    {schema.name: schema for schema in _TABLE_SCHEMAS}
)
FOREIGN_KEY_SCHEMAS: Mapping[str, ForeignKeySchema] = MappingProxyType(
    {schema.name: schema for schema in _FOREIGN_KEY_SCHEMAS}
)


class _Cursor(Protocol):
    def execute(
        self,
        operation: str,
        params: Sequence[object] | None = None,
    ) -> object: ...

    def fetchall(self) -> Sequence[Sequence[object]]: ...

    def close(self) -> object: ...


class _Connection(Protocol):
    def cursor(self) -> _Cursor: ...


def create_mysql_tables(connection: _Connection) -> None:
    """Create the four fixed Silver tables without an explicit commit."""
    cursor = connection.cursor()
    try:
        for schema in TABLE_SCHEMAS.values():
            cursor.execute(schema.create_sql)
    finally:
        cursor.close()


def ensure_mysql_foreign_keys(
    connection: _Connection,
    database_name: str,
) -> None:
    """Add only absent fixed foreign keys to the four existing Silver tables.

    Unexpected foreign keys and expected names with different definitions fail
    closed before any ALTER statement is issued. Existing constraints are never
    disabled, dropped, or replaced, and the caller retains commit ownership.
    """
    cursor = connection.cursor()
    try:
        foreign_key_rows = _query_foreign_key_rows(cursor, database_name)
        actual_foreign_keys, errors = _parse_foreign_key_rows(foreign_key_rows)
        errors.extend(
            _foreign_key_contract_errors(
                actual_foreign_keys,
                require_all=False,
            )
        )
        if errors:
            raise MySQLSchemaError(
                "MySQL Silver foreign-key reconciliation failed: " + "; ".join(errors)
            )

        for name, foreign_key in FOREIGN_KEY_SCHEMAS.items():
            if name not in actual_foreign_keys:
                cursor.execute(foreign_key.add_sql)
    finally:
        cursor.close()


def validate_mysql_schema(connection: _Connection, database_name: str) -> None:
    """Raise when the database's four Silver tables differ from the contract."""
    table_names = tuple(TABLE_SCHEMAS)
    placeholders = ", ".join("%s" for _ in table_names)
    table_params: tuple[object, ...] = (database_name, *table_names)

    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT TABLE_NAME "
            "FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = %s "
            f"AND TABLE_NAME IN ({placeholders})",
            table_params,
        )
        table_rows = tuple(cursor.fetchall())

        cursor.execute(
            "SELECT TABLE_NAME, COLUMN_NAME, ORDINAL_POSITION, DATA_TYPE, "
            "CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE "
            "FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = %s "
            f"AND TABLE_NAME IN ({placeholders}) "
            "ORDER BY TABLE_NAME, ORDINAL_POSITION",
            table_params,
        )
        column_rows = tuple(cursor.fetchall())

        cursor.execute(
            "SELECT TABLE_NAME, COLUMN_NAME, ORDINAL_POSITION "
            "FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA = %s "
            f"AND TABLE_NAME IN ({placeholders}) "
            "AND CONSTRAINT_NAME = %s "
            "ORDER BY TABLE_NAME, ORDINAL_POSITION",
            (*table_params, "PRIMARY"),
        )
        primary_key_rows = tuple(cursor.fetchall())

        foreign_key_rows = _query_foreign_key_rows(cursor, database_name)
    finally:
        cursor.close()

    errors = _schema_errors(
        table_rows,
        column_rows,
        primary_key_rows,
        foreign_key_rows,
    )
    if errors:
        raise MySQLSchemaError(
            "MySQL Silver schema validation failed: " + "; ".join(errors)
        )


def _schema_errors(
    table_rows: Sequence[Sequence[object]],
    column_rows: Sequence[Sequence[object]],
    primary_key_rows: Sequence[Sequence[object]],
    foreign_key_rows: Sequence[Sequence[object]],
) -> list[str]:
    errors: list[str] = []

    actual_tables = {
        str(row[0]) for row in table_rows if _row_has_fields(row, 1, "TABLES", errors)
    }
    missing_tables = [name for name in TABLE_SCHEMAS if name not in actual_tables]
    if missing_tables:
        errors.append("missing tables=" + repr(missing_tables))

    actual_columns: dict[str, list[tuple[int, str, str, int | None, bool]]] = {
        name: [] for name in TABLE_SCHEMAS
    }
    for row in column_rows:
        if not _row_has_fields(row, 6, "COLUMNS", errors):
            continue
        table_name = str(row[0])
        if table_name not in actual_columns:
            errors.append(f"unexpected column metadata table={table_name!r}")
            continue
        try:
            ordinal_position = int(row[2])
            length = None if row[4] is None else int(row[4])
            nullable = _parse_nullable(row[5])
        except (TypeError, ValueError) as error:
            errors.append(f"invalid column metadata table={table_name!r}: {error}")
            continue
        actual_columns[table_name].append(
            (
                ordinal_position,
                str(row[1]),
                str(row[3]).upper(),
                length,
                nullable,
            )
        )

    for table_name, schema in TABLE_SCHEMAS.items():
        if table_name not in actual_tables:
            continue
        expected = tuple(
            (
                ordinal_position,
                column.name,
                column.data_type,
                column.length,
                column.nullable,
            )
            for ordinal_position, column in enumerate(schema.columns, start=1)
        )
        actual = tuple(sorted(actual_columns[table_name], key=lambda item: item[0]))
        if actual != expected:
            errors.append(
                f"{table_name} column/order/type/length/nullability mismatch: "
                f"expected={expected!r}, actual={actual!r}"
            )

    actual_primary_keys: dict[str, list[tuple[int, str]]] = {
        name: [] for name in TABLE_SCHEMAS
    }
    for row in primary_key_rows:
        if not _row_has_fields(row, 3, "KEY_COLUMN_USAGE", errors):
            continue
        table_name = str(row[0])
        if table_name not in actual_primary_keys:
            errors.append(f"unexpected primary-key metadata table={table_name!r}")
            continue
        try:
            ordinal_position = int(row[2])
        except (TypeError, ValueError) as error:
            errors.append(f"invalid primary-key metadata table={table_name!r}: {error}")
            continue
        actual_primary_keys[table_name].append((ordinal_position, str(row[1])))

    for table_name, schema in TABLE_SCHEMAS.items():
        if table_name not in actual_tables:
            continue
        expected = tuple(enumerate(schema.primary_key, start=1))
        actual = tuple(
            sorted(actual_primary_keys[table_name], key=lambda item: item[0])
        )
        if actual != expected:
            errors.append(
                f"{table_name} primary-key mismatch: "
                f"expected={expected!r}, actual={actual!r}"
            )

    actual_foreign_keys, foreign_key_errors = _parse_foreign_key_rows(foreign_key_rows)
    errors.extend(foreign_key_errors)
    errors.extend(
        _foreign_key_contract_errors(
            actual_foreign_keys,
            require_all=True,
        )
    )

    return errors


def _query_foreign_key_rows(
    cursor: _Cursor,
    database_name: str,
) -> tuple[Sequence[object], ...]:
    table_names = tuple(TABLE_SCHEMAS)
    constraint_names = tuple(FOREIGN_KEY_SCHEMAS)
    table_placeholders = ", ".join("%s" for _ in table_names)
    constraint_placeholders = ", ".join("%s" for _ in constraint_names)
    cursor.execute(
        "SELECT kcu.TABLE_NAME, kcu.CONSTRAINT_NAME, kcu.COLUMN_NAME, "
        "kcu.ORDINAL_POSITION, kcu.REFERENCED_TABLE_NAME, "
        "kcu.REFERENCED_COLUMN_NAME, rc.UPDATE_RULE, rc.DELETE_RULE "
        "FROM information_schema.KEY_COLUMN_USAGE AS kcu "
        "INNER JOIN information_schema.REFERENTIAL_CONSTRAINTS AS rc "
        "ON rc.CONSTRAINT_SCHEMA = kcu.CONSTRAINT_SCHEMA "
        "AND rc.TABLE_NAME = kcu.TABLE_NAME "
        "AND rc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME "
        "WHERE kcu.CONSTRAINT_SCHEMA = %s "
        "AND kcu.REFERENCED_TABLE_NAME IS NOT NULL "
        f"AND (kcu.TABLE_NAME IN ({table_placeholders}) "
        f"OR kcu.CONSTRAINT_NAME IN ({constraint_placeholders})) "
        "ORDER BY kcu.TABLE_NAME, kcu.CONSTRAINT_NAME, kcu.ORDINAL_POSITION",
        (database_name, *table_names, *constraint_names),
    )
    return tuple(cursor.fetchall())


def _parse_foreign_key_rows(
    foreign_key_rows: Sequence[Sequence[object]],
) -> tuple[dict[str, ForeignKeySchema], list[str]]:
    errors: list[str] = []
    grouped_rows: dict[
        str,
        list[tuple[str, int, str, str, str, str, str]],
    ] = {}

    for row in foreign_key_rows:
        if not _row_has_fields(row, 8, "FOREIGN_KEYS", errors):
            continue
        try:
            ordinal_position = int(row[3])
        except (TypeError, ValueError) as error:
            errors.append(f"invalid foreign-key metadata row={row!r}: {error}")
            continue

        constraint_name = str(row[1])
        grouped_rows.setdefault(constraint_name, []).append(
            (
                str(row[0]),
                ordinal_position,
                str(row[2]),
                str(row[4]),
                str(row[5]),
                str(row[6]).upper(),
                str(row[7]).upper(),
            )
        )

    actual_foreign_keys: dict[str, ForeignKeySchema] = {}
    for constraint_name, rows in grouped_rows.items():
        ordered_rows = tuple(sorted(rows, key=lambda item: item[1]))
        ordinals = tuple(row[1] for row in ordered_rows)
        expected_ordinals = tuple(range(1, len(ordered_rows) + 1))
        fixed_fields = {(row[0], row[3], row[5], row[6]) for row in ordered_rows}
        if ordinals != expected_ordinals or len(fixed_fields) != 1:
            errors.append(
                f"invalid foreign-key metadata name={constraint_name!r}: "
                f"rows={ordered_rows!r}"
            )
            continue

        table_name, referenced_table, update_rule, delete_rule = next(
            iter(fixed_fields)
        )
        actual_foreign_keys[constraint_name] = ForeignKeySchema(
            name=constraint_name,
            table_name=table_name,
            columns=tuple(row[2] for row in ordered_rows),
            referenced_table=referenced_table,
            referenced_columns=tuple(row[4] for row in ordered_rows),
            update_rule=update_rule,
            delete_rule=delete_rule,
        )

    return actual_foreign_keys, errors


def _foreign_key_contract_errors(
    actual_foreign_keys: Mapping[str, ForeignKeySchema],
    *,
    require_all: bool,
) -> list[str]:
    errors: list[str] = []
    unexpected = sorted(
        (foreign_key.table_name, name)
        for name, foreign_key in actual_foreign_keys.items()
        if foreign_key.table_name in TABLE_SCHEMAS and name not in FOREIGN_KEY_SCHEMAS
    )
    if unexpected:
        errors.append(f"unexpected foreign keys={unexpected!r}")

    for name, expected in FOREIGN_KEY_SCHEMAS.items():
        actual = actual_foreign_keys.get(name)
        if actual is None:
            if require_all:
                errors.append(f"missing foreign key={name!r}")
            continue
        if actual != expected:
            errors.append(
                f"foreign-key mismatch name={name!r}: "
                f"expected={expected!r}, actual={actual!r}"
            )

    return errors


def _row_has_fields(
    row: Sequence[object],
    minimum: int,
    source: str,
    errors: list[str],
) -> bool:
    try:
        field_count = len(row)
    except TypeError:
        errors.append(f"invalid {source} metadata row={row!r}")
        return False
    if field_count < minimum:
        errors.append(f"invalid {source} metadata row={row!r}")
        return False
    return True


def _parse_nullable(value: object) -> bool:
    normalized = str(value).upper()
    if normalized == "YES":
        return True
    if normalized == "NO":
        return False
    raise ValueError(f"IS_NULLABLE must be YES or NO, got {value!r}")


__all__ = [
    "ColumnSchema",
    "FOREIGN_KEY_SCHEMAS",
    "ForeignKeySchema",
    "MySQLSchemaError",
    "TABLE_SCHEMAS",
    "TableSchema",
    "create_mysql_tables",
    "ensure_mysql_foreign_keys",
    "validate_mysql_schema",
]
