"""Phase 6 모델 키 병합·정렬·충돌 경계를 검증한다."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.silver.contracts.phase5 import (
    EmployeeData,
    JoinReferenceData,
    JoinReferenceKey,
)
from src.silver.modeling.assembly import assemble_phase5_output
from src.silver.modeling.phase4_binding import Phase4ContractViolation
from src.silver.modeling.projections import (
    Phase5ProjectionResult,
    ProjectionCandidate,
)


def _employee_result(
    candidates: tuple[ProjectionCandidate[EmployeeData], ...],
) -> Phase5ProjectionResult:
    """employee 후보만 가진 최소 Phase 6 입력을 만든다.

    Args:
        candidates: dedup 전에 검증할 employee 후보들.

    Returns:
        다른 모델 후보가 비어 있는 내부 projection 결과.
    """

    return Phase5ProjectionResult(
        context=SimpleNamespace(contract_version="contract-v1"),
        employees=candidates,
        areas=(),
        parent_areas=(),
        join_references=(),
        rejected=(),
        source_metrics=object(),
    )


def _join_reference_data(
    area_id: str = "BIZ_00001",
    employee_id: str = "EMP000001",
) -> JoinReferenceData:
    """복합키 조립 테스트에 사용할 조인 참조 data를 만든다.

    Args:
        area_id: 조인 참조의 영역 키 구성요소.
        employee_id: 조인 참조의 직원 키 구성요소.

    Returns:
        두 식별자를 payload에도 그대로 가진 JoinReferenceData.
    """

    return JoinReferenceData(
        area_id=area_id,
        parent_area_id=None,
        parent_area_name=None,
        employee_id=employee_id,
        employee_name=f"직원 {employee_id}",
        employee_department_name="데이터팀",
        employee_position_name="선임",
        employee_hire_datetime="2020-01-02T09:00:00",
        employee_status_code="ACTIVE",
    )


def _join_reference_result(
    candidates: tuple[ProjectionCandidate[JoinReferenceData], ...],
) -> Phase5ProjectionResult:
    """join-reference 후보만 가진 최소 Phase 6 입력을 만든다.

    Args:
        candidates: 복합키 중복 제거를 검증할 join-reference 후보들.

    Returns:
        다른 모델 후보가 비어 있는 내부 projection 결과.
    """

    return Phase5ProjectionResult(
        context=SimpleNamespace(contract_version="contract-v1"),
        employees=(),
        areas=(),
        parent_areas=(),
        join_references=candidates,
        rejected=(),
        source_metrics=object(),
    )


def test_same_key_same_data_merges_sorted_unique_source_ids() -> None:
    """동일 키·동일 데이터 후보를 한 레코드와 정렬된 고유 ID로 병합한다."""

    employee_a = EmployeeData(
        employee_id="EMP000002",
        employee_name="두번째",
        employee_department_name="데이터팀",
        employee_position_name="선임",
        employee_hire_datetime="2020-01-02T09:00:00",
        employee_status_code="ACTIVE",
    )
    employee_b = EmployeeData(
        employee_id="EMP000001",
        employee_name="첫번째",
        employee_department_name="데이터팀",
        employee_position_name="선임",
        employee_hire_datetime="2020-01-02T09:00:00",
        employee_status_code="ACTIVE",
    )
    candidates = (
        ProjectionCandidate("EMP000002", employee_a, 3),
        ProjectionCandidate("EMP000001", employee_b, 2),
        ProjectionCandidate("EMP000002", employee_a, 1),
        ProjectionCandidate("EMP000002", employee_a, 3),
    )

    output = assemble_phase5_output(_employee_result(candidates))

    assert tuple(record.metadata.model_key for record in output.employees) == (
        "EMP000001",
        "EMP000002",
    )
    assert output.employees[1].metadata.source_record_ids == (1, 3)
    assert output.model_counts.employee == 2
    assert output.model_counts.area == 0
    assert output.model_counts.parent_area == 0
    assert output.model_counts.join_reference == 0


def test_same_key_different_data_fails_closed() -> None:
    """동일 model key의 데이터가 다르면 첫값·마지막값을 선택하지 않는다."""

    first = EmployeeData(
        employee_id="EMP000001",
        employee_name="첫 값",
        employee_department_name="데이터팀",
        employee_position_name="선임",
        employee_hire_datetime="2020-01-02T09:00:00",
        employee_status_code="ACTIVE",
    )
    different = EmployeeData(
        employee_id="EMP000001",
        employee_name="다른 값",
        employee_department_name="데이터팀",
        employee_position_name="선임",
        employee_hire_datetime="2020-01-02T09:00:00",
        employee_status_code="ACTIVE",
    )
    candidates = (
        ProjectionCandidate("EMP000001", first, 1),
        ProjectionCandidate("EMP000001", different, 2),
    )

    with pytest.raises(Phase4ContractViolation, match="projection conflict"):
        assemble_phase5_output(_employee_result(candidates))


def test_candidate_input_order_does_not_change_final_records() -> None:
    """후보 입력 순서가 키 정렬·lineage·fingerprint 결과에 영향 없음을 확인한다."""

    employee = EmployeeData(
        employee_id="EMP000001",
        employee_name="순서 독립",
        employee_department_name="데이터팀",
        employee_position_name="선임",
        employee_hire_datetime="2020-01-01T00:00:00",
        employee_status_code="ACTIVE",
    )
    forward = (
        ProjectionCandidate("EMP000001", employee, 9),
        ProjectionCandidate("EMP000001", employee, 1),
    )
    reverse = tuple(reversed(forward))

    forward_output = assemble_phase5_output(_employee_result(forward))
    reverse_output = assemble_phase5_output(_employee_result(reverse))

    assert forward_output.employees == reverse_output.employees


def test_non_tuple_candidates_fail_closed() -> None:
    """Phase 6 조립 경계는 list 후보를 tuple로 보정하지 않는다."""

    employee = EmployeeData(
        employee_id="EMP000001",
        employee_name="목록 후보",
        employee_department_name="데이터팀",
        employee_position_name="선임",
        employee_hire_datetime="2020-01-01T00:00:00",
        employee_status_code="ACTIVE",
    )
    result = _employee_result(
        [ProjectionCandidate("EMP000001", employee, 1)]  # type: ignore[arg-type]
    )

    with pytest.raises(Phase4ContractViolation, match="candidates must be tuple"):
        assemble_phase5_output(result)


def test_empty_model_key_fails_closed() -> None:
    """빈 model key를 정상 모델 레코드로 조립하지 않는다."""

    employee = EmployeeData(
        employee_id="EMP000001",
        employee_name="빈 키",
        employee_department_name="데이터팀",
        employee_position_name="선임",
        employee_hire_datetime="2020-01-01T00:00:00",
        employee_status_code="ACTIVE",
    )
    result = _employee_result((ProjectionCandidate("", employee, 1),))

    with pytest.raises(Phase4ContractViolation, match="non-empty str"):
        assemble_phase5_output(result)


def test_model_key_must_match_canonical_data_key() -> None:
    """후보 metadata key가 data의 employee key와 다르면 조립을 중단한다."""

    employee = EmployeeData(
        employee_id="EMP000001",
        employee_name="키 불일치",
        employee_department_name="데이터팀",
        employee_position_name="선임",
        employee_hire_datetime="2020-01-01T00:00:00",
        employee_status_code="ACTIVE",
    )
    result = _employee_result((ProjectionCandidate("EMP999999", employee, 1),))

    with pytest.raises(Phase4ContractViolation, match="must equal data.employee_id"):
        assemble_phase5_output(result)


def test_non_dataclass_candidate_data_fails_closed() -> None:
    """임의 객체를 Phase 6 normal data로 조립하지 않는다."""

    result = _employee_result(
        (
            ProjectionCandidate(
                "EMP000001",
                SimpleNamespace(employee_id="EMP000001"),  # type: ignore[arg-type]
                1,
            ),
        )
    )

    with pytest.raises(Phase4ContractViolation, match="dataclass instance"):
        assemble_phase5_output(result)


def test_join_reference_same_composite_key_and_data_deduplicates() -> None:
    """동일 조인 복합키·동일 payload는 한 건으로 lineage만 병합한다."""

    data = _join_reference_data()
    model_key = JoinReferenceKey(data.area_id, data.employee_id)
    candidates = (
        ProjectionCandidate(model_key, data, 3),
        ProjectionCandidate(model_key, data, 1),
        ProjectionCandidate(model_key, data, 3),
    )

    output = assemble_phase5_output(_join_reference_result(candidates))

    assert len(output.join_references) == 1
    assert output.join_references[0].metadata.model_key == model_key
    assert output.join_references[0].metadata.source_record_ids == (1, 3)


def test_join_reference_composite_key_preserves_each_component_distinctness() -> None:
    """같은 area 또는 employee를 공유해도 다른 페어는 각각 보존한다."""

    data_area_employee_2 = _join_reference_data(employee_id="EMP000002")
    data_area_employee_1 = _join_reference_data(employee_id="EMP000001")
    data_other_area_employee_1 = _join_reference_data(
        area_id="BIZ_00002",
        employee_id="EMP000001",
    )
    candidates = (
        ProjectionCandidate(
            JoinReferenceKey("BIZ_00001", "EMP000002"),
            data_area_employee_2,
            2,
        ),
        ProjectionCandidate(
            JoinReferenceKey("BIZ_00002", "EMP000001"),
            data_other_area_employee_1,
            3,
        ),
        ProjectionCandidate(
            JoinReferenceKey("BIZ_00001", "EMP000001"),
            data_area_employee_1,
            1,
        ),
    )

    output = assemble_phase5_output(_join_reference_result(candidates))

    assert tuple(
        record.metadata.model_key for record in output.join_references
    ) == (
        JoinReferenceKey("BIZ_00001", "EMP000001"),
        JoinReferenceKey("BIZ_00001", "EMP000002"),
        JoinReferenceKey("BIZ_00002", "EMP000001"),
    )


def test_join_reference_same_composite_key_with_conflict_fails_closed() -> None:
    """동일 조인 복합키의 상충 payload는 첫값·마지막값을 선택하지 않는다."""

    first = _join_reference_data()
    conflicting = replace(first, employee_name="다른 직원 이름")
    model_key = JoinReferenceKey(first.area_id, first.employee_id)
    candidates = (
        ProjectionCandidate(model_key, first, 1),
        ProjectionCandidate(model_key, conflicting, 2),
    )

    with pytest.raises(
        Phase4ContractViolation,
        match="join_reference projection conflict",
    ):
        assemble_phase5_output(_join_reference_result(candidates))


@pytest.mark.parametrize("empty_field", ("area_id", "employee_id"))
def test_join_reference_empty_composite_key_component_fails_closed(
    empty_field: str,
) -> None:
    """조인 복합키의 어느 구성요소도 빈 문자열로 조립하지 않는다."""

    data = _join_reference_data()
    key_values = {
        "area_id": data.area_id,
        "employee_id": data.employee_id,
    }
    key_values[empty_field] = ""
    model_key = JoinReferenceKey(**key_values)
    candidates = (ProjectionCandidate(model_key, data, 1),)

    with pytest.raises(Phase4ContractViolation, match=empty_field):
        assemble_phase5_output(_join_reference_result(candidates))


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("area_id", 1),
        ("area_id", None),
        ("employee_id", 1),
        ("employee_id", None),
    ),
)
def test_join_reference_wrong_type_composite_key_component_fails_closed(
    field_name: str,
    invalid_value: object,
) -> None:
    """조인 복합키의 두 구성요소에서 integer와 None을 모두 거부한다."""

    data = _join_reference_data()
    valid_key = JoinReferenceKey(data.area_id, data.employee_id)
    invalid_key = replace(valid_key, **{field_name: invalid_value})
    candidates = (ProjectionCandidate(invalid_key, data, 1),)

    with pytest.raises(
        Phase4ContractViolation,
        match=(
            rf"join_reference model_key\.{field_name} "
            "must be a non-empty str"
        ),
    ):
        assemble_phase5_output(_join_reference_result(candidates))


def test_join_reference_delimited_string_key_fails_closed() -> None:
    """조인 키를 구분자로 합친 문자열로 우회하지 못하게 한다."""

    data = _join_reference_data()
    candidates = (
        ProjectionCandidate(
            "BIZ_00001|EMP000001",  # type: ignore[arg-type]
            data,
            1,
        ),
    )

    with pytest.raises(Phase4ContractViolation, match="JoinReferenceKey"):
        assemble_phase5_output(_join_reference_result(candidates))
