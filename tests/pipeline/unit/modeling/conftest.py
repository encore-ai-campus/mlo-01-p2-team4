"""Phase 5/6 격리 테스트에 필요한 최소 합성 Phase 4 경계를 제공한다."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TypeAlias

import pytest

from src.silver.modeling import (
    Phase4ContractViolation,
    Phase4IntegrationBinding,
    Phase5Processor,
)

EXPECTED_LOCK_VERSION = "phase4-lock-v1"
EXPECTED_LOCK_CHECKSUM = "phase4-lock-checksum"


@dataclass(frozen=True, slots=True)
class SyntheticContext:
    """합성 lock·contract version과 배치 accounting 기준을 보존한다."""

    contract_version: str
    lock_version: str
    lock_checksum: str
    input_count: int
    accepted_count: int
    rejected_count: int
    excluded_count: int


@dataclass(frozen=True, slots=True)
class SyntheticMetrics:
    """합성 Phase 4 source accounting 결과를 보존한다."""

    contract_version: str
    input_count: int
    accepted_count: int
    rejected_count: int
    excluded_count: int


@dataclass(frozen=True, slots=True)
class SyntheticLineage:
    """accepted record의 필수 record ID를 제공한다."""

    record_id: int


@dataclass(frozen=True, slots=True)
class SyntheticObservedLineage:
    """rejected record의 관찰 가능한 선택적 record ID를 제공한다."""

    batch_record_index: int
    record_id: int | None


@dataclass(frozen=True, slots=True)
class SyntheticRejected:
    """Phase 5가 그대로 전달해야 하는 합성 rejected record다."""

    observed_lineage: SyntheticObservedLineage
    marker: str


@dataclass(frozen=True, slots=True)
class SyntheticBusiness:
    """잠긴 순서를 그대로 따르는 15개 표준 business 필드다."""

    area_id: str
    area_name: str
    parent_area_id: str | None
    parent_area_name: str | None
    top_area_id: str
    top_area_name: str
    top_area_level_code: str
    employee_id: str
    employee_name: str
    employee_department_name: str
    employee_position_name: str
    employee_hire_datetime: str
    employee_status_code: str
    area_registration_date: str
    top_area_registration_date: str


@dataclass(frozen=True, slots=True)
class SyntheticAccepted:
    """합성 StandardizedRecord의 lineage·business·지문을 제공한다."""

    lineage: SyntheticLineage
    business: SyntheticBusiness
    record_fingerprint: str
    canonical_fingerprint: str


@dataclass(frozen=True, slots=True)
class SyntheticPhase4Output:
    """실제 Phase4Output과 같은 최상위 전달 속성만 제공한다."""

    context: SyntheticContext
    accepted: tuple[SyntheticAccepted, ...]
    rejected: tuple[SyntheticRejected, ...]
    metrics: SyntheticMetrics


BusinessFactory: TypeAlias = Callable[..., SyntheticBusiness]
AcceptedFactory: TypeAlias = Callable[..., SyntheticAccepted]
RejectedFactory: TypeAlias = Callable[..., SyntheticRejected]
OutputFactory: TypeAlias = Callable[..., SyntheticPhase4Output]


def _make_business(**overrides: object) -> SyntheticBusiness:
    """정상 canonical 값에서 필요한 필드만 바꾼 합성 business를 만든다.

    Args:
        **overrides: 기본 business에서 교체할 필드와 값.

    Returns:
        정확히 15개 필드를 가진 frozen 합성 business.
    """

    business = SyntheticBusiness(
        area_id="BIZ_00001",
        area_name="인사본부",
        parent_area_id="BIZ_00010",
        parent_area_name="경영지원",
        top_area_id="BIZ_00099",
        top_area_name="본사",
        top_area_level_code="TOP_LEVEL",
        employee_id="EMP000001",
        employee_name="김은서",
        employee_department_name="인사팀",
        employee_position_name="팀장",
        employee_hire_datetime="2020-01-02T09:00:00",
        employee_status_code="ACTIVE",
        area_registration_date="2024-01-01T00:00:00",
        top_area_registration_date="2010-01-01T00:00:00",
    )
    return replace(business, **overrides)


def _make_accepted(
    record_id: int,
    business: SyntheticBusiness,
    stored_fingerprint: str | None = None,
    canonical_fingerprint: str | None = None,
) -> SyntheticAccepted:
    """저장·재계산 지문을 독립 지정할 수 있는 accepted record를 만든다.

    Args:
        record_id: 합성 accepted lineage의 record ID.
        business: 투영할 합성 표준 business.
        stored_fingerprint: Phase 4가 저장한 지문. 생략 시 정상 기본값.
        canonical_fingerprint: 공유 callback 재계산 값. 생략 시 저장값과 동일.

    Returns:
        Phase 5 입력 경계를 만족하는 합성 accepted record.
    """

    if stored_fingerprint is None:
        stored_fingerprint = f"phase4-record-{record_id}"
    if canonical_fingerprint is None:
        canonical_fingerprint = stored_fingerprint
    return SyntheticAccepted(
        lineage=SyntheticLineage(record_id=record_id),
        business=business,
        record_fingerprint=stored_fingerprint,
        canonical_fingerprint=canonical_fingerprint,
    )


def _make_rejected(
    record_id: int | None,
    batch_record_index: int = 0,
) -> SyntheticRejected:
    """관찰 ID가 없을 수도 있는 합성 rejected record를 만든다.

    Args:
        record_id: 관찰 가능한 원천 record ID 또는 정상적인 None.
        batch_record_index: 배치 안에서의 원천 위치.

    Returns:
        그대로 전달 여부를 확인할 합성 rejected record.
    """

    return SyntheticRejected(
        observed_lineage=SyntheticObservedLineage(
            batch_record_index=batch_record_index,
            record_id=record_id,
        ),
        marker=f"rejected-{batch_record_index}",
    )


def _make_output(
    accepted: tuple[SyntheticAccepted, ...],
    rejected: tuple[SyntheticRejected, ...] = (),
    excluded_count: int = 0,
    contract_version: str = "phase4-contract-v1",
    lock_version: str = EXPECTED_LOCK_VERSION,
    lock_checksum: str = EXPECTED_LOCK_CHECKSUM,
) -> SyntheticPhase4Output:
    """context와 metrics accounting이 일치하는 합성 Phase4Output을 만든다.

    Args:
        accepted: 합성 accepted records.
        rejected: 합성 rejected records.
        excluded_count: Phase 4에서 accounting된 제외 행 수.
        contract_version: context와 metrics가 공유할 계약 버전.
        lock_version: 공유 contract lock 버전.
        lock_checksum: 공유 contract lock checksum.

    Returns:
        입력 행 accounting이 닫힌 합성 Phase4Output.
    """

    accepted_count = len(accepted)
    rejected_count = len(rejected)
    input_count = accepted_count + rejected_count + excluded_count
    context = SyntheticContext(
        contract_version=contract_version,
        lock_version=lock_version,
        lock_checksum=lock_checksum,
        input_count=input_count,
        accepted_count=accepted_count,
        rejected_count=rejected_count,
        excluded_count=excluded_count,
    )
    metrics = SyntheticMetrics(
        contract_version=contract_version,
        input_count=input_count,
        accepted_count=accepted_count,
        rejected_count=rejected_count,
        excluded_count=excluded_count,
    )
    return SyntheticPhase4Output(
        context=context,
        accepted=accepted,
        rejected=rejected,
        metrics=metrics,
    )


def _verify_lock(output: object) -> None:
    """합성 Phase 4 타입과 lock version·checksum을 fail closed로 검증한다.

    Args:
        output: 검증할 실제 processor 입력 객체.

    Raises:
        Phase4ContractViolation: 타입 또는 lock 값이 잠긴 기준과 다른 경우.
    """

    if not isinstance(output, SyntheticPhase4Output):
        raise Phase4ContractViolation("unexpected Phase4Output type")
    if output.context.lock_version != EXPECTED_LOCK_VERSION:
        raise Phase4ContractViolation("contract lock version mismatch")
    if output.context.lock_checksum != EXPECTED_LOCK_CHECKSUM:
        raise Phase4ContractViolation("contract lock checksum mismatch")


def _verify_output(output: object) -> None:
    """합성 context·metrics version과 행 accounting을 검증한다.

    Args:
        output: lock 검증을 통과한 합성 Phase4Output.

    Raises:
        Phase4ContractViolation: collection, version 또는 accounting이 다른 경우.
    """

    if not isinstance(output, SyntheticPhase4Output):
        raise Phase4ContractViolation("unexpected Phase4Output type")
    if not isinstance(output.accepted, tuple) or not isinstance(output.rejected, tuple):
        raise Phase4ContractViolation("Phase4Output collections must be tuples")

    context = output.context
    metrics = output.metrics
    observed_input_count = len(output.accepted) + len(output.rejected)
    observed_input_count += metrics.excluded_count
    if context.contract_version != metrics.contract_version:
        raise Phase4ContractViolation("contract version mismatch")
    if context.input_count != observed_input_count:
        raise Phase4ContractViolation("context input accounting mismatch")
    if context.accepted_count != len(output.accepted):
        raise Phase4ContractViolation("context accepted accounting mismatch")
    if context.rejected_count != len(output.rejected):
        raise Phase4ContractViolation("context rejected accounting mismatch")
    if context.excluded_count != metrics.excluded_count:
        raise Phase4ContractViolation("excluded accounting mismatch")
    if metrics.input_count != context.input_count:
        raise Phase4ContractViolation("metrics input accounting mismatch")
    if metrics.accepted_count != context.accepted_count:
        raise Phase4ContractViolation("metrics accepted accounting mismatch")
    if metrics.rejected_count != context.rejected_count:
        raise Phase4ContractViolation("metrics rejected accounting mismatch")


def _recompute_record_fingerprint(record: object) -> str:
    """합성 record에 둔 canonical fingerprint를 공유 결과처럼 반환한다.

    Args:
        record: 지문을 재계산할 합성 accepted record.

    Returns:
        합성 canonical fingerprint.

    Raises:
        Phase4ContractViolation: accepted record 타입이 아닌 경우.
    """

    if not isinstance(record, SyntheticAccepted):
        raise Phase4ContractViolation("unexpected StandardizedRecord type")
    return record.canonical_fingerprint


@pytest.fixture
def business_factory() -> BusinessFactory:
    """테스트별 표준 business 변형 factory를 제공한다."""

    return _make_business


@pytest.fixture
def accepted_factory() -> AcceptedFactory:
    """테스트별 accepted record factory를 제공한다."""

    return _make_accepted


@pytest.fixture
def rejected_factory() -> RejectedFactory:
    """테스트별 rejected record factory를 제공한다."""

    return _make_rejected


@pytest.fixture
def output_factory() -> OutputFactory:
    """accounting이 닫힌 합성 Phase4Output factory를 제공한다."""

    return _make_output


@pytest.fixture
def standardized_business(business_factory: BusinessFactory) -> SyntheticBusiness:
    """모든 명시 domain을 만족하는 기본 표준 business를 제공한다."""

    return business_factory()


@pytest.fixture
def phase4_binding() -> Phase4IntegrationBinding:
    """실제 공유 책임을 모사하는 fail-closed 합성 binding을 제공한다."""

    return Phase4IntegrationBinding(
        verify_lock=_verify_lock,
        verify_output=_verify_output,
        recompute_record_fingerprint=_recompute_record_fingerprint,
    )


@pytest.fixture
def phase5_processor(phase4_binding: Phase4IntegrationBinding) -> Phase5Processor:
    """합성 Phase 4 binding이 연결된 전체 processor를 제공한다."""

    return Phase5Processor(binding=phase4_binding)
