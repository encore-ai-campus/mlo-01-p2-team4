"""Phase 7 최종 모델 교차 검증과 자동 processor 연결을 검증한다."""

from collections import namedtuple
from copy import deepcopy
from dataclasses import dataclass, fields, replace
from typing import Any

import pytest

import src.silver.modeling.model_validator as model_validator_module
from src.silver.contracts.phase5 import (
    JoinReferenceKey,
    ModelKey,
    Phase5Output,
)
from src.silver.modeling import (
    Phase5ModelValidationError,
    Phase5ModelValidator,
    Phase5Processor,
)
from src.silver.modeling.assembly import compute_model_fingerprint

COLLECTION_MODEL_NAMES = {
    "employees": "employee",
    "areas": "area",
    "parent_areas": "parent_area",
    "join_references": "join_reference",
}

EXPECTED_JOIN_FIELDS = (
    "area_id",
    "parent_area_id",
    "parent_area_name",
    "employee_id",
    "employee_name",
    "employee_department_name",
    "employee_position_name",
    "employee_hire_datetime",
    "employee_status_code",
)

RawPayloadTuple = namedtuple("RawPayloadTuple", ("raw_payload",))


@dataclass(frozen=True, slots=True)
class WrongShapeData:
    """고정 catalog와 다른 단일 필드 data를 제공한다."""

    unexpected: str


class HiddenAttributeList(list[str]):
    """동적 raw 속성을 숨길 수 있는 JSON 직렬화 가능 list subclass다."""


def _distinct_businesses(business_factory: Any) -> tuple[object, object]:
    """네 모델에서 충돌하지 않는 서로 다른 표준 business 두 개를 만든다.

    Args:
        business_factory: 기본 합성 business를 만드는 fixture factory.

    Returns:
        같은 top parent를 공유하지만 직원·area가 다른 business 두 개.
    """

    first = business_factory()
    second = replace(
        first,
        area_id="BIZ_00002",
        area_name="재무본부",
        parent_area_id=None,
        parent_area_name=None,
        employee_id="EMP000002",
        employee_name="박재무",
        employee_department_name="재무팀",
        employee_position_name="부장",
        employee_hire_datetime="2019-02-03T10:00:00",
        employee_status_code="INACTIVE",
        area_registration_date="2024-02-01T00:00:00",
    )
    return first, second


def _valid_phase4_and_phase5(
    business_factory: Any,
    accepted_factory: Any,
    output_factory: Any,
    phase5_processor: Phase5Processor,
) -> tuple[object, Phase5Output]:
    """단일 accepted record의 정상 Phase 4/5 출력을 함께 만든다.

    Args:
        business_factory: 표준 business fixture factory.
        accepted_factory: accepted record fixture factory.
        output_factory: accounting이 닫힌 Phase 4 output fixture factory.
        phase5_processor: 실제 Phase 7까지 연결된 processor.

    Returns:
        원래 Phase 4 출력과 검증을 통과한 최종 Phase 5 출력.
    """

    accepted = accepted_factory(1, business_factory())
    phase4_output = output_factory(accepted=(accepted,))
    return phase4_output, phase5_processor.process(phase4_output)


def _replace_record_data(
    output: Phase5Output,
    collection_name: str,
    data: object,
    *,
    model_key: ModelKey | None = None,
) -> Phase5Output:
    """단일 모델 data를 바꾸고 그 data의 지문을 정상적으로 다시 계산한다.

    Args:
        output: 변경의 기반이 되는 정상 Phase 5 출력.
        collection_name: 첫 record를 바꿀 collection 속성 이름.
        data: 교체할 모델 data 객체.
        model_key: 함께 교체할 metadata key. 생략하면 기존 key를 보존한다.

    Returns:
        지정 collection의 첫 record만 교체한 새 Phase5Output.
    """

    records = getattr(output, collection_name)
    original_record = records[0]
    if model_key is None:
        model_key = original_record.metadata.model_key
    metadata = replace(
        original_record.metadata,
        model_key=model_key,
        model_fingerprint=compute_model_fingerprint(
            COLLECTION_MODEL_NAMES[collection_name],
            data,
            output.context.contract_version,
        ),
    )
    changed_record = replace(original_record, data=data, metadata=metadata)
    changed_records = (changed_record, *records[1:])
    return replace(output, **{collection_name: changed_records})


