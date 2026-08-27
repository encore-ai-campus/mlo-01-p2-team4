"""Phase 5 모델 데이터와 최종 출력의 불변 계약을 정의한다."""

from dataclasses import dataclass
from typing import Generic, TypeVar

ModelDataT = TypeVar("ModelDataT")


@dataclass(frozen=True, slots=True)
class EmployeeData:
    """직원 모델에 투영되는 표준 필드 묶음이다."""

    employee_id: str
    employee_name: str | None
    employee_department_name: str | None
    employee_position_name: str | None
    employee_hire_datetime: str | None
    employee_status_code: str | None


@dataclass(frozen=True, slots=True)
class AreaData:
    """직접 부모와 관리자 참조만 포함하는 영역 모델 데이터다."""

    division_id: str
    division_name: str
    parent_division_id: str | None
    employee_id: str
    division_registered_datetime: str | None


@dataclass(frozen=True, slots=True)
class ParentAreaData:
    """원천의 최상위 영역 값만 사용하는 부모 조회 모델 데이터다."""

    top_division_id: str
    top_division_name: str
    division_level_code: str | None
    top_division_registered_datetime: str | None


@dataclass(frozen=True, slots=True)
class JoinReferenceData:
    """영역과 직원의 공유 필드만 담는 조인 참조 모델 데이터다."""

    division_id: str
    parent_division_id: str | None
    parent_division_name: str | None
    employee_id: str
    employee_name: str | None
    employee_department_name: str | None
    employee_position_name: str | None
    employee_hire_datetime: str | None
    employee_status_code: str | None


@dataclass(frozen=True, slots=True)
class ModelMetadata:
    """Phase 6에서 조립할 모델 키·지문·lineage 메타데이터다."""

    model_key: str
    model_fingerprint: str
    source_record_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ModelRecord(Generic[ModelDataT]):
    """모델 데이터와 Phase 6 메타데이터를 결합하는 불변 레코드다."""

    data: ModelDataT
    metadata: ModelMetadata


@dataclass(frozen=True, slots=True)
class ModelCounts:
    """Phase 6에서 확정할 모델별 최종 레코드 수다."""

    employee: int
    area: int
    parent_area: int
    join_reference: int


@dataclass(frozen=True, slots=True)
class Phase5Output:
    """Phase 6 조립 이후 외부로 전달할 최종 Phase 5 출력 계약이다."""

    context: object
    employees: tuple[ModelRecord[EmployeeData], ...]
    areas: tuple[ModelRecord[AreaData], ...]
    parent_areas: tuple[ModelRecord[ParentAreaData], ...]
    join_references: tuple[ModelRecord[JoinReferenceData], ...]
    rejected: tuple[object, ...]
    source_metrics: object
    model_counts: ModelCounts
