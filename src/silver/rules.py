"""Silver 표준 용어와 값 mapping을 읽고 검증한다."""

from __future__ import annotations

import csv
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class SilverRuleError(RuntimeError):
    """필수 표준 용어 또는 정규화 mapping이 유효하지 않을 때 발생한다."""


@dataclass(frozen=True, slots=True)
class StandardField:
    """원천 payload 필드와 표준 flat 필드의 최소 mapping."""

    source: str
    target: str
    nullable: bool


@dataclass(frozen=True, slots=True)
class SilverRules:
    """실행에 필요한 표준 용어와 값 mapping만 보관한다."""

    fields: tuple[StandardField, ...]
    status_codes: Mapping[str, str]
    level_codes: Mapping[str, str]
    area_names: tuple[tuple[str, str], ...]

    @classmethod
    def load_default(cls, project_root: Path = PROJECT_ROOT) -> SilverRules:
        """남아 있는 표준 용어와 정규화 mapping 파일을 읽는다."""
        standards_root = project_root / "standards"
        fields = _load_standard_fields(
            project_root / "data-contracts" / "standard-term.csv"
        )
        status_codes, level_codes = _load_code_mappings(
            standards_root / "code-normalization.yaml"
        )
        area_names = _load_area_names(standards_root / "area-name-normalization.csv")
        return cls(
            fields=fields,
            status_codes=status_codes,
            level_codes=level_codes,
            area_names=area_names,
        )

    @property
    def output_fields(self) -> tuple[str, ...]:
        """표준 용어 파일 순서의 physical field 목록을 반환한다."""
        return tuple(field.target for field in self.fields)


def _load_standard_fields(path: Path) -> tuple[StandardField, ...]:
    """표준 용어 CSV에서 source-to-physical mapping과 nullability를 읽는다."""
    required = {"physical_name", "nullable", "source_columns"}
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise SilverRuleError("standard-term.csv 필수 열이 없습니다.")
        rows = list(reader)

    fields: list[StandardField] = []
    seen_sources: set[str] = set()
    seen_targets: set[str] = set()
    for row in rows:
        source = row["source_columns"].strip()
        target = row["physical_name"].strip()
        nullable_text = row["nullable"].strip().upper()
        if not source or "|" in source:
            raise SilverRuleError("표준 용어의 source_columns는 단일 필드여야 합니다.")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", target):
            raise SilverRuleError("표준 physical_name이 lower_snake_case가 아닙니다.")
        if nullable_text not in {"Y", "N"}:
            raise SilverRuleError("표준 용어 nullable은 Y 또는 N이어야 합니다.")
        if source in seen_sources or target in seen_targets:
            raise SilverRuleError(
                "표준 용어에 중복 source 또는 physical name이 있습니다."
            )
        seen_sources.add(source)
        seen_targets.add(target)
        fields.append(
            StandardField(
                source=source,
                target=target,
                nullable=nullable_text == "Y",
            )
        )
    if not fields:
        raise SilverRuleError("표준 용어가 비어 있습니다.")
    return tuple(fields)


def _load_code_mappings(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """YAML의 허용 코드값만 case-insensitive lookup으로 만든다."""
    with path.open(encoding="utf-8") as file:
        document = yaml.safe_load(file)
    if not isinstance(document, Mapping) or document.get("unknown_action") != "reject":
        raise SilverRuleError(
            "code-normalization.yaml은 unknown_action=reject여야 합니다."
        )

    def load_section(name: str) -> dict[str, str]:
        section = document.get(name)
        if not isinstance(section, Mapping):
            raise SilverRuleError(
                f"code-normalization.yaml의 {name} mapping이 없습니다."
            )
        allowed_values = section.get("allowed_values")
        if (
            type(allowed_values) is not list
            or not allowed_values
            or any(
                type(value) is not str or not value.strip() for value in allowed_values
            )
        ):
            raise SilverRuleError(f"{name}.allowed_values가 유효하지 않습니다.")
        allowed = {value.strip() for value in allowed_values}
        source_values = section.get("source_values")
        if not isinstance(source_values, Mapping) or not source_values:
            raise SilverRuleError(f"{name}.source_values가 비어 있습니다.")
        mappings: dict[str, str] = {}
        for raw, canonical in source_values.items():
            key = unicodedata.normalize("NFKC", str(raw)).strip().upper()
            if type(canonical) is not str or not canonical.strip():
                raise SilverRuleError(
                    f"{name}.source_values 표준값이 유효하지 않습니다."
                )
            canonical_value = canonical.strip()
            if canonical_value not in allowed:
                raise SilverRuleError(
                    f"{name}.source_values 표준값이 allowed_values에 없습니다."
                )
            if not key:
                raise SilverRuleError(f"{name}.source_values 원천값이 비어 있습니다.")
            previous = mappings.get(key)
            if previous is not None and previous != canonical_value:
                raise SilverRuleError(
                    f"{name}.source_values가 정규화 후 서로 다른 표준값으로 충돌합니다."
                )
            mappings[key] = canonical_value
        return mappings

    return load_section("status"), load_section("level")


def _load_area_names(path: Path) -> tuple[tuple[str, str], ...]:
    """승인된 업무영역명 prefix mapping만 읽는다."""
    required = {"raw_pattern", "canonical_name", "match_type"}
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise SilverRuleError("area-name-normalization.csv 필수 열이 없습니다.")
        rows = list(reader)

    mappings: dict[str, str] = {}
    for row in rows:
        raw = unicodedata.normalize("NFKC", row["raw_pattern"]).strip()
        canonical = unicodedata.normalize("NFKC", row["canonical_name"]).strip()
        if row["match_type"] != "prefix_after_numeric_suffix_trim":
            raise SilverRuleError("지원하지 않는 업무영역명 match_type입니다.")
        key = raw.upper()
        if not raw or not canonical or key in mappings:
            raise SilverRuleError("업무영역명 mapping이 비었거나 중복되었습니다.")
        mappings[key] = canonical
    if not mappings:
        raise SilverRuleError("업무영역명 mapping이 비어 있습니다.")
    return tuple(sorted(mappings.items(), key=lambda item: (-len(item[0]), item[0])))


__all__ = ["PROJECT_ROOT", "SilverRuleError", "SilverRules", "StandardField"]
