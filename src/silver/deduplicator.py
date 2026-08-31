"""표준 필드 전체값을 기준으로 2차 중복 판정을 담당한다."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DuplicateMatch:
    """먼저 accept된 동일 표준 행의 lineage를 나타낸다."""

    first_source_id: str | None


class FullRowDeduplicator:
    """lineage를 제외한 모든 표준 필드값으로 가장 오래된 행을 선택한다."""

    def __init__(self, output_fields: Sequence[str]) -> None:
        self.output_fields = tuple(output_fields)
        self._seen: dict[tuple[str, ...], str | None] = {}

    def seed(self, accepted_rows: Iterable[Mapping[str, object]]) -> None:
        """이전 실행의 accept 행을 중복 기준에 먼저 등록한다."""
        for row in accepted_rows:
            source_id = self._optional_text(row.get("source_id"))
            self._seen.setdefault(self.key_for(row), source_id)

    def check_and_add(
        self,
        values: Mapping[str, object],
        *,
        source_id: str,
    ) -> DuplicateMatch | None:
        """중복이면 최초 lineage를 반환하고, 신규이면 기준 집합에 추가한다."""
        key = self.key_for(values)
        if key in self._seen:
            return DuplicateMatch(first_source_id=self._seen[key])
        self._seen[key] = source_id
        return None

    def key_for(self, values: Mapping[str, object]) -> tuple[str, ...]:
        """Mongo `_id`와 `record_id`를 제외한 표준 필드 key를 만든다."""
        return tuple(self._csv_value(values.get(field)) for field in self.output_fields)

    @staticmethod
    def _csv_value(value: object) -> str:
        return "" if value is None else str(value)

    @staticmethod
    def _optional_text(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


__all__ = ["DuplicateMatch", "FullRowDeduplicator"]
