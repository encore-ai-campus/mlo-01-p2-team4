"""Phase 5가 Phase 4 계약 위반을 repair 없이 배치 실패시키는지 검증한다."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

import pytest

from src.silver.modeling import (
    Phase4ContractViolation,
    Phase4IntegrationUnavailable,
    Phase5Processor,
)


@dataclass(frozen=True, slots=True)
class MissingFieldsBusiness:
    """잠긴 15-field business 계약을 의도적으로 만족하지 않는 객체다."""

    area_id: str


def test_default_binding_fails_closed(
    output_factory: Callable[..., Any],
) -> None:
    """실제 Plan 1 binding이 없으면 첫 lock 검증에서 즉시 중단한다."""

    output = output_factory(accepted=())

    with pytest.raises(Phase4IntegrationUnavailable, match="contract-lock"):
        Phase5Processor().process(output)


def test_unexpected_phase4_output_type_is_rejected(
    phase5_processor: Phase5Processor,
) -> None:
    """binding이 실제 Phase4Output이 아닌 최상위 객체를 거부한다."""

    with pytest.raises(Phase4ContractViolation, match="Phase4Output type"):
        phase5_processor.process(object())


def test_contract_lock_checksum_mismatch_is_rejected(
    output_factory: Callable[..., Any],
    phase5_processor: Phase5Processor,
) -> None:
    """별도 contract lock checksum을 정상 계약으로 받아들이지 않는다."""

    output = output_factory(accepted=(), lock_checksum="different-checksum")

    with pytest.raises(Phase4ContractViolation, match="checksum mismatch"):
        phase5_processor.process(output)


def test_context_and_metrics_accounting_mismatch_is_rejected(
    output_factory: Callable[..., Any],
    phase5_processor: Phase5Processor,
) -> None:
    """context와 source metrics count가 다르면 배치 전체를 실패시킨다."""

    output = output_factory(accepted=())
    malformed_metrics = replace(output.metrics, input_count=1)
    malformed_output = replace(output, metrics=malformed_metrics)

    with pytest.raises(Phase4ContractViolation, match="metrics input"):
        phase5_processor.process(malformed_output)


def test_non_tuple_phase4_collections_are_rejected(
    output_factory: Callable[..., Any],
    phase5_processor: Phase5Processor,
) -> None:
    """accepted collection이 list이면 공유 tuple 계약 위반으로 실패한다."""

    output = output_factory(accepted=())
    malformed_output = replace(output, accepted=[])

    with pytest.raises(Phase4ContractViolation, match="collections must be tuples"):
        phase5_processor.process(malformed_output)


def test_accepted_rejected_record_id_intersection_is_rejected(
    business_factory: Callable[..., Any],
    accepted_factory: Callable[..., Any],
    rejected_factory: Callable[..., Any],
    output_factory: Callable[..., Any],
    phase5_processor: Phase5Processor,
) -> None:
    """같은 non-None record ID가 accepted와 rejected에 함께 있으면 실패한다."""

    accepted = accepted_factory(10, business_factory())
    rejected = rejected_factory(10, batch_record_index=1)
    output = output_factory(accepted=(accepted,), rejected=(rejected,))

    with pytest.raises(Phase4ContractViolation, match="intersection"):
        phase5_processor.process(output)


def test_rejected_none_record_id_is_preserved(
    business_factory: Callable[..., Any],
    accepted_factory: Callable[..., Any],
    rejected_factory: Callable[..., Any],
    output_factory: Callable[..., Any],
    phase5_processor: Phase5Processor,
) -> None:
    """malformed 원천을 나타내는 rejected None ID를 추정하거나 거부하지 않는다."""

    accepted = accepted_factory(10, business_factory())
    rejected = rejected_factory(None, batch_record_index=1)
    output = output_factory(accepted=(accepted,), rejected=(rejected,))

    result = phase5_processor.process(output)

    assert result.rejected is output.rejected
    assert result.rejected[0].observed_lineage.record_id is None


def test_record_fingerprint_callback_mismatch_is_rejected(
    business_factory: Callable[..., Any],
    accepted_factory: Callable[..., Any],
    output_factory: Callable[..., Any],
    phase5_processor: Phase5Processor,
) -> None:
    """저장 지문과 Plan 1 callback 재계산 값이 다르면 tampering으로 실패한다."""

    accepted = accepted_factory(
        10,
        business_factory(),
        stored_fingerprint="stored",
        canonical_fingerprint="recomputed",
    )
    output = output_factory(accepted=(accepted,))

    with pytest.raises(Phase4ContractViolation, match="does not match"):
        phase5_processor.process(output)


def test_missing_locked_business_fields_are_rejected(
    accepted_factory: Callable[..., Any],
    output_factory: Callable[..., Any],
    phase5_processor: Phase5Processor,
) -> None:
    """15개 표준 필드 중 누락이 있으면 projection 전에 실패한다."""

    accepted = accepted_factory(10, MissingFieldsBusiness(area_id="BIZ_00001"))
    output = output_factory(accepted=(accepted,))

    with pytest.raises(Phase4ContractViolation, match="locked 15-field"):
        phase5_processor.process(output)


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("area_id", "BIZ_１２３４５"),
        ("employee_id", "EMP٠١٢٣٤٥"),
        ("employee_status_code", "active"),
        ("top_area_level_code", "level-code"),
        ("employee_name", ""),
        ("employee_department_name", "x" * 101),
        ("employee_hire_datetime", "2026-01-02T03:04:60"),
        ("area_registration_date", "2026-01-02T03:04:61"),
    ),
)
def test_explicit_canonical_domain_breaches_are_rejected(
    field_name: str,
    invalid_value: str,
    business_factory: Callable[..., Any],
    accepted_factory: Callable[..., Any],
    output_factory: Callable[..., Any],
    phase5_processor: Phase5Processor,
) -> None:
    """ASCII ID·status·naive ISO seconds domain 위반을 repair 없이 거부한다."""

    business = business_factory(**{field_name: invalid_value})
    accepted = accepted_factory(10, business)
    output = output_factory(accepted=(accepted,))

    with pytest.raises(Phase4ContractViolation):
        phase5_processor.process(output)


@pytest.mark.parametrize(
    "field_name",
    (
        "area_id",
        "area_name",
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
    ),
)
def test_non_nullable_business_fields_reject_none(
    field_name: str,
    business_factory: Callable[..., Any],
    accepted_factory: Callable[..., Any],
    output_factory: Callable[..., Any],
    phase5_processor: Phase5Processor,
) -> None:
    """표준상 필수인 13개 business 필드는 None을 허용하지 않는다."""

    business = business_factory(**{field_name: None})
    accepted = accepted_factory(10, business)
    output = output_factory(accepted=(accepted,))

    with pytest.raises(Phase4ContractViolation, match=field_name):
        phase5_processor.process(output)
