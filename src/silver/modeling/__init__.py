"""누적 표준 Flat을 네 개 Silver 모델 snapshot으로 투영한다."""

from .contracts import (
    AREA_MODEL_CONFLICT,
    EMPLOYEE_MODEL_CONFLICT,
    FLAT_INPUT_FIELDS,
    JOIN_REFERENCE_MODEL_CONFLICT,
    MODEL_KEY_MISSING,
    MODEL_SPECS,
    NORMALIZATION_REJECT_FIELDS,
    NORMALIZATION_REJECT_STAGE,
    PARENT_AREA_MODEL_CONFLICT,
    STANDARDIZED_FIELDS,
    ModelSpec,
    NormalizationContractError,
    NormalizationProjection,
    NormalizationReject,
)
from .materializer import NormalizationRunSummary, materialize_normalized_outputs
from .projections import build_normalization_projection

__all__ = [
    "AREA_MODEL_CONFLICT",
    "EMPLOYEE_MODEL_CONFLICT",
    "FLAT_INPUT_FIELDS",
    "JOIN_REFERENCE_MODEL_CONFLICT",
    "MODEL_KEY_MISSING",
    "MODEL_SPECS",
    "NORMALIZATION_REJECT_FIELDS",
    "NORMALIZATION_REJECT_STAGE",
    "PARENT_AREA_MODEL_CONFLICT",
    "STANDARDIZED_FIELDS",
    "ModelSpec",
    "NormalizationContractError",
    "NormalizationProjection",
    "NormalizationReject",
    "NormalizationRunSummary",
    "build_normalization_projection",
    "materialize_normalized_outputs",
]
