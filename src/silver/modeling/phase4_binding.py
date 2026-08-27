"""Plan 1 공유 계약과 Phase 5 사이의 fail-closed 결합 경계를 정의한다."""

from collections.abc import Callable
from dataclasses import dataclass

VerifyLockCallback = Callable[[object], None]
VerifyOutputCallback = Callable[[object], None]
RecomputeRecordFingerprintCallback = Callable[[object], str]


class Phase4IntegrationUnavailable(RuntimeError):
    """필수 Plan 1 공유 결합이 아직 제공되지 않았음을 나타낸다."""


class Phase4ContractViolation(ValueError):
    """Phase 4가 보장해야 할 입력 계약 위반을 나타낸다."""


@dataclass(frozen=True, slots=True)
class Phase4IntegrationBinding:
    """Plan 1 소유 검증과 지문 함수를 주입하는 불변 결합점이다.

    ``verify_lock``은 공유 Phase 4 계약 lock의 version과 checksum을 모두
    고정값과 대조해야 한다. ``verify_output``은 실제 ``Phase4Output`` 타입,
    context version, accepted/rejected accounting과 Phase 4 metrics를 검증해야
    한다. 두 callback은 계약 위반 시 ``Phase4ContractViolation``을 발생시킨다.
    ``recompute_record_fingerprint``는 Plan 1의 단일 canonical 알고리즘을
    호출해야 하며 Phase 5가 그 알고리즘을 복제하지 않도록 한다.
    """

    verify_lock: VerifyLockCallback
    verify_output: VerifyOutputCallback
    recompute_record_fingerprint: RecomputeRecordFingerprintCallback

    def __post_init__(self) -> None:
        """세 결합 callback이 모두 호출 가능한지 확인한다.

        Raises:
            Phase4IntegrationUnavailable: callback이 하나라도 호출 불가능한 경우.
        """

        callbacks = (
            ("verify_lock", self.verify_lock),
            ("verify_output", self.verify_output),
            (
                "recompute_record_fingerprint",
                self.recompute_record_fingerprint,
            ),
        )
        for callback_name, callback in callbacks:
            if not callable(callback):
                raise Phase4IntegrationUnavailable(
                    f"Phase 4 integration callback is not callable: {callback_name}"
                )


def _unavailable_verify_lock(phase4_output: object) -> None:
    """공유 계약 lock 결합이 없으면 검증을 건너뛰지 않고 중단한다.

    Args:
        phase4_output: 검증하려던 실제 Phase 4 출력 객체.

    Raises:
        Phase4IntegrationUnavailable: 항상 발생한다.
    """

    del phase4_output
    raise Phase4IntegrationUnavailable(
        "Phase 4 contract-lock binding is not configured"
    )


def _unavailable_verify_output(phase4_output: object) -> None:
    """공유 출력 검증 결합이 없으면 accounting 검증을 중단한다.

    Args:
        phase4_output: 검증하려던 실제 Phase 4 출력 객체.

    Raises:
        Phase4IntegrationUnavailable: 항상 발생한다.
    """

    del phase4_output
    raise Phase4IntegrationUnavailable(
        "Phase 4 output/accounting binding is not configured"
    )


def _unavailable_recompute_record_fingerprint(record: object) -> str:
    """공유 지문 함수가 없으면 임의 알고리즘으로 대체하지 않고 중단한다.

    Args:
        record: 지문을 다시 계산하려던 실제 StandardizedRecord 객체.

    Returns:
        정상 결합에서는 재계산한 canonical record fingerprint를 반환한다.

    Raises:
        Phase4IntegrationUnavailable: 항상 발생한다.
    """

    del record
    raise Phase4IntegrationUnavailable(
        "Phase 4 record-fingerprint binding is not configured"
    )


def unavailable_phase4_binding() -> Phase4IntegrationBinding:
    """누락된 Plan 1 결합을 명시적으로 실패시키는 기본 binding을 만든다.

    Returns:
        모든 Phase 4 공유 작업을 fail closed로 처리하는 binding.
    """

    return Phase4IntegrationBinding(
        verify_lock=_unavailable_verify_lock,
        verify_output=_unavailable_verify_output,
        recompute_record_fingerprint=(_unavailable_recompute_record_fingerprint),
    )
