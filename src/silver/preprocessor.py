"""원본의 의미를 추정하지 않는 최소 문자열 전처리를 담당한다."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping

_CONTROL_CATEGORIES = frozenset({"Cc", "Cf"})
_WHITESPACE_PATTERN = re.compile(r"\s+")


class BasicPreprocessor:
    """원본 구조를 유지하며 문자열 표기만 최소 정리한다."""

    def preprocess(self, record: object) -> object:
        """NFKC, 제어문자 제거, 공백 정규화를 재귀적으로 적용한다."""
        return self._clean_value(record)

    def _clean_value(self, value: object) -> object:
        if isinstance(value, str):
            return self.clean_text(value)
        if isinstance(value, Mapping):
            return {key: self._clean_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._clean_value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self._clean_value(item) for item in value)
        return value

    @staticmethod
    def clean_text(value: str) -> str:
        """문자열의 의미는 바꾸지 않고 비정상 표기와 공백만 정리한다."""
        normalized = unicodedata.normalize("NFKC", value)
        characters: list[str] = []
        for character in normalized:
            if character.isspace():
                characters.append(" ")
            elif unicodedata.category(character) not in _CONTROL_CATEGORIES:
                characters.append(character)
        return _WHITESPACE_PATTERN.sub(" ", "".join(characters)).strip()


def preprocess_record(record: object) -> object:
    """기본 전처리기를 사용하는 함수형 공개 경로다."""
    return BasicPreprocessor().preprocess(record)


__all__ = ["BasicPreprocessor", "preprocess_record"]
