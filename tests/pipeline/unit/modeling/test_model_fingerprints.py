"""Phase 6 canonical model fingerprint 계약을 독립 기대값으로 검증한다."""

from dataclasses import dataclass, replace

import pytest

from src.silver.contracts.phase5 import EmployeeData
from src.silver.modeling.assembly import compute_model_fingerprint
from src.silver.modeling.phase4_binding import Phase4ContractViolation


@dataclass(frozen=True, slots=True)
class OrderedData:
    """alpha 다음 beta 순서로 선언한 fingerprint 입력이다."""

    alpha: str
    beta: int


@dataclass(frozen=True, slots=True)
class ReverseOrderedData:
    """beta 다음 alpha 순서로 선언한 동등 fingerprint 입력이다."""

    beta: int
    alpha: str


def _employee_data() -> EmployeeData:
    """UTF-8 canonical 직렬화를 확인할 employee data를 만든다.

    Returns:
        한글과 모든 필수 직원 필드를 가진 frozen EmployeeData.
    """

    return EmployeeData(
        employee_id="EMP000001",
        employee_name="김은서",
        employee_department_name="데이터팀",
        employee_position_name="선임",
        employee_hire_datetime="2020-01-02T09:00:00",
        employee_status_code="ACTIVE",
    )


def test_fingerprint_matches_exact_canonical_payload_hash() -> None:
    """세 payload 필드만 compact sorted-key UTF-8 JSON으로 hash하는지 확인한다."""

    actual = compute_model_fingerprint(
        "employee",
        _employee_data(),
        "계약-v1",
    )

    assert actual == "aaea18222c8d7992b807a2bb79ab2714bf6568dc25479d4a917ce7cc4cf60686"


def test_fingerprint_is_sensitive_to_each_payload_component() -> None:
    """model name·data·contract version 중 하나만 달라도 지문이 바뀐다."""

    data = _employee_data()
    fingerprints = {
        compute_model_fingerprint("employee", data, "v1"),
        compute_model_fingerprint("manager", data, "v1"),
        compute_model_fingerprint(
            "employee",
            replace(data, employee_name="다른 이름"),
            "v1",
        ),
        compute_model_fingerprint("employee", data, "v2"),
    }

    assert len(fingerprints) == 4


def test_data_key_declaration_order_does_not_change_fingerprint() -> None:
    """동일 key/value의 dataclass 선언 순서가 canonical hash에 영향 없음을 확인한다."""

    ordered = OrderedData(alpha="값", beta=2)
    reversed_order = ReverseOrderedData(beta=2, alpha="값")

    assert compute_model_fingerprint("sample", ordered, "v1") == (
        compute_model_fingerprint("sample", reversed_order, "v1")
    )


def test_non_dataclass_fingerprint_input_is_rejected() -> None:
    """임의 mapping을 dataclass model data처럼 fingerprint하지 않는다."""

    with pytest.raises(Phase4ContractViolation, match="must be a dataclass"):
        compute_model_fingerprint("employee", {"employee_id": "EMP000001"}, "v1")
