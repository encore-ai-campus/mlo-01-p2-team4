"""employee projection의 선택 필드와 값 보존을 검증한다."""

from dataclasses import replace
from typing import Any

from src.silver.contracts.phase5 import EmployeeData
from src.silver.modeling.projections import project_employee


def test_employee_projection_selects_exact_values(
    standardized_business: Any,
) -> None:
    """15개 business에서 승인된 6개 employee 값을 그대로 선택한다."""

    assert project_employee(standardized_business) == EmployeeData(
        employee_id="EMP000001",
        employee_name="김은서",
        employee_department_name="인사팀",
        employee_position_name="팀장",
        employee_hire_datetime="2020-01-02T09:00:00",
        employee_status_code="ACTIVE",
    )


def test_employee_projection_preserves_required_values(
    standardized_business: Any,
) -> None:
    """직원 모델의 네 employee 필드와 이름을 변환 없이 보존한다."""

    business = replace(
        standardized_business,
        employee_name="변경 직원",
        employee_department_name="변경 부서",
        employee_position_name="변경 직위",
        employee_hire_datetime="2021-02-03T04:05:06",
        employee_status_code="RETIRED",
    )
    projected = project_employee(business)

    assert projected.employee_name == "변경 직원"
    assert projected.employee_department_name == "변경 부서"
    assert projected.employee_position_name == "변경 직위"
    assert projected.employee_hire_datetime == "2021-02-03T04:05:06"
    assert projected.employee_status_code == "RETIRED"
