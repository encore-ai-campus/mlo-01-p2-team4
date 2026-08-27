"""parent-area lookup projection의 top-only 원천 경계를 검증한다."""

from dataclasses import replace
from typing import Any

from src.silver.contracts.phase5 import ParentAreaData
from src.silver.modeling.projections import project_parent_area


def test_parent_area_projection_selects_top_values_only(
    standardized_business: Any,
) -> None:
    """네 parent-area 필드가 모두 top_area 계열 값에서 오는지 확인한다."""

    assert project_parent_area(standardized_business) == ParentAreaData(
        top_area_id="BIZ_00099",
        top_area_name="본사",
        top_area_level_code="TOP_LEVEL",
        top_area_registration_date="2010-01-01T00:00:00",
    )


def test_parent_area_projection_ignores_immediate_parent_values(
    standardized_business: Any,
) -> None:
    """immediate parent ID·name 변경을 top lookup에 혼합하지 않는다."""

    changed = replace(
        standardized_business,
        parent_area_id=None,
        parent_area_name="다른 직접 부모",
        area_name="다른 하위 영역",
    )

    assert project_parent_area(changed) == project_parent_area(standardized_business)