def test_validator_accepts_exact_catalog_and_metadata_source_fields(
    business_factory: Any,
    accepted_factory: Any,
    output_factory: Any,
    phase5_processor: Phase5Processor,
) -> None:
    """정확한 6·5·4·9 필드와 metadata lineage는 정상 통과하는지 확인한다."""

    phase4_output, phase5_output = _valid_phase4_and_phase5(
        business_factory,
        accepted_factory,
        output_factory,
        phase5_processor,
    )

    Phase5ModelValidator.validate(phase4_output, phase5_output)

    assert (
        tuple(
            field_definition.name
            for field_definition in fields(phase5_output.join_references[0].data)
        )
        == EXPECTED_JOIN_FIELDS
    )
    assert phase5_output.parent_areas[0].metadata.source_record_ids == (1,)
    assert phase5_output.parent_areas[0].data.top_area_id not in {
        record.data.area_id for record in phase5_output.areas
    }


@pytest.mark.parametrize(
    "collection_name",
    ("employees", "areas", "parent_areas", "join_references"),
)
def test_duplicate_model_key_is_rejected_in_every_collection(
    collection_name: str,
    business_factory: Any,
    accepted_factory: Any,
    output_factory: Any,
    phase5_processor: Phase5Processor,
) -> None:
    """네 collection 각각에서 중복 metadata.model_key를 거부한다."""

    phase4_output, phase5_output = _valid_phase4_and_phase5(
        business_factory,
        accepted_factory,
        output_factory,
        phase5_processor,
    )
    record = getattr(phase5_output, collection_name)[0]
    malformed = replace(phase5_output, **{collection_name: (record, record)})

    with pytest.raises(Phase5ModelValidationError, match="must be unique"):
        Phase5ModelValidator.validate(phase4_output, malformed)


def test_join_reference_model_key_requires_structured_composite(
    business_factory: Any,
    accepted_factory: Any,
    output_factory: Any,
    phase5_processor: Phase5Processor,
) -> None:
    """join-reference metadata의 area 단독 문자열 키를 거부한다."""

    phase4_output, phase5_output = _valid_phase4_and_phase5(
        business_factory,
        accepted_factory,
        output_factory,
        phase5_processor,
    )
    join_record = phase5_output.join_references[0]
    metadata = replace(
        join_record.metadata,
        model_key=join_record.data.area_id,
    )
    malformed = replace(
        phase5_output,
        join_references=(replace(join_record, metadata=metadata),),
    )

    with pytest.raises(Phase5ModelValidationError, match="JoinReferenceKey"):
        Phase5ModelValidator.validate(phase4_output, malformed)


@pytest.mark.parametrize("empty_field", ("area_id", "employee_id"))
def test_join_reference_empty_key_component_is_rejected(
    empty_field: str,
    business_factory: Any,
    accepted_factory: Any,
    output_factory: Any,
    phase5_processor: Phase5Processor,
) -> None:
    """join-reference metadata 복합키의 빈 구성요소를 각각 거부한다."""

    phase4_output, phase5_output = _valid_phase4_and_phase5(
        business_factory,
        accepted_factory,
        output_factory,
        phase5_processor,
    )
    join_record = phase5_output.join_references[0]
    empty_key = replace(
        join_record.metadata.model_key,
        **{empty_field: ""},
    )
    metadata = replace(join_record.metadata, model_key=empty_key)
    malformed = replace(
        phase5_output,
        join_references=(replace(join_record, metadata=metadata),),
    )

    with pytest.raises(Phase5ModelValidationError, match=empty_field):
        Phase5ModelValidator.validate(phase4_output, malformed)


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("area_id", 1),
        ("area_id", None),
        ("employee_id", 1),
        ("employee_id", None),
    ),
)
def test_join_reference_wrong_type_key_component_is_rejected(
    field_name: str,
    invalid_value: object,
    business_factory: Any,
    accepted_factory: Any,
    output_factory: Any,
    phase5_processor: Phase5Processor,
) -> None:
    """Phase 7이 조인 복합키의 integer·None 구성요소를 각각 거부한다."""

    phase4_output, phase5_output = _valid_phase4_and_phase5(
        business_factory,
        accepted_factory,
        output_factory,
        phase5_processor,
    )
    join_record = phase5_output.join_references[0]
    invalid_key = replace(
        join_record.metadata.model_key,
        **{field_name: invalid_value},
    )
    metadata = replace(join_record.metadata, model_key=invalid_key)
    malformed = replace(
        phase5_output,
        join_references=(replace(join_record, metadata=metadata),),
    )

    with pytest.raises(
        Phase5ModelValidationError,
        match=(
            rf"metadata\.model_key\.{field_name} "
            "must be a non-empty str"
        ),
    ):
        Phase5ModelValidator.validate(phase4_output, malformed)


