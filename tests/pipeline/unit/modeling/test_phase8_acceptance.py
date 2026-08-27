"""실제 Plan 1 결합 전, 현재 합성 fixture로 Phase 8 인수 경계를 검증한다.

이 모듈의 결과는 fixture 수준 증거이며 실제 Plan 1 계약·runtime 통합 증거가
아니다.
"""

import json
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest

from src.silver.contracts.phase5 import (
    JoinReferenceKey,
    ModelCounts,
    Phase5Output,
)
from src.silver.modeling import (
    Phase4ContractViolation,
    Phase4IntegrationBinding,
    Phase5Processor,
)

PROJECT_ROOT = Path(__file__).resolve().parents[4]
GOLDEN_OUTPUT_PATH = (
    PROJECT_ROOT / "standards" / "silver" / "v1" / "examples" / "phase5-output.json"
)
MODEL_COLLECTION_NAMES = (
    "employees",
    "areas",
    "parent_areas",
    "join_references",
)


def _canonical_phase5_bytes(output: Phase5Output) -> bytes:
    """Phase5Output을 테스트 전용 canonical JSON bytes로 직렬화한다.

    Args:
        output: 비교할 불변 Phase5Output 객체.

    Returns:
        dataclass 전체를 compact sorted-key UTF-8 JSON으로 만든 bytes.
    """

    payload = asdict(output)
    canonical_json = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return canonical_json.encode("utf-8")


def _load_golden_output() -> dict[str, Any]:
    """승인된 Phase 5 golden JSON을 읽는다.

    Returns:
        golden 문서의 최상위 JSON mapping.
    """

    with GOLDEN_OUTPUT_PATH.open(encoding="utf-8") as golden_file:
        return json.load(golden_file)


def _assert_required_projection_values(
    output: Phase5Output,
    business: Any,
) -> None:
    """nullable 변형 중에도 네 모델의 필수 값이 유지되는지 확인한다.

    Args:
        output: nullable 값을 투영한 최종 Phase5Output.
        business: 해당 출력의 원천 합성 business 객체.
    """

    assert output.employees[0].data.employee_id == business.employee_id
    assert output.areas[0].data.area_id == business.area_id
    assert output.areas[0].data.area_name == business.area_name
    assert output.areas[0].data.employee_id == business.employee_id
    assert output.parent_areas[0].data.top_area_id == business.top_area_id
    assert output.parent_areas[0].data.top_area_name == business.top_area_name
    assert output.join_references[0].data.area_id == business.area_id
    assert output.join_references[0].data.employee_id == business.employee_id
    assert output.join_references[0].metadata.model_key == JoinReferenceKey(
        area_id=business.area_id,
        employee_id=business.employee_id,
    )


@pytest.fixture
def golden_phase4_output(
    business_factory: Callable[..., Any],
    accepted_factory: Callable[..., Any],
    output_factory: Callable[..., Any],
) -> Any:
    """golden 모델과 일치하는 동일 accepted 두 건의 합성 입력을 제공한다.

    Returns:
        record ID 101·102와 contract version 1을 가진 합성 Phase4Output.
    """

    business = business_factory(
        area_id="BIZ_00001",
        area_name="인사본부",
        parent_area_id=None,
        parent_area_name=None,
        top_area_id="BIZ_00001",
        top_area_name="인사본부",
        top_area_level_code="1",
        employee_id="EMP000001",
        employee_name="김은서",
        employee_department_name="인사팀",
        employee_position_name="팀장",
        employee_hire_datetime="2020-01-02T09:00:00",
        employee_status_code="ACTIVE",
        area_registration_date="2024-01-01T00:00:00",
        top_area_registration_date="2024-01-01T00:00:00",
    )
    accepted = (
        accepted_factory(101, business),
        accepted_factory(102, business),
    )
    return output_factory(accepted=accepted, contract_version="1")


def test_accepted_fixture_matches_approved_golden_model_collections(
    golden_phase4_output: Any,
    phase5_processor: Phase5Processor,
) -> None:
    """동일 accepted 두 건의 네 모델 JSON이 승인 golden과 정확히 같은지 본다."""

    output = phase5_processor.process(golden_phase4_output)
    actual_json = json.loads(_canonical_phase5_bytes(output))
    golden_json = _load_golden_output()

    for collection_name in MODEL_COLLECTION_NAMES:
        assert actual_json[collection_name] == golden_json[collection_name]


