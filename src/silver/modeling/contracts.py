"""표준 Flat 입력을 네 개 Silver 모델로 투영하기 위한 고정 계약."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


STANDARDIZED_FIELDS = (
    "area_id",
    "area_name",
    "parent_area_id",
    "parent_area_name",
    "top_area_id",
    "top_area_name",
    "top_area_level_code",
    "employee_id",
    "employee_name",
    "employee_department_name",
    "employee_position_name",
    "employee_hire_datetime",
    "employee_status_code",
    "area_registration_date",
    "top_area_registration_date",
)

FLAT_INPUT_FIELDS = ("source_id", "record_id", *STANDARDIZED_FIELDS)

NORMALIZATION_REJECT_FIELDS = (
    "source_id",
    "record_id",
    "reject_stage",
    "model_name",
    "model_key",
    "reason_code",
    "reason_detail",
    "standardized_json",
)

NORMALIZATION_REJECT_STAGE = "NORMALIZATION"
MODEL_KEY_MISSING = "MODEL_KEY_MISSING"
EMPLOYEE_MODEL_CONFLICT = "EMPLOYEE_MODEL_CONFLICT"
AREA_MODEL_CONFLICT = "AREA_MODEL_CONFLICT"
PARENT_AREA_MODEL_CONFLICT = "PARENT_AREA_MODEL_CONFLICT"
JOIN_REFERENCE_MODEL_CONFLICT = "JOIN_REFERENCE_MODEL_CONFLICT"


class NormalizationContractError(ValueError):
    """누적 Flat 입력 또는 내부 모델 결과가 고정 계약을 위반한 경우."""


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """한 Silver 모델의 파일명, 컬럼 순서, key와 충돌 코드를 정의한다."""

    name: str
    filename: str
    fields: tuple[str, ...]
    key_fields: tuple[str, ...]
    conflict_reason_code: str


_MODEL_SPECS = (
    ModelSpec(
        name="silver_employee",
        filename="silver_employee.csv",
        fields=(
            "employee_id",
            "employee_name",
            "employee_department_name",
            "employee_position_name",
            "employee_hire_datetime",
            "employee_status_code",
        ),
        key_fields=("employee_id",),
        conflict_reason_code=EMPLOYEE_MODEL_CONFLICT,
    ),
    ModelSpec(
        name="silver_area",
        filename="silver_area.csv",
        fields=(
            "area_id",
            "area_name",
            "parent_area_id",
            "employee_id",
            "area_registration_date",
        ),
        key_fields=("area_id",),
        conflict_reason_code=AREA_MODEL_CONFLICT,
    ),
    ModelSpec(
        name="silver_parent_area",
        filename="silver_parent_area.csv",
        fields=(
            "top_area_id",
            "top_area_name",
            "top_area_level_code",
            "top_area_registration_date",
        ),
        key_fields=("top_area_id",),
        conflict_reason_code=PARENT_AREA_MODEL_CONFLICT,
    ),
    ModelSpec(
        name="silver_area_join_reference",
        filename="silver_area_join_reference.csv",
        fields=(
            "area_id",
            "parent_area_id",
            "parent_area_name",
            "employee_id",
            "employee_name",
            "employee_department_name",
            "employee_position_name",
            "employee_hire_datetime",
            "employee_status_code",
        ),
        key_fields=("area_id", "employee_id"),
        conflict_reason_code=JOIN_REFERENCE_MODEL_CONFLICT,
    ),
)

MODEL_SPECS: Mapping[str, ModelSpec] = MappingProxyType(
    {spec.name: spec for spec in _MODEL_SPECS}
)


@dataclass(frozen=True, slots=True)
class NormalizationReject:
    """Flat 통과 후 모델 투영에서 제외된 source 한 건의 사유."""

    source_id: str
    record_id: object
    model_name: str
    model_key: str
    reason_code: str
    reason_detail: str
    standardized_json: str
    reject_stage: str = NORMALIZATION_REJECT_STAGE

    def as_row(self) -> dict[str, object]:
        """고정 CSV 컬럼 순서의 dictionary를 반환한다."""
        return {
            "source_id": self.source_id,
            "record_id": self.record_id,
            "reject_stage": self.reject_stage,
            "model_name": self.model_name,
            "model_key": self.model_key,
            "reason_code": self.reason_code,
            "reason_detail": self.reason_detail,
            "standardized_json": self.standardized_json,
        }


@dataclass(frozen=True, slots=True)
class NormalizationProjection:
    """누적 Flat 전체에서 생성한 네 모델, Reject와 source accounting."""

    model_rows: Mapping[str, tuple[dict[str, object], ...]]
    rejects: tuple[NormalizationReject, ...]
    input_source_count: int
    accepted_source_count: int
    rejected_source_count: int
    orphan_counts: Mapping[str, int]


__all__ = [
    "AREA_MODEL_CONFLICT",
    "EMPLOYEE_MODEL_CONFLICT",
    "FLAT_INPUT_FIELDS",
    "JOIN_REFERENCE_MODEL_CONFLICT",
    "MODEL_KEY_MISSING",
    "MODEL_SPECS",
    "NORMALIZATION_REJECT_FIELDS",
    "NORMALIZATION_REJECT_STAGE",
    "NormalizationContractError",
    "NormalizationProjection",
    "NormalizationReject",
    "PARENT_AREA_MODEL_CONFLICT",
    "STANDARDIZED_FIELDS",
    "ModelSpec",
]