@pytest.mark.parametrize(
    "collection_name",
    ("employees", "areas", "parent_areas", "join_references"),
)
def test_wrong_data_field_shape_is_rejected_in_every_collection(
    collection_name: str,
    business_factory: Any,
    accepted_factory: Any,
    output_factory: Any,
    phase5_processor: Phase5Processor,
) -> None:
    """네 모델 모두 고정 catalog와 다른 data 필드 구성·순서를 거부한다."""

    phase4_output, phase5_output = _valid_phase4_and_phase5(
        business_factory,
        accepted_factory,
        output_factory,
        phase5_processor,
    )
    records = getattr(phase5_output, collection_name)
    wrong_record = replace(records[0], data=WrongShapeData(unexpected="value"))
    malformed = replace(
        phase5_output,
        **{collection_name: (wrong_record, *records[1:])},
    )

    with pytest.raises(Phase5ModelValidationError, match="fixed catalog"):
        Phase5ModelValidator.validate(phase4_output, malformed)


def test_forbidden_data_field_is_found_recursively(
    business_factory: Any,
    accepted_factory: Any,
    output_factory: Any,
    phase5_processor: Phase5Processor,
) -> None:
    """normal record.data 안쪽 mapping의 source 필드도 재귀적으로 거부한다."""

    phase4_output, phase5_output = _valid_phase4_and_phase5(
        business_factory,
        accepted_factory,
        output_factory,
        phase5_processor,
    )
    employee = phase5_output.employees[0]
    nested_source = {"nested": {"source_record_id": 1}}
    malformed_data = replace(employee.data, employee_name=nested_source)
    malformed = _replace_record_data(phase5_output, "employees", malformed_data)

    with pytest.raises(Phase5ModelValidationError, match="forbidden normal-data"):
        Phase5ModelValidator.validate(phase4_output, malformed)


def test_namedtuple_raw_field_bypass_is_rejected_after_fingerprint_recompute(
    business_factory: Any,
    accepted_factory: Any,
    output_factory: Any,
    phase5_processor: Phase5Processor,
) -> None:
    """직원·join 양쪽의 namedtuple raw 필드를 지문 재계산 뒤에도 거부한다."""

    phase4_output, phase5_output = _valid_phase4_and_phase5(
        business_factory,
        accepted_factory,
        output_factory,
        phase5_processor,
    )
    hidden_raw = RawPayloadTuple(raw_payload="secret")
    employee_data = replace(
        phase5_output.employees[0].data,
        employee_name=hidden_raw,
    )
    join_data = replace(
        phase5_output.join_references[0].data,
        employee_name=hidden_raw,
    )
    malformed = _replace_record_data(phase5_output, "employees", employee_data)
    malformed = _replace_record_data(malformed, "join_references", join_data)

    assert malformed.employees[0].metadata.model_fingerprint == (
        compute_model_fingerprint(
            "employee",
            employee_data,
            malformed.context.contract_version,
        )
    )
    assert malformed.join_references[0].metadata.model_fingerprint == (
        compute_model_fingerprint(
            "join_reference",
            join_data,
            malformed.context.contract_version,
        )
    )
    with pytest.raises(Phase5ModelValidationError, match="raw_payload"):
        Phase5ModelValidator.validate(phase4_output, malformed)


def test_unsupported_container_subclass_fails_closed(
    business_factory: Any,
    accepted_factory: Any,
    output_factory: Any,
    phase5_processor: Phase5Processor,
) -> None:
    """임의 속성을 숨길 수 있는 JSON 직렬화 container subclass를 거부한다."""

    phase4_output, phase5_output = _valid_phase4_and_phase5(
        business_factory,
        accepted_factory,
        output_factory,
        phase5_processor,
    )
    hidden_raw = HiddenAttributeList(["visible"])
    hidden_raw.raw_payload = "secret"
    employee_data = replace(
        phase5_output.employees[0].data,
        employee_name=hidden_raw,
    )
    join_data = replace(
        phase5_output.join_references[0].data,
        employee_name=hidden_raw,
    )
    malformed = _replace_record_data(phase5_output, "employees", employee_data)
    malformed = _replace_record_data(malformed, "join_references", join_data)

    with pytest.raises(Phase5ModelValidationError, match="unsupported nested"):
        Phase5ModelValidator.validate(phase4_output, malformed)


