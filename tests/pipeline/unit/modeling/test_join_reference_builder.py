"""user-approved 9-field join-reference projection을 검증한다."""

from dataclasses import fields, replace
from typing import Any

from src.silver.contracts.phase5 import JoinReferenceData
from src.silver.modeling.projections import project_join_reference


def test_join_reference_projection_has_exact_nine_fields(
    standardized_business: Any,
) -> None:
    """join-reference의 필드 순서와 값이 승인된 9개와 정확히 일치한다."""

    projected = project_join_reference(standardized_business)

    assert tuple(field.name for field in fields(JoinReferenceData)) == (
        "area_id",
        "parent_area_id",
        "parent_area_name",
        "employee_id",
        "employee_name",
        "employee_department_name",
        "employee_position_name",
        "employee_hire_datetime",
        "employee_status_code",
    )
    assert projected == JoinReferenceData(
        area_id="BIZ_00001",
        parent_area_id="BIZ_00010",
        parent_area_name="경영지원",
        employee_id="EMP000001",
        employee_name="김은서",
        employee_department_name="인사팀",
        employee_position_name="팀장",
        employee_hire_datetime="2020-01-02T09:00:00",
        employee_status_code="ACTIVE",
    )


def test_join_reference_projection_excludes_unapproved_fields(
    standardized_business: Any,
) -> None:
    """area name·top·level·두 등록 일시가 join data에 영향 없음을 확인한다."""

    changed = replace(
        standardized_business,
        area_name="다른 영역 이름",
        top_area_id="BIZ_54321",
        top_area_name="다른 최상위",
        top_area_level_code="L2",
        area_registration_date="2022-02-02T02:02:02",
        top_area_registration_date="2023-03-03T03:03:03",
    )

    assert project_join_reference(changed) == project_join_reference(
        standardized_business
    )