def test_fully_rejected_input_preserves_passthrough_and_empty_models(
    rejected_factory: Callable[..., Any],
    output_factory: Callable[..., Any],
    phase5_processor: Phase5Processor,
) -> None:
    """전부 Reject인 입력의 원본 전달 객체와 빈 모델 accounting을 보존한다."""

    rejected = (
        rejected_factory(902, batch_record_index=2),
        rejected_factory(None, batch_record_index=1),
    )
    phase4_output = output_factory(accepted=(), rejected=rejected)
    context_value = asdict(phase4_output.context)
    metrics_value = asdict(phase4_output.metrics)

    output = phase5_processor.process(phase4_output)

    assert output.employees == ()
    assert output.areas == ()
    assert output.parent_areas == ()
    assert output.join_references == ()
    assert output.model_counts == ModelCounts(0, 0, 0, 0)
    assert output.context is phase4_output.context
    assert output.rejected is phase4_output.rejected
    assert output.source_metrics is phase4_output.metrics
    assert output.rejected == rejected
    assert tuple(record.marker for record in output.rejected) == (
        "rejected-2",
        "rejected-1",
    )
    assert asdict(output.context) == context_value
    assert asdict(output.source_metrics) == metrics_value


def test_duplicate_accepted_sources_merge_in_all_four_models(
    golden_phase4_output: Any,
    phase5_processor: Phase5Processor,
) -> None:
    """동일 business의 두 source가 모델별 한 건과 정렬된 고유 ID로 병합된다."""

    output = phase5_processor.process(golden_phase4_output)

    assert output.model_counts == ModelCounts(1, 1, 1, 1)
    for collection_name in MODEL_COLLECTION_NAMES:
        collection = getattr(output, collection_name)
        assert len(collection) == 1
        assert collection[0].metadata.source_record_ids == (101, 102)


def test_reversed_accepted_input_has_identical_canonical_output_bytes(
    golden_phase4_output: Any,
    phase5_processor: Phase5Processor,
) -> None:
    """accepted 순서를 뒤집어도 전체 Phase5Output canonical bytes가 같다."""

    reversed_input = replace(
        golden_phase4_output,
        accepted=tuple(reversed(golden_phase4_output.accepted)),
    )

    forward_output = phase5_processor.process(golden_phase4_output)
    reversed_output = phase5_processor.process(reversed_input)

    assert _canonical_phase5_bytes(forward_output) == _canonical_phase5_bytes(
        reversed_output
    )


def test_accounting_mismatch_fails_without_repair(
    golden_phase4_output: Any,
    phase5_processor: Phase5Processor,
) -> None:
    """metrics accounting이 어긋난 합성 입력을 고치지 않고 계약 위반으로 낸다."""

    malformed_metrics = replace(golden_phase4_output.metrics, input_count=999)
    malformed_output = replace(golden_phase4_output, metrics=malformed_metrics)
    original_value = asdict(malformed_output)

    with pytest.raises(Phase4ContractViolation, match="metrics input accounting"):
        phase5_processor.process(malformed_output)

    assert asdict(malformed_output) == original_value


def test_non_tuple_collection_fails_without_coercion(
    golden_phase4_output: Any,
    phase5_processor: Phase5Processor,
) -> None:
    """list accepted를 tuple로 보정하지 않고 Phase 4 계약 위반으로 낸다."""

    malformed_accepted = list(golden_phase4_output.accepted)
    malformed_output = replace(golden_phase4_output, accepted=malformed_accepted)

    with pytest.raises(Phase4ContractViolation, match="collections must be tuples"):
        phase5_processor.process(malformed_output)

    assert malformed_output.accepted is malformed_accepted


def test_same_model_key_with_different_projection_fails_closed(
    business_factory: Callable[..., Any],
    accepted_factory: Callable[..., Any],
    output_factory: Callable[..., Any],
    phase5_processor: Phase5Processor,
) -> None:
    """같은 employee key의 다른 투영을 선택·Reject 전환 없이 실패시킨다."""

    first_business = business_factory()
    conflicting_business = replace(first_business, employee_name="다른 직원 이름")
    accepted = (
        accepted_factory(101, first_business),
        accepted_factory(102, conflicting_business),
    )
    phase4_output = output_factory(accepted=accepted)
    original_accepted = phase4_output.accepted
    original_rejected = phase4_output.rejected

    with pytest.raises(
        Phase4ContractViolation,
        match="employee projection conflict",
    ):
        phase5_processor.process(phase4_output)

    assert phase4_output.accepted is original_accepted
    assert phase4_output.rejected is original_rejected
    assert phase4_output.rejected == ()