def test_fingerprint_function_exception_is_wrapped_with_original_cause(
    monkeypatch: pytest.MonkeyPatch,
    business_factory: Any,
    accepted_factory: Any,
    output_factory: Any,
    phase5_processor: Phase5Processor,
) -> None:
    """기존 지문 함수 자체 예외도 공개 Phase 7 오류와 cause로 보존한다."""

    phase4_output, phase5_output = _valid_phase4_and_phase5(
        business_factory,
        accepted_factory,
        output_factory,
        phase5_processor,
    )

    def raise_fingerprint_type_error(
        model_name: str,
        data: object,
        contract_version: str,
    ) -> str:
        """정상 출력의 지문 재계산 경로에서 의도한 TypeError를 발생시킨다."""

        del model_name, data, contract_version
        raise TypeError("forced fingerprint failure")

    monkeypatch.setattr(
        model_validator_module,
        "compute_model_fingerprint",
        raise_fingerprint_type_error,
    )

    with pytest.raises(Phase5ModelValidationError) as error_info:
        Phase5ModelValidator.validate(phase4_output, phase5_output)

    assert isinstance(error_info.value.__cause__, TypeError)
    assert str(error_info.value.__cause__) == "forced fingerprint failure"


@pytest.mark.parametrize(
    ("field_name", "changed_value"),
    (
        ("employee_name", "다른 직원 이름"),
        ("employee_department_name", "다른 부서"),
        ("employee_position_name", "다른 직급"),
        ("employee_hire_datetime", "2021-01-01T00:00:00"),
        ("employee_status_code", "INACTIVE"),
    ),
)
def test_join_employee_shared_value_mismatch_is_rejected(
    field_name: str,
    changed_value: str,
    business_factory: Any,
    accepted_factory: Any,
    output_factory: Any,
    phase5_processor: Phase5Processor,
) -> None:
    """join의 직원 공유 값 하나라도 employee 모델과 다르면 거부한다."""

    phase4_output, phase5_output = _valid_phase4_and_phase5(
        business_factory,
        accepted_factory,
        output_factory,
        phase5_processor,
    )
    join_data = replace(
        phase5_output.join_references[0].data,
        **{field_name: changed_value},
    )
    malformed = _replace_record_data(
        phase5_output,
        "join_references",
        join_data,
    )

    with pytest.raises(Phase5ModelValidationError, match="employee portion"):
        Phase5ModelValidator.validate(phase4_output, malformed)


def test_join_missing_employee_is_rejected(
    business_factory: Any,
    accepted_factory: Any,
    output_factory: Any,
    phase5_processor: Phase5Processor,
) -> None:
    """join.employee_id에 대응하는 employee 모델이 없으면 거부한다."""

    phase4_output, phase5_output = _valid_phase4_and_phase5(
        business_factory,
        accepted_factory,
        output_factory,
        phase5_processor,
    )
    join_data = replace(
        phase5_output.join_references[0].data,
        employee_id="EMP999999",
    )
    malformed = _replace_record_data(
        phase5_output,
        "join_references",
        join_data,
        model_key=JoinReferenceKey(
            area_id=join_data.area_id,
            employee_id=join_data.employee_id,
        ),
    )

    with pytest.raises(Phase5ModelValidationError, match="missing employee"):
        Phase5ModelValidator.validate(phase4_output, malformed)


def test_join_area_shared_value_mismatch_is_rejected(
    business_factory: Any,
    accepted_factory: Any,
    output_factory: Any,
    phase5_processor: Phase5Processor,
) -> None:
    """join의 parent ID가 대응 area 값과 다르면 거부한다."""

    phase4_output, phase5_output = _valid_phase4_and_phase5(
        business_factory,
        accepted_factory,
        output_factory,
        phase5_processor,
    )
    join_data = replace(
        phase5_output.join_references[0].data,
        parent_area_id="BIZ_00011",
    )
    malformed = _replace_record_data(
        phase5_output,
        "join_references",
        join_data,
    )

    with pytest.raises(Phase5ModelValidationError, match="area portion"):
        Phase5ModelValidator.validate(phase4_output, malformed)


