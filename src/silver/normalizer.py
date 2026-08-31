"""Atlas 레코드 한 건을 cleaned flat 값 또는 reject 사유로 정규화한다."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from bson import json_util
from bson.json_util import RELAXED_JSON_OPTIONS

from .rules import SilverRules, StandardField

SEOUL = ZoneInfo("Asia/Seoul")

NULL_LIKE_TOKENS = frozenset(
    {
        "NULL",
        "N/A",
        "NONE",
        "UNKNOWN",
        "없음",
        "미상",
        "오류값",
        "-",
    }
)
DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d",
    "%Y%m%d",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S.%f",
    "%Y.%m.%d %H:%M:%S",
    "%Y.%m.%d %H:%M:%S.%f",
    "%Y%m%d%H%M%S",
)

AREA_ID_SOURCES = frozenset({"area_no", "p_area_no", "top_area_no"})
AREA_NAME_SOURCES = frozenset({"area_nm", "p_area_nm", "top_area_nm"})
EMPLOYEE_NAME_SOURCES = frozenset({"mgr_nm", "mgr_dept_nm", "mgr_pos_nm"})
WHITESPACE_REMOVAL_SOURCES = AREA_NAME_SOURCES | EMPLOYEE_NAME_SOURCES
NULLABLE_PARENT_SOURCES = frozenset({"p_area_no", "p_area_nm"})
DATETIME_SOURCES = frozenset({"mgr_hire_dtm", "area_reg_dtm", "top_area_reg_dtm"})
CODE_SOURCES = frozenset({"mgr_act_yn", "top_area_lvl"})


@dataclass(frozen=True, slots=True)
class Violation:
    """한 원본 레코드가 reject된 구체적인 이유."""

    code: str
    field: str
    detail: str


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    """한 원본 레코드의 accept 또는 reject 결과."""

    source_id: str | None
    record_id: int | None
    accepted: Mapping[str, object] | None
    violations: tuple[Violation, ...]
    raw_json: str


class FlatNormalizer:
    """한 Atlas payload를 표준 flat 행 또는 reject 사유로 변환한다."""

    def __init__(
        self,
        rules: SilverRules,
        *,
        validation_now: datetime | None = None,
    ) -> None:
        self.rules = rules
        current = datetime.now(tz=SEOUL) if validation_now is None else validation_now
        if current.tzinfo is None:
            current = current.replace(tzinfo=SEOUL)
        else:
            current = current.astimezone(SEOUL)
        self.validation_now = current

    def normalize(
        self,
        record: object,
        *,
        raw_record: object | None = None,
    ) -> NormalizationResult:
        """원본 한 건의 모든 필드를 검증하고 단 하나의 disposition을 만든다."""
        raw_for_json = record if raw_record is None else raw_record
        raw_json = json_util.dumps(
            raw_for_json,
            json_options=RELAXED_JSON_OPTIONS,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        violations: list[Violation] = []
        if not isinstance(record, Mapping):
            return NormalizationResult(
                source_id=None,
                record_id=None,
                accepted=None,
                violations=(
                    Violation(
                        "TYPE_INVALID", "record", "Atlas 레코드가 객체가 아닙니다."
                    ),
                ),
                raw_json=raw_json,
            )

        source_id = extract_source_id(record)
        if source_id is None:
            violations.append(
                Violation(
                    "SOURCE_ID_INVALID",
                    "source_id",
                    "MongoDB _id를 비어 있지 않은 문자열로 변환할 수 없습니다.",
                )
            )

        observed_record_id = record.get("record_id")
        record_id = observed_record_id if type(observed_record_id) is int else None
        if record_id is None or record_id < 1:
            violations.append(
                Violation(
                    "RECORD_ID_INVALID",
                    "record_id",
                    "record_id는 1 이상의 정수여야 합니다.",
                )
            )

        payload = record.get("payload")
        if not isinstance(payload, Mapping):
            violations.append(
                Violation("PAYLOAD_INVALID", "payload", "payload가 객체가 아닙니다.")
            )
            return NormalizationResult(
                source_id=source_id,
                record_id=record_id,
                accepted=None,
                violations=tuple(violations),
                raw_json=raw_json,
            )

        accepted: dict[str, object] = {}
        for field in self.rules.fields:
            normalized, violation = self._normalize_field(
                field, payload.get(field.source)
            )
            accepted[field.target] = normalized
            if violation is not None:
                violations.append(violation)

        return NormalizationResult(
            source_id=source_id,
            record_id=record_id,
            accepted=None if violations else accepted,
            violations=tuple(violations),
            raw_json=raw_json,
        )

    def _normalize_field(
        self,
        field: StandardField,
        raw_value: object,
    ) -> tuple[object | None, Violation | None]:
        """한 source field를 종류별 표준값으로 변환한다."""
        text, missing_kind = self._clean_text(field.source, raw_value)
        if missing_kind == "missing":
            if field.nullable:
                return None, None
            return None, Violation(
                "REQUIRED_VALUE_MISSING",
                field.target,
                f"{field.source} 필수값이 없습니다.",
            )
        if missing_kind == "placeholder":
            if field.nullable and field.source in NULLABLE_PARENT_SOURCES:
                return None, None
            return None, Violation(
                "NULL_LIKE_VALUE",
                field.target,
                f"{field.source}에 결측 대체 토큰이 있습니다.",
            )
        if missing_kind == "type" or text is None:
            return None, Violation(
                "TYPE_INVALID",
                field.target,
                f"{field.source} 값의 타입을 정규화할 수 없습니다.",
            )

        if field.source in WHITESPACE_REMOVAL_SOURCES:
            text = re.sub(r"\s+", "", text)

        if field.source in AREA_ID_SOURCES:
            match = re.fullmatch(r"BIZ(?:_|-| )?(\d{5})", text, re.IGNORECASE)
            if match is None:
                return None, self._invalid("IDENTIFIER_INVALID", field)
            return f"BIZ_{match.group(1)}", None

        if field.source == "mgr_no":
            match = re.fullmatch(r"EMP(?:[ -]?)(\d{6})", text, re.IGNORECASE)
            if match is None:
                return None, self._invalid("IDENTIFIER_INVALID", field)
            return f"EMP{match.group(1)}", None

        if field.source in AREA_NAME_SOURCES:
            candidate = re.sub(r"\s+\d+$", "", text).strip().upper()
            for raw_pattern, canonical_name in self.rules.area_names:
                if candidate.startswith(raw_pattern):
                    return canonical_name, None
            return None, self._invalid("AREA_NAME_UNMAPPED", field)

        if field.source == "mgr_act_yn":
            normalized = self.rules.status_codes.get(text.upper())
            if normalized is None:
                return None, self._invalid("STATUS_CODE_INVALID", field)
            return normalized, None

        if field.source == "top_area_lvl":
            normalized = self.rules.level_codes.get(text.upper())
            if normalized is None:
                return None, self._invalid("LEVEL_CODE_INVALID", field)
            return normalized, None

        if field.source in DATETIME_SOURCES:
            return self._normalize_datetime(field, text)

        if len(text) > 100:
            return None, self._invalid("TEXT_INVALID", field)
        return text, None

    @staticmethod
    def _clean_text(source: str, value: object) -> tuple[str | None, str | None]:
        """공백, 결측 대체 토큰, 허용 타입을 구분한다."""
        if value is None:
            return None, "missing"
        if isinstance(value, datetime) and source in DATETIME_SOURCES:
            text = value.isoformat()
        elif isinstance(value, str):
            text = value
        elif type(value) is int and source in CODE_SOURCES:
            text = str(value)
        else:
            return None, "type"
        if not text:
            return None, "missing"
        if text.upper() in NULL_LIKE_TOKENS:
            return None, "placeholder"
        return text, None

    def _normalize_datetime(
        self,
        field: StandardField,
        text: str,
    ) -> tuple[str | None, Violation | None]:
        """지원 형식의 일시를 서울 시각 ISO 초 단위로 바꾼다."""
        parsed: datetime | None = None
        for date_format in DATE_FORMATS:
            try:
                parsed = datetime.strptime(text, date_format)  # noqa: DTZ007
                break
            except ValueError:
                continue
        if parsed is None or parsed.microsecond != 0:
            return None, self._invalid("DATETIME_INVALID", field)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=SEOUL)
        else:
            parsed = parsed.astimezone(SEOUL)
        if parsed > self.validation_now:
            return None, Violation(
                "FUTURE_DATETIME",
                field.target,
                f"{field.source} 값이 실행 기준시각보다 미래입니다.",
            )
        return parsed.replace(tzinfo=None).isoformat(timespec="seconds"), None

    @staticmethod
    def _invalid(code: str, field: StandardField) -> Violation:
        """필드별 일반 정규화 실패 사유를 만든다."""
        return Violation(
            code,
            field.target,
            f"{field.source} 값을 표준 형식으로 변환할 수 없습니다.",
        )


def extract_source_id(record: object) -> str | None:
    """MongoDB `_id`를 checkpoint와 CSV에 사용할 문자열로 변환한다."""
    if not isinstance(record, Mapping):
        return None
    observed = record.get("_id")
    if observed is None or isinstance(observed, (Mapping, list, tuple, set)):
        return None
    source_id = str(observed).strip()
    return source_id or None


__all__ = [
    "FlatNormalizer",
    "NormalizationResult",
    "SEOUL",
    "Violation",
    "extract_source_id",
]