def test_same_join_composite_key_with_different_projection_fails_closed(
    business_factory: Callable[..., Any],
    accepted_factory: Callable[..., Any],
    output_factory: Callable[..., Any],
    phase5_processor: Phase5Processor,
) -> None:
    """동일 조인 복합키의 상충 join 전용 payload를 실패로 처리한다."""

    first_business = business_factory()
    conflicting_business = replace(
        first_business,
        parent_area_name="다른 직접 부모 이름",
    )
    accepted = (
        accepted_factory(101, first_business),
        accepted_factory(102, conflicting_business),
    )
    phase4_output = output_factory(accepted=accepted)

    with pytest.raises(
        Phase4ContractViolation,
        match="join_reference projection conflict",
    ):
        phase5_processor.process(phase4_output)


def test_same_area_different_employee_stops_at_unchanged_area_conflict(
    business_factory: Callable[..., Any],
    accepted_factory: Callable[..., Any],
    output_factory: Callable[..., Any],
    phase5_processor: Phase5Processor,
) -> None:
    """동일 area·다른 employee 입력은 기존 area 충돌 경계에서 중단한다."""

    first_business = business_factory()
    second_business = replace(
        first_business,
        employee_id="EMP000002",
    )
    accepted = (
        accepted_factory(101, first_business),
        accepted_factory(102, second_business),
    )
    phase4_output = output_factory(accepted=accepted)

    with pytest.raises(
        Phase4ContractViolation,
        match=r"^area projection conflict",
    ) as error_info:
        phase5_processor.process(phase4_output)

    assert "join_reference projection conflict" not in str(error_info.value)


@pytest.mark.parametrize(
    ("business_field", "collection_name", "model_field"),
    (
        ("parent_area_id", "areas", "parent_area_id"),
        ("parent_area_name", "join_references", "parent_area_name"),
    ),
)
def test_allowed_nullable_fields_preserve_none_in_each_model_target(
    business_field: str,
    collection_name: str,
    model_field: str,
    business_factory: Callable[..., Any],
    accepted_factory: Callable[..., Any],
    output_factory: Callable[..., Any],
    phase5_processor: Phase5Processor,
) -> None:
    """각 모델 대상의 허용 nullable 값은 None 그대로이며 필수 값은 유지된다."""

    business = business_factory(**{business_field: None})
    accepted = accepted_factory(101, business)
    phase4_output = output_factory(accepted=(accepted,))

    output = phase5_processor.process(phase4_output)

    collection = getattr(output, collection_name)
    assert getattr(collection[0].data, model_field) is None
    _assert_required_projection_values(output, business)


def test_one_thousand_records_are_deterministic_without_timing_assumption(
    business_factory: Callable[..., Any],
    accepted_factory: Callable[..., Any],
    output_factory: Callable[..., Any],
    phase5_processor: Phase5Processor,
) -> None:
    """합성 1,000건을 양방향 처리해 accounting과 결정성을 검증한다."""

    accepted_records = []
    for record_id in range(1, 1_001):
        area_id = f"BIZ_{record_id:05d}"
        area_name = f"합성 부서 {record_id}"
        employee_id = f"EMP{record_id:06d}"
        business = business_factory(
            area_id=area_id,
            area_name=area_name,
            parent_area_id=None,
            parent_area_name=None,
            top_area_id=area_id,
            top_area_name=area_name,
            top_area_level_code="1",
            employee_id=employee_id,
            employee_name=f"합성 직원 {record_id}",
        )
        accepted_records.append(accepted_factory(record_id, business))

    accepted = tuple(accepted_records)
    forward_input = output_factory(accepted=accepted)
    reversed_input = output_factory(accepted=tuple(reversed(accepted)))

    forward_output = phase5_processor.process(forward_input)
    reversed_output = phase5_processor.process(reversed_input)

    assert forward_output.source_metrics.input_count == 1_000
    assert forward_output.source_metrics.accepted_count == 1_000
    assert forward_output.source_metrics.rejected_count == 0
    assert forward_output.source_metrics.excluded_count == 0
    assert forward_output.model_counts == ModelCounts(1_000, 1_000, 1_000, 1_000)
    assert forward_output.employees[0].metadata.source_record_ids == (1,)
    assert forward_output.employees[-1].metadata.source_record_ids == (1_000,)
    assert _canonical_phase5_bytes(forward_output) == _canonical_phase5_bytes(
        reversed_output
    )


def test_public_processor_accepts_injected_phase4_object_directly(
    golden_phase4_output: Any,
    phase4_binding: Phase4IntegrationBinding,
) -> None:
    """주입 binding의 processor 공개 API에 합성 Phase 4 객체를 직접 전달한다."""

    processor = Phase5Processor(binding=phase4_binding)
    original_value = asdict(golden_phase4_output)

    output = processor.process(golden_phase4_output)

    assert isinstance(output, Phase5Output)
    assert output.context is golden_phase4_output.context
    assert output.source_metrics is golden_phase4_output.metrics
    assert asdict(golden_phase4_output) == original_value
