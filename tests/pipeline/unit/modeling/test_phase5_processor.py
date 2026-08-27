"""Phase5Processor의 Phase 4 guard부터 Phase 6 조립까지 전체 흐름을 검증한다."""

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from src.silver.contracts.phase5 import (
    JoinReferenceKey,
    ModelCounts,
    Phase5Output,
)
from src.silver.modeling import Phase5Processor
from src.silver.modeling.assembly import compute_model_fingerprint


def test_process_returns_deduplicated_phase5_output(
    business_factory: Callable[..., Any],
    accepted_factory: Callable[..., Any],
    rejected_factory: Callable[..., Any],
    output_factory: Callable[..., Any],
    phase5_processor: Phase5Processor,
) -> None:
    """전체 processor가 identity와 dedup counts를 보존한 최종 출력을 반환한다."""

    first_business = business_factory()
    second_business = replace(
        first_business,
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
    accepted = (
        accepted_factory(3, second_business),
        accepted_factory(2, first_business),
        accepted_factory(1, first_business),
    )
    rejected = (
        rejected_factory(None, batch_record_index=3),
        rejected_factory(99, batch_record_index=4),
    )
    phase4_output = output_factory(
        accepted=accepted,
        rejected=rejected,
        excluded_count=1,
        contract_version="contract-v7",
    )

    result = phase5_processor.process(phase4_output)

    assert isinstance(result, Phase5Output)
    assert result.context is phase4_output.context
    assert result.rejected is phase4_output.rejected
    assert result.source_metrics is phase4_output.metrics
    assert result.model_counts == ModelCounts(
        employee=2,
        area=2,
        parent_area=1,
        join_reference=2,
    )
    assert tuple(record.metadata.model_key for record in result.employees) == (
        "EMP000001",
        "EMP000002",
    )
    assert tuple(record.metadata.model_key for record in result.areas) == (
        "BIZ_00001",
        "BIZ_00002",
    )
    assert tuple(record.metadata.model_key for record in result.join_references) == (
        JoinReferenceKey("BIZ_00001", "EMP000001"),
        JoinReferenceKey("BIZ_00002", "EMP000002"),
    )
    assert result.employees[0].metadata.source_record_ids == (1, 2)
    assert result.parent_areas[0].metadata.source_record_ids == (1, 2, 3)
    assert phase4_output.metrics.input_count == 6
    assert phase4_output.metrics.accepted_count == 3
    assert phase4_output.metrics.rejected_count == 2
    assert phase4_output.metrics.excluded_count == 1

    for record in result.employees:
        expected_fingerprint = compute_model_fingerprint(
            "employee",
            record.data,
            "contract-v7",
        )
        assert record.metadata.model_fingerprint == expected_fingerprint


def test_process_model_output_is_independent_of_accepted_input_order(
    business_factory: Callable[..., Any],
    accepted_factory: Callable[..., Any],
    output_factory: Callable[..., Any],
    phase5_processor: Phase5Processor,
) -> None:
    """accepted 입력 순서를 뒤집어도 네 최종 model collection이 같은지 확인한다."""

    first_business = business_factory()
    second_business = replace(
        first_business,
        area_id="BIZ_00002",
        area_name="재무본부",
        employee_id="EMP000002",
        employee_name="박재무",
        employee_department_name="재무팀",
    )
    accepted = (
        accepted_factory(1, first_business),
        accepted_factory(2, second_business),
    )
    forward_input = output_factory(accepted=accepted)
    reverse_input = output_factory(accepted=tuple(reversed(accepted)))

    forward = phase5_processor.process(forward_input)
    reverse = phase5_processor.process(reverse_input)

    assert forward.employees == reverse.employees
    assert forward.areas == reverse.areas
    assert forward.parent_areas == reverse.parent_areas
    assert forward.join_references == reverse.join_references
    assert forward.model_counts == reverse.model_counts
