"""복지연차 정책 조회와 일괄 부여 결과 저장을 수행합니다."""

from decimal import Decimal
from datetime import datetime
from typing import Iterable
from uuid import NAMESPACE_URL, uuid5

from django.conf import settings
from django.db.models import QuerySet

from .models import (
    WelfareLeaveGrantBatch,
    WelfareLeaveGrantTarget,
    WelfareLeavePolicy,
)


class WelfareLeavePolicyRepository:
    """실제 사내 복지연차 정책 테이블을 조회합니다."""

    @staticmethod
    def list_active() -> list[WelfareLeavePolicy]:
        """현재 사용 가능한 복지연차 정책만 반환합니다."""
        if not settings.DATA_SOURCE_READY:
            return []
        return list(
            WelfareLeavePolicy.objects.filter(
                active_yn=settings.MYSQL_POLICY_ACTIVE_VALUE
            ).order_by("policy_name")
        )

    @staticmethod
    def get_active(policy_code: str) -> WelfareLeavePolicy:
        """정책 코드가 일치하는 활성 복지연차 정책 한 건을 반환합니다."""
        return WelfareLeavePolicy.objects.get(
            policy_code=policy_code,
            active_yn=settings.MYSQL_POLICY_ACTIVE_VALUE,
        )


class WelfareLeaveGrantRepository:
    """일괄 부여 마스터와 사원별 상세 결과를 저장합니다."""

    @staticmethod
    def get_by_request_key(request_key: str) -> WelfareLeaveGrantBatch | None:
        """중복 제출 여부를 확인하기 위해 기존 배치를 조회합니다."""
        return WelfareLeaveGrantBatch.objects.filter(request_key=request_key).first()

    @staticmethod
    def create_batch(**values: object) -> WelfareLeaveGrantBatch:
        """일괄 부여 조건과 처리 요약을 마스터 테이블에 생성합니다."""
        return WelfareLeaveGrantBatch.objects.create(**values)

    @staticmethod
    def bulk_create_targets(
        batch_id: str,
        employee_numbers: Iterable[str],
        grant_days: Decimal,
        processed_at: datetime,
    ) -> int:
        """대상 사원별 성공 상세 행을 설정된 크기로 나눠 일괄 저장합니다."""
        buffer: list[WelfareLeaveGrantTarget] = []
        created_count = 0

        for employee_no in employee_numbers:
            buffer.append(
                WelfareLeaveGrantTarget(
                    target_id=uuid5(
                        NAMESPACE_URL,
                        f"{batch_id}:{employee_no}",
                    ).hex,
                    batch_id=batch_id,
                    employee_no=employee_no,
                    grant_days=grant_days,
                    status="success",
                    failure_reason=None,
                    processed_at=processed_at,
                )
            )
            if len(buffer) >= settings.WELFARE_BULK_BATCH_SIZE:
                WelfareLeaveGrantTarget.objects.bulk_create(
                    buffer,
                    batch_size=settings.WELFARE_BULK_BATCH_SIZE,
                )
                created_count += len(buffer)
                buffer.clear()

        if buffer:
            WelfareLeaveGrantTarget.objects.bulk_create(
                buffer,
                batch_size=settings.WELFARE_BULK_BATCH_SIZE,
            )
            created_count += len(buffer)

        return created_count

    @staticmethod
    def history_queryset() -> QuerySet[WelfareLeaveGrantBatch]:
        """최근 처리 순서의 일괄 부여 내역 QuerySet을 반환합니다."""
        if not settings.DATA_SOURCE_READY:
            return WelfareLeaveGrantBatch.objects.none()
        return WelfareLeaveGrantBatch.objects.all().order_by("-processed_at")

    @staticmethod
    def employee_numbers_by_batch_ids(
        batch_ids: Iterable[str],
    ) -> dict[str, tuple[str, ...]]:
        """여러 배치의 대상 사원번호를 한 번의 조회로 묶어 반환합니다."""
        normalized_batch_ids = tuple(batch_ids)
        if not settings.DATA_SOURCE_READY or not normalized_batch_ids:
            return {}

        grouped: dict[str, list[str]] = {
            batch_id: [] for batch_id in normalized_batch_ids
        }
        rows = (
            WelfareLeaveGrantTarget.objects.filter(
                batch_id__in=normalized_batch_ids,
            )
            .order_by("batch_id", "employee_no")
            .values_list("batch_id", "employee_no")
        )
        for batch_id, employee_no in rows:
            grouped.setdefault(batch_id, []).append(employee_no)
        return {
            batch_id: tuple(employee_numbers)
            for batch_id, employee_numbers in grouped.items()
        }
