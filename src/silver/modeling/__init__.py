"""Phase 4 출력을 검증하고 Silver 모델 후보로 투영한다."""

from .phase4_binding import (
    Phase4ContractViolation,
    Phase4IntegrationBinding,
    Phase4IntegrationUnavailable,
    unavailable_phase4_binding,
)
from .processor import Phase5Processor
from .projections import Phase5ProjectionResult, ProjectionCandidate

__all__ = [
    "Phase4ContractViolation",
    "Phase4IntegrationBinding",
    "Phase4IntegrationUnavailable",
    "Phase5Processor",
    "Phase5ProjectionResult",
    "ProjectionCandidate",
    "unavailable_phase4_binding",
]
