"""검증된 표준 business 레코드를 네 Silver 모델 후보로 순수 투영한다."""

from dataclasses import dataclass
from typing import Any, Generic, TypeVar, cast

from ..contracts.phase5 import (
    AreaData,
    EmployeeData,
    JoinReferenceData,
    ModelKey,
    ParentAreaData,
)
from .phase4_binding import Phase4ContractViolation

ProjectionDataT = TypeVar("ProjectionDataT")


@dataclass(frozen=True, slots=True)
class ProjectionCandidate(Generic[ProjectionDataT]):
    """Phase 6 dedup 전 모델 데이터와 단일 원천 record ID를 보존한다."""

    model_key: ModelKey
    data: ProjectionDataT
    source_record_id: int


@dataclass(frozen=True, slots=True)
class Phase5ProjectionResult:
    """Phase 5 검증·투영과 Phase 6 조립 사이의 내부 전달 결과다."""

    context: object
    employees: tuple[ProjectionCandidate[EmployeeData], ...]
    areas: tuple[ProjectionCandidate[AreaData], ...]
    parent_areas: tuple[ProjectionCandidate[ParentAreaData], ...]
    join_references: tuple[ProjectionCandidate[JoinReferenceData], ...]
    rejected: tuple[object, ...]
    source_metrics: object


def project_employee(business: object) -> EmployeeData:
    """검증된 표준 business 값에서 직원 모델 데이터를 투영한다.

    Args:
        business: Phase 5 방어 검증을 통과한 StandardizedBusinessRecord.

    Returns:
        입력 값을 변환하지 않고 선택한 EmployeeData.
    """

    source = cast(Any, business)
    return EmployeeData(
        employee_id=source.employee_id,
        employee_name=source.employee_name,
        employee_department_name=source.employee_department_name,
        employee_position_name=source.employee_position_name,
        employee_hire_datetime=source.employee_hire_datetime,
        employee_status_code=source.employee_status_code,
    )


def project_area(business: object) -> AreaData:
    """검증된 표준 business 값에서 즉시 부모 기반 영역 데이터를 투영한다.

    Args:
        business: Phase 5 방어 검증을 통과한 StandardizedBusinessRecord.

    Returns:
        top 관계를 추가하지 않은 AreaData.
    """

    source = cast(Any, business)
    return AreaData(
        area_id=source.area_id,
        area_name=source.area_name,
        parent_area_id=source.parent_area_id,
        employee_id=source.employee_id,
        area_registration_date=source.area_registration_date,
    )


def project_parent_area(business: object) -> ParentAreaData:
    """검증된 표준 business의 top 필드만 부모 조회 데이터로 투영한다.

    Args:
        business: Phase 5 방어 검증을 통과한 StandardizedBusinessRecord.

    Returns:
        immediate parent나 join lookup을 사용하지 않은 ParentAreaData.
    """

    source = cast(Any, business)
    return ParentAreaData(
        top_area_id=source.top_area_id,
        top_area_name=source.top_area_name,
        top_area_level_code=source.top_area_level_code,
        top_area_registration_date=(source.top_area_registration_date),
    )


def project_join_reference(business: object) -> JoinReferenceData:
    """검증된 표준 business에서 승인된 9개 조인 참조 필드를 투영한다.

    Args:
        business: Phase 5 방어 검증을 통과한 StandardizedBusinessRecord.

    Returns:
        area name·top 필드·등록 일시를 제외한 JoinReferenceData.
    """

    source = cast(Any, business)
    return JoinReferenceData(
        area_id=source.area_id,
        parent_area_id=source.parent_area_id,
        parent_area_name=source.parent_area_name,
        employee_id=source.employee_id,
        employee_name=source.employee_name,
        employee_department_name=source.employee_department_name,
        employee_position_name=source.employee_position_name,
        employee_hire_datetime=source.employee_hire_datetime,
        employee_status_code=source.employee_status_code,
    )


def ensure_no_projection_conflicts(
    candidates: tuple[ProjectionCandidate[ProjectionDataT], ...],
    model_name: str,
) -> None:
    """같은 모델 키가 서로 다른 projection data를 갖는지 검사한다.

    같은 키·같은 데이터 후보는 Phase 6 dedup과 source ID 집계를 위해 그대로
    보존한다. 이 함수는 첫 값이나 마지막 값을 선택하지 않는다.

    Args:
        candidates: accepted 순서를 보존한 모델 후보들.
        model_name: 위반 메시지에 사용할 모델 이름.

    Raises:
        Phase4ContractViolation: 같은 키에서 서로 다른 데이터가 발견된 경우.
    """

    first_data_by_key: dict[ModelKey, ProjectionDataT] = {}
    first_source_id_by_key: dict[ModelKey, int] = {}

    for candidate in candidates:
        model_key = candidate.model_key
        if model_key not in first_data_by_key:
            first_data_by_key[model_key] = candidate.data
            first_source_id_by_key[model_key] = candidate.source_record_id
            continue

        if first_data_by_key[model_key] != candidate.data:
            first_source_record_id = first_source_id_by_key[model_key]
            raise Phase4ContractViolation(
                f"{model_name} projection conflict for model_key="
                f"{model_key!r}: source_record_ids="
                f"({first_source_record_id}, {candidate.source_record_id})"
            )
