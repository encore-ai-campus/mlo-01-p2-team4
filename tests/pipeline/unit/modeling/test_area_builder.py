"""area projection이 immediate parent 경계만 사용하는지 검증한다."""

from dataclasses import replace
from typing import Any

from src.silver.contracts.phase5 import AreaData
from src.silver.modeling.projections import project_area


def test_area_projection_selects_exact_values(standardized_business: Any) -> None:
    """영역 모델의 5개 승인 필드를 business에서 그대로 선택한다."""

    assert project_area(standardized_business) == AreaData(
        area_id="BIZ_00001",
        area_name="인사본부",
        parent_area_id="BIZ_00010",
        employee_id="EMP000001",
        area_registration_date="2024-01-01T00:00:00",
    )


def test_area_projection_ignores_top_and_parent_name_fields(
    standardized_business: Any,
) -> None:
    """top 관계와 부모 이름 변경이 area data에 들어오지 않는지 확인한다."""

    changed = replace(
        standardized_business,
        parent_area_name="다른 부모 이름",
        top_area_id="BIZ_54321",
        top_area_name="다른 최상위",
        top_area_level_code="L2",
        top_area_registration_date="2022-02-02T02:02:02",
    )

    assert project_area(changed) == project_area(standardized_business)
