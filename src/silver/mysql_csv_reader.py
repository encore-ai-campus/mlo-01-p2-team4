"""네 개 Silver 모델 CSV를 MySQL 적재용 불변 snapshot으로 읽는다."""

from __future__ import annotations

import csv
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType

from src.silver.modeling.contracts import MODEL_SPECS, ModelSpec


_EXPECTED_MODEL_NAMES = (
    "silver_employee",
    "silver_area",
    "silver_parent_area",
    "silver_area_join_reference",
)
_NULLABLE_FIELDS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "silver_employee": frozenset(),
        "silver_area": frozenset({"parent_area_id"}),
        "silver_parent_area": frozenset(),
        "silver_area_join_reference": frozenset({"parent_area_id", "parent_area_name"}),
    }
)


class MySQLInputError(ValueError):
    """MySQL 적재 전 모델 CSV가 고정 입력 계약을 위반한 경우."""


@dataclass(frozen=True, slots=True)
class MySQLModelSnapshot:
    """검증과 형 변환이 끝난 네 모델의 불변 적재 snapshot.

    각 ``rows[model_name]``의 행은 ``columns[model_name]``과 같은 순서의
    tuple이다. 날짜·일시 필드는 naive :class:`datetime`이고, 허용된 nullable
    필드의 빈 문자열만 ``None``으로 변환된다.
    """

    rows: Mapping[str, tuple[tuple[object, ...], ...]]
    columns: Mapping[str, tuple[str, ...]]
    row_counts: Mapping[str, int]


def read_mysql_model_snapshot(models_dir: Path) -> MySQLModelSnapshot:
    """``models_dir``의 네 모델 CSV를 검증하고 적재 snapshot으로 반환한다.

    파일과 ordered header 계약은 ``MODEL_SPECS``를 단일 기준으로 사용한다.
    모든 필수 값과 모델 key를 검증하고, key 중복 또는 timezone-aware 일시를
    발견하면 전체 snapshot 생성을 중단한다.
    """

    _validate_model_contracts()
    resolved_dir = Path(models_dir)
    if not resolved_dir.is_dir():
        raise MySQLInputError(f"Silver 모델 디렉터리가 없습니다: {resolved_dir}")

    model_rows: dict[str, tuple[tuple[object, ...], ...]] = {}
    columns: dict[str, tuple[str, ...]] = {}
    row_counts: dict[str, int] = {}

    for model_name in _EXPECTED_MODEL_NAMES:
        spec = MODEL_SPECS[model_name]
        rows = _read_model_rows(
            resolved_dir / spec.filename,
            spec=spec,
            nullable_fields=_NULLABLE_FIELDS[model_name],
        )
        model_rows[model_name] = rows
        columns[model_name] = spec.fields
        row_counts[model_name] = len(rows)

    return MySQLModelSnapshot(
        rows=MappingProxyType(model_rows),
        columns=MappingProxyType(columns),
        row_counts=MappingProxyType(row_counts),
    )


def _validate_model_contracts() -> None:
    if tuple(MODEL_SPECS) != _EXPECTED_MODEL_NAMES:
        raise MySQLInputError(
            "MySQL 입력 모델 계약은 정확히 네 개의 Silver 모델이어야 합니다."
        )


def _read_model_rows(
    path: Path,
    *,
    spec: ModelSpec,
    nullable_fields: frozenset[str],
) -> tuple[tuple[object, ...], ...]:
    if not path.is_file():
        raise MySQLInputError(f"Silver 모델 CSV가 없습니다: {path}")

    try:
        with path.open(encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file, strict=True)
            actual_fields = tuple(reader.fieldnames or ())
            if actual_fields != spec.fields:
                raise MySQLInputError(
                    f"{spec.filename} header 순서가 고정 계약과 다릅니다."
                )

            rows: list[tuple[object, ...]] = []
            seen_keys: set[tuple[str, ...]] = set()
            for raw_row in reader:
                row_number = reader.line_num
                if None in raw_row or any(
                    raw_row.get(field) is None for field in spec.fields
                ):
                    raise MySQLInputError(
                        f"{spec.filename} 컬럼 수가 올바르지 않습니다: row={row_number}"
                    )

                key = tuple(raw_row[field] for field in spec.key_fields)
                if any(not value.strip() for value in key):
                    raise MySQLInputError(
                        f"{spec.filename} 모델 key가 비어 있습니다: row={row_number}"
                    )
                if key in seen_keys:
                    raise MySQLInputError(
                        f"{spec.filename} 모델 key가 중복되었습니다: row={row_number}"
                    )
                seen_keys.add(key)

                rows.append(
                    tuple(
                        _parse_field(
                            raw_row[field],
                            field=field,
                            nullable=field in nullable_fields,
                            filename=spec.filename,
                            row_number=row_number,
                        )
                        for field in spec.fields
                    )
                )
    except MySQLInputError:
        raise
    except (OSError, UnicodeError, csv.Error) as error:
        raise MySQLInputError(f"Silver 모델 CSV를 읽을 수 없습니다: {path}") from error

    return tuple(rows)


def _parse_field(
    value: str,
    *,
    field: str,
    nullable: bool,
    filename: str,
    row_number: int,
) -> object:
    if nullable and value == "":
        return None
    if not nullable and not value.strip():
        raise MySQLInputError(
            f"{filename} 필수 필드가 비어 있습니다: row={row_number}, field={field}"
        )
    if not field.endswith(("_datetime", "_date")):
        return value

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise MySQLInputError(
            f"{filename} datetime 형식이 올바르지 않습니다: "
            f"row={row_number}, field={field}"
        ) from None
    if parsed.tzinfo is not None:
        raise MySQLInputError(
            f"{filename} datetime에는 timezone을 사용할 수 없습니다: "
            f"row={row_number}, field={field}"
        )
    return parsed


__all__ = [
    "MySQLInputError",
    "MySQLModelSnapshot",
    "read_mysql_model_snapshot",
]