def test_join_missing_area_is_rejected(
    business_factory: Any,
    accepted_factory: Any,
    output_factory: Any,
    phase5_processor: Phase5Processor,
) -> None:
    """join.area_id에 대응하는 area 모델이 없으면 거부한다."""

    phase4_output, phase5_output = _valid_phase4_and_phase5(
        business_factory,
        accepted_factory,
        output_factory,
        phase5_processor,
    )
    join_data = replace(
        phase5_output.join_references[0].data,
        area_id="BIZ_99999",
    )
    malformed = _replace_record_data(
        phase5_output,
        "join_references",
        join_data,
        model_key=JoinReferenceKey(
            area_id=join_data.area_id,
            employee_id=join_data.employee_id,
        ),
    )

    with pytest.raises(Phase5ModelValidationError, match="missing area"):
        Phase5ModelValidator.validate(phase4_output, malformed)


def test_parent_area_must_match_each_original_phase4_source(
    business_factory: Any,
    accepted_factory: Any,
    output_factory: Any,
    phase5_processor: Phase5Processor,
) -> None:
    """parent-area의 top 값이 원 Phase 4 accepted와 다르면 거부한다."""

    phase4_output, phase5_output = _valid_phase4_and_phase5(
        business_factory,
        accepted_factory,
        output_factory,
        phase5_processor,
    )
    parent_data = replace(
        phase5_output.parent_areas[0].data,
        top_area_name="변조된 최상위 이름",
    )
    malformed = _replace_record_data(
        phase5_output,
        "parent_areas",
        parent_data,
    )

    with pytest.raises(Phase5ModelValidationError, match="Phase 4 accepted"):
        Phase5ModelValidator.validate(phase4_output, malformed)


def test_model_fingerprint_mismatch_is_phase5_validation_error(
    business_factory: Any,
    accepted_factory: Any,
    output_factory: Any,
    phase5_processor: Phase5Processor,
) -> None:
    """저장 모델 지문 변조를 Phase4 오류가 아닌 공개 Phase 7 오류로 바꾼다."""

    phase4_output, phase5_output = _valid_phase4_and_phase5(
        business_factory,
        accepted_factory,
        output_factory,
        phase5_processor,
    )
    employee = phase5_output.employees[0]
    metadata = replace(employee.metadata, model_fingerprint="tampered")
    malformed = replace(
        phase5_output,
        employees=(replace(employee, metadata=metadata),),
    )

    with pytest.raises(Phase5ModelValidationError, match="fingerprint"):
        Phase5ModelValidator.validate(phase4_output, malformed)


@pytest.mark.parametrize("source_record_ids", ([], (), (True,), (1, 1)))
def test_malformed_source_metadata_fails_closed(
    source_record_ids: object,
    business_factory: Any,
    accepted_factory: Any,
    output_factory: Any,
    phase5_processor: Phase5Processor,
) -> None:
    """list·빈 tuple·bool·중복 source ID를 모두 Phase 7 오류로 거부한다."""

    phase4_output, phase5_output = _valid_phase4_and_phase5(
        business_factory,
        accepted_factory,
        output_factory,
        phase5_processor,
    )
    employee = phase5_output.employees[0]
    metadata = replace(employee.metadata, source_record_ids=source_record_ids)
    malformed = replace(
        phase5_output,
        employees=(replace(employee, metadata=metadata),),
    )

    with pytest.raises(Phase5ModelValidationError, match="source_record_ids"):
        Phase5ModelValidator.validate(phase4_output, malformed)


def test_unknown_source_record_id_is_rejected(
    business_factory: Any,
    accepted_factory: Any,
    output_factory: Any,
    phase5_processor: Phase5Processor,
) -> None:
    """원 Phase 4 accepted lineage에 없는 metadata source ID를 거부한다."""

    phase4_output, phase5_output = _valid_phase4_and_phase5(
        business_factory,
        accepted_factory,
        output_factory,
        phase5_processor,
    )
    employee = phase5_output.employees[0]
    metadata = replace(employee.metadata, source_record_ids=(999,))
    malformed = replace(
        phase5_output,
        employees=(replace(employee, metadata=metadata),),
    )

    with pytest.raises(Phase5ModelValidationError, match="accepted lineage"):
        Phase5ModelValidator.validate(phase4_output, malformed)


