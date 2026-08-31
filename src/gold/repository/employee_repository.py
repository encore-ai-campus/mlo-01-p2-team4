"""조건 기반 사원 조회와 대상자 집계를 수행합니다."""

from typing import Iterable

from django.conf import settings
from django.db.models import Q, QuerySet

from .models import EmployeeRecord


class EmployeeRepository:
    """기존 MySQL 사원 테이블의 조건 조회를 담당합니다."""

    @staticmethod
    def build_target_queryset(condition: dict[str, object]) -> QuerySet[EmployeeRecord]:
        """정규화된 조건을 적용한 전체 부여 대상 QuerySet을 반환합니다."""
        if not settings.DATA_SOURCE_READY:
            return EmployeeRecord.objects.none()

        queryset = EmployeeRecord.objects.filter(
            active_yn=settings.MYSQL_EMPLOYEE_ACTIVE_VALUE
        )

        exact_filters = {
            "department_code": "department_code",
            "team_code": "team_code",
            "position_code": "position_code",
        }
        for condition_key, model_field in exact_filters.items():
            value = condition.get(condition_key)
            if value:
                queryset = queryset.filter(**{model_field: value})

        if condition.get("tenure_hire_date_upper"):
            queryset = queryset.filter(
                hire_date__lte=condition["tenure_hire_date_upper"]
            )
        if condition.get("tenure_hire_date_lower_exclusive"):
            queryset = queryset.filter(
                hire_date__gt=condition["tenure_hire_date_lower_exclusive"]
            )

        return queryset.order_by("employee_no")

    @staticmethod
    def search_within_results(
        queryset: QuerySet[EmployeeRecord],
        keyword: str,
    ) -> QuerySet[EmployeeRecord]:
        """전체 대상 범위는 유지하고 화면 표시 목록만 사원번호·이름으로 좁힙니다."""
        normalized_keyword = keyword.strip()
        if not normalized_keyword:
            return queryset
        return queryset.filter(
            Q(employee_no__icontains=normalized_keyword)
            | Q(employee_name__icontains=normalized_keyword)
        )

    @staticmethod
    def filter_by_employee_numbers(
        queryset: QuerySet[EmployeeRecord],
        employee_numbers: Iterable[str],
    ) -> QuerySet[EmployeeRecord]:
        """기존 조회 범위 안에서 선택된 사원번호만 반환합니다."""
        return queryset.filter(employee_no__in=tuple(employee_numbers)).order_by(
            "employee_no"
        )

    @staticmethod
    def exclude_employee_numbers(
        queryset: QuerySet[EmployeeRecord],
        employee_numbers: Iterable[str],
    ) -> QuerySet[EmployeeRecord]:
        """기존 조회 범위에서 전체 선택 후 해제한 사원번호를 제외합니다."""
        normalized_numbers = tuple(employee_numbers)
        if not normalized_numbers:
            return queryset
        return queryset.exclude(employee_no__in=normalized_numbers).order_by(
            "employee_no"
        )

    @staticmethod
    def iter_employee_numbers(
        queryset: QuerySet[EmployeeRecord],
    ) -> Iterable[str]:
        """대상 사원번호를 메모리에 한꺼번에 올리지 않고 순회합니다."""
        return queryset.values_list("employee_no", flat=True).iterator(chunk_size=1000)

    @staticmethod
    def list_departments() -> list[tuple[str, str]]:
        """검색 조건에 사용할 실제 부서 코드와 이름을 반환합니다."""
        if not settings.DATA_SOURCE_READY:
            return []
        rows = (
            EmployeeRecord.objects.filter(active_yn=settings.MYSQL_EMPLOYEE_ACTIVE_VALUE)
            .exclude(department_code="")
            .values_list("department_code", "department_name")
            .distinct()
            .order_by("department_name")
        )
        return list(rows)

    @staticmethod
    def list_teams(department_code: str = "") -> list[tuple[str, str]]:
        """선택 부서 범위의 실제 팀 코드와 이름을 반환합니다."""
        if not settings.DATA_SOURCE_READY:
            return []
        queryset = EmployeeRecord.objects.filter(
            active_yn=settings.MYSQL_EMPLOYEE_ACTIVE_VALUE
        )
        if department_code:
            queryset = queryset.filter(department_code=department_code)
        rows = (
            queryset.exclude(team_code="")
            .values_list("team_code", "team_name")
            .distinct()
            .order_by("team_name")
        )
        return list(rows)

    @staticmethod
    def list_positions() -> list[tuple[str, str]]:
        """검색 조건에 사용할 실제 직급 코드와 이름을 반환합니다."""
        if not settings.DATA_SOURCE_READY:
            return []
        rows = (
            EmployeeRecord.objects.filter(active_yn=settings.MYSQL_EMPLOYEE_ACTIVE_VALUE)
            .exclude(position_code="")
            .values_list("position_code", "position_name")
            .distinct()
            .order_by("position_name")
        )
        return list(rows)
