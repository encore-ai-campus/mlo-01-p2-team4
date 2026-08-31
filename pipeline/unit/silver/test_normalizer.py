"""Silver 필드 정규화의 공백·결측 토큰 회귀 계약을 검증한다."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from src.silver.normalizer import FlatNormalizer, NormalizationResult, SEOUL
from src.silver.rules import SilverRules

VALIDATION_NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=SEOUL)

EXPECTED_AREA_NAME_MAPPINGS = {
    "고객서비스": "고객서비스",
    "교육": "교육",
    "구매": "구매",
    "기획": "기획",
    "데이터": "데이터",
    "마케팅": "마케팅",
    "물류": "물류",
    "법무": "법무",
    "보안": "보안",
    "분석": "분석",
    "생산": "생산",
    "시설": "시설",
    "영업": "영업",
    "인사": "인사",
    "자산관리": "자산관리",
    "재무": "재무",
    "전략": "전략",
    "품질관리": "품질관리",
    "IT": "IT",
    "R&D": "R&D",
}

NULL_LIKE_TOKENS = (
    "NULL",
    "N/A",
    "NONE",
    "UNKNOWN",
    "없음",
    "미상",
    "오류값",
    "-",
)

NON_NULLABLE_FIELDS = (
    ("area_no", "area_id"),
    ("area_nm", "area_name"),
    ("top_area_no", "top_area_id"),
    ("top_area_nm", "top_area_name"),
    ("top_area_lvl", "top_area_level_code"),
    ("mgr_no", "employee_id"),
    ("mgr_nm", "employee_name"),
    ("mgr_dept_nm", "employee_department_name"),
    ("mgr_pos_nm", "employee_position_name"),
    ("mgr_hire_dtm", "employee_hire_datetime"),
    ("mgr_act_yn", "employee_status_code"),
    ("area_reg_dtm", "area_registration_date"),
    ("top_area_reg_dtm", "top_area_registration_date"),
)


def _valid_record() -> dict[str, Any]:
    """모든 필드 규칙을 통과하는 전처리 완료 레코드를 만든다."""
    return {
        "_id": "source-1",
        "record_id": 1,
        "payload": {
            "area_no": "BIZ_10001",
            "area_nm": "보안관리 49",
            "p_area_no": None,
            "p_area_nm": "",
            "top_area_no": "BIZ_00004",
            "top_area_nm": "기획",
            "top_area_lvl": "TOP LEVEL",
            "mgr_no": "EMP000038",
            "mgr_nm": "이민서",
            "mgr_dept_nm": "분석팀",
            "mgr_pos_nm": "팀장",
            "mgr_hire_dtm": "2021-12-01T05:30:46",
            "mgr_act_yn": "사용",
            "area_reg_dtm": "2018-10-25T09:31:19",
            "top_area_reg_dtm": "2019-11-04T00:52:02",
        },
    }


def _normalize(**payload_overrides: object) -> tuple[SilverRules, NormalizationResult]:
    """기본 규칙으로 payload 일부만 바꾼 레코드를 정규화한다."""
    rules = SilverRules.load_default()
    record = _valid_record()
    record["payload"].update(payload_overrides)
    result = FlatNormalizer(rules, validation_now=VALIDATION_NOW).normalize(record)
    return rules, result


def test_area_names_remove_internal_whitespace_before_approved_mapping() -> None:
    """세 영역명은 내부 공백을 없앤 뒤 기존 승인 mapping을 적용한다."""
    _, result = _normalize(
        area_nm="보 안 관 리 49",
        p_area_no="BIZ_00005",
        p_area_nm="데 이 터 7",
        top_area_nm="기 획 3",
    )

    assert result.violations == ()
    assert result.accepted is not None
    assert result.accepted["area_name"] == "보안"
    assert result.accepted["parent_area_name"] == "데이터"
    assert result.accepted["top_area_name"] == "기획"


def test_area_name_rule_set_remains_the_existing_twenty_mappings() -> None:
    """공백 정규화 추가가 승인된 20개 영역 mapping을 바꾸지 않는다."""
    rules = SilverRules.load_default()

    assert dict(rules.area_names) == EXPECTED_AREA_NAME_MAPPINGS


def test_employee_text_fields_remove_internal_whitespace_together() -> None:
    """직원명·부서명·직위명은 같은 규칙으로 내부 공백을 제거한다."""
    _, result = _normalize(
        mgr_nm="김 민 수",
        mgr_dept_nm="데 이 터 분 석 팀",
        mgr_pos_nm="책 임 매 니 저",
    )

    assert result.violations == ()
    assert result.accepted is not None
    assert result.accepted["employee_name"] == "김민수"
    assert result.accepted["employee_department_name"] == "데이터분석팀"
    assert result.accepted["employee_position_name"] == "책임매니저"


@pytest.mark.parametrize("token", NULL_LIKE_TOKENS)
def test_nullable_parent_fields_convert_null_like_tokens_to_none(token: str) -> None:
    """nullable 부모 ID·이름의 결측 토큰만 정상적인 None으로 허용한다."""
    _, result = _normalize(p_area_no=token, p_area_nm=token)

    assert result.violations == ()
    assert result.accepted is not None
    assert result.accepted["parent_area_id"] is None
    assert result.accepted["parent_area_name"] is None


@pytest.mark.parametrize(("source", "target"), NON_NULLABLE_FIELDS)
def test_null_like_token_still_rejects_every_non_nullable_field(
    source: str,
    target: str,
) -> None:
    """같은 결측 토큰은 필수 필드에서 기존 NULL_LIKE_VALUE Reject를 유지한다."""
    _, result = _normalize(**{source: "NULL"})

    assert result.accepted is None
    assert [(violation.code, violation.field) for violation in result.violations] == [
        ("NULL_LIKE_VALUE", target)
    ]