def test_canonical_model_key_and_collection_order_are_enforced(
    business_factory: Any,
    accepted_factory: Any,
    output_factory: Any,
    phase5_processor: Phase5Processor,
) -> None:
    """data key 불일치와 model_key 역순을 각각 결정성 위반으로 거부한다."""

    first_business, second_business = _distinct_businesses(business_factory)
    accepted = (
        accepted_factory(1, first_business),
        accepted_factory(2, second_business),
    )
    phase4_output = output_factory(accepted=accepted)
    phase5_output = phase5_processor.process(phase4_output)

    first_employee = phase5_output.employees[0]
    wrong_key_metadata = replace(first_employee.metadata, model_key="EMP999999")
    wrong_key_output = replace(
        phase5_output,
        employees=(
            replace(first_employee, metadata=wrong_key_metadata),
            *phase5_output.employees[1:],
        ),
    )
    with pytest.raises(Phase5ModelValidationError, match="must equal data"):
        Phase5ModelValidator.validate(phase4_output, wrong_key_output)

    reverse_output = replace(
        phase5_output,
        employees=tuple(reversed(phase5_output.employees)),
    )
    with pytest.raises(Phase5ModelValidationError, match="must be ordered"):
        Phase5ModelValidator.validate(phase4_output, reverse_output)


def test_source_id_order_and_model_counts_are_enforced(
    business_factory: Any,
    accepted_factory: Any,
    output_factory: Any,
    phase5_processor: Phase5Processor,
) -> None:
    """lineage ID 정렬과 실제 collection 기반 count를 결정적으로 고정한다."""

    business = business_factory()
    accepted = (
        accepted_factory(2, business),
        accepted_factory(1, business),
    )
    phase4_output = output_factory(accepted=accepted)
    phase5_output = phase5_processor.process(phase4_output)
    employee = phase5_output.employees[0]

    reversed_ids = replace(employee.metadata, source_record_ids=(2, 1))
    reversed_ids_output = replace(
        phase5_output,
        employees=(replace(employee, metadata=reversed_ids),),
    )
    with pytest.raises(Phase5ModelValidationError, match="sorted and unique"):
        Phase5ModelValidator.validate(phase4_output, reversed_ids_output)

    wrong_counts = replace(
        phase5_output.model_counts,
        employee=phase5_output.model_counts.employee + 1,
    )
    wrong_counts_output = replace(phase5_output, model_counts=wrong_counts)
    with pytest.raises(Phase5ModelValidationError, match="collection length"):
        Phase5ModelValidator.validate(phase4_output, wrong_counts_output)


def test_processor_and_standalone_validator_do_not_mutate_phase4_input(
    monkeypatch: pytest.MonkeyPatch,
    business_factory: Any,
    accepted_factory: Any,
    output_factory: Any,
    phase5_processor: Phase5Processor,
) -> None:
    """자동·직접 Phase 7 경로 모두 원 Phase 4 입력 snapshot을 보존한다."""

    accepted = accepted_factory(1, business_factory())
    phase4_output = output_factory(accepted=(accepted,))
    phase4_snapshot = deepcopy(phase4_output)
    original_validate = Phase5ModelValidator.validate
    observed_calls: list[tuple[object, Phase5Output]] = []

    def validating_spy(
        observed_phase4: object,
        observed_phase5: Phase5Output,
    ) -> None:
        """processor의 자동 호출을 기록한 뒤 실제 validator를 실행한다."""

        observed_calls.append((observed_phase4, observed_phase5))
        original_validate(observed_phase4, observed_phase5)

    monkeypatch.setattr(Phase5ModelValidator, "validate", validating_spy)

    phase5_output = phase5_processor.process(phase4_output)

    assert observed_calls == [(phase4_output, phase5_output)]
    assert phase4_output == phase4_snapshot
    assert phase5_output.context is phase4_output.context

    original_validate(phase4_output, phase5_output)
    assert phase4_output == phase4_snapshot


def test_reversed_accepted_order_produces_identical_full_phase5_output(
    business_factory: Any,
    accepted_factory: Any,
    output_factory: Any,
    phase5_processor: Phase5Processor,
) -> None:
    """accepted 순서를 뒤집어 재처리해도 전체 Phase5Output이 동일하다."""

    first_business, second_business = _distinct_businesses(business_factory)
    accepted = (
        accepted_factory(1, first_business),
        accepted_factory(2, second_business),
    )
    forward_input = output_factory(accepted=accepted)
    reverse_input = output_factory(accepted=tuple(reversed(accepted)))

    forward_output = phase5_processor.process(forward_input)
    reverse_output = phase5_processor.process(reverse_input)

    assert forward_output == reverse_output
