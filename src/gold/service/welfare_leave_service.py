"""조건 기반 대상자 집계와 복지연차 일괄 부여 업무 규칙을 제공합니다."""

import calendar
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
from uuid import uuid4

from django.conf import settings
from django.core import signing
from django.core.paginator import Page, Paginator
from django.db import IntegrityError, transaction
from django.utils import timezone

from repository.employee_repository import EmployeeRepository
from repository.models import WelfareLeaveGrantBatch, WelfareLeavePolicy
from repository.mount_repository import DatabaseMountRepository, MountStatus
from repository.welfare_leave_repository import (
    WelfareLeaveGrantRepository,
    WelfareLeavePolicyRepository,
)


SELECTION_TOKEN_SALT = "welfare-leave-target-selection"


class MountUnavailableError(RuntimeError):
    """MySQL이 연결되지 않은 상태에서 조회·저장을 요청했을 때 발생합니다."""


class SelectionTokenError(ValueError):
    """대상자 조회 토큰이 없거나 만료·변조됐을 때 발생합니다."""


class TargetSetChangedError(RuntimeError):
    """조회 이후 저장 전까지 대상자 집합이 변경됐을 때 발생합니다."""


class PolicyNotFoundError(LookupError):
    """활성 상태의 실제 복지연차 정책을 찾지 못했을 때 발생합니다."""


class OperatorMissingError(ValueError):
    """처리 관리자 식별자가 설정되지 않았을 때 발생합니다."""


class NoTargetsError(ValueError):
    """일괄 부여할 대상자가 없을 때 발생합니다."""


@dataclass(frozen=True)
class FilterOptions:
    """검색 화면의 부서·팀·직급 선택값입니다."""

    departments: tuple[tuple[str, str], ...]
    teams: tuple[tuple[str, str], ...]
    positions: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class TargetSearchResult:
    """전체 대상 집계와 화면에 표시할 한 페이지의 조회 결과입니다."""

    condition: dict[str, object]
    total_count: int
    display_count: int
    page: Page
    keyword: str
    selection_token: str


@dataclass(frozen=True)
class GrantResult:
    """일괄 부여 처리 결과와 중복 제출 여부입니다."""

    batch: WelfareLeaveGrantBatch
    duplicate_request: bool


def get_mount_status(validate_schema: bool = False) -> MountStatus:
    """현재 MySQL 마운트와 선택적 스키마 검증 결과를 반환합니다."""
    return DatabaseMountRepository.check(validate_schema=validate_schema)


def get_filter_options(department_code: str = "") -> FilterOptions:
    """실제 사원 테이블에서 조건 선택용 부서·팀·직급 목록을 가져옵니다."""
    if not settings.DATA_SOURCE_READY:
        return FilterOptions((), (), ())
    return FilterOptions(
        departments=tuple(EmployeeRepository.list_departments()),
        teams=tuple(EmployeeRepository.list_teams(department_code)),
        positions=tuple(EmployeeRepository.list_positions()),
    )


def get_active_policies() -> tuple[WelfareLeavePolicy, ...]:
    """MySQL에 등록된 활성 복지연차 정책만 반환합니다."""
    if not settings.DATA_SOURCE_READY:
        return ()
    return tuple(WelfareLeavePolicyRepository.list_active())


def subtract_months(reference_date: date, months: int) -> date:
    """기준일에서 지정 개월을 빼되 월말을 안전하게 보정합니다."""
    month_index = reference_date.year * 12 + reference_date.month - 1 - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(reference_date.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def calculate_tenure_months(hire_date: date, reference_date: date) -> int:
    """입사일과 기준일 사이의 완료된 근속 개월 수를 계산합니다."""
    months = (reference_date.year - hire_date.year) * 12
    months += reference_date.month - hire_date.month
    if reference_date.day < hire_date.day:
        months -= 1
    return max(months, 0)


def format_tenure(hire_date: date, reference_date: date) -> str:
    """완료된 근속 개월을 'N년 N개월' 표시값으로 변환합니다."""
    months = calculate_tenure_months(hire_date, reference_date)
    years, remaining_months = divmod(months, 12)
    return f"{years}년 {remaining_months}개월"


def normalize_condition(cleaned_data: dict[str, object]) -> dict[str, object]:
    """Form 검증값을 ORM 조회에 사용할 일관된 조건 사전으로 변환합니다."""
    reference_date = timezone.localdate()
    tenure_condition = str(cleaned_data.get("tenure_condition") or "")
    tenure_months = int(tenure_condition) if tenure_condition else None

    condition: dict[str, object] = {
        "policy_code": str(cleaned_data.get("policy_code") or ""),
        "department_code": str(cleaned_data.get("department_code") or ""),
        "team_code": str(cleaned_data.get("team_code") or ""),
        "position_code": str(cleaned_data.get("position_code") or ""),
        "tenure_condition": tenure_condition,
        "tenure_year_band_months": tenure_months,
        "reference_date": reference_date,
    }
    if isinstance(tenure_months, int):
        condition["tenure_hire_date_upper"] = subtract_months(
            reference_date,
            tenure_months,
        )
        condition["tenure_hire_date_lower_exclusive"] = subtract_months(
            reference_date,
            tenure_months + 12,
        )
    return condition


def serialize_condition(condition: dict[str, object]) -> dict[str, object]:
    """날짜를 ISO 문자열로 변환해 서명 토큰과 JSON 저장에 사용합니다."""
    serialized: dict[str, object] = {}
    for key, value in condition.items():
        serialized[key] = value.isoformat() if isinstance(value, date) else value
    return serialized


def deserialize_condition(serialized: dict[str, object]) -> dict[str, object]:
    """서명 토큰의 ISO 날짜 문자열을 ORM 조회용 날짜 객체로 복원합니다."""
    date_keys = {
        "reference_date",
        "tenure_hire_date_upper",
        "tenure_hire_date_lower_exclusive",
    }
    condition = dict(serialized)
    for key in date_keys:
        value = condition.get(key)
        if isinstance(value, str) and value:
            condition[key] = date.fromisoformat(value)
    return condition


def _tenure_months_label(value: object) -> str:
    """개월 수를 사람이 읽기 쉬운 년·개월 문자열로 변환합니다."""
    try:
        total_months = int(value)
    except (TypeError, ValueError):
        return "근속기간 정보 없음"
    years, months = divmod(max(total_months, 0), 12)
    if years and months:
        return f"{years}년 {months}개월"
    if years:
        return f"{years}년"
    return f"{months}개월"


def format_history_condition_lines(
    condition_snapshot: str,
    filter_options: FilterOptions,
) -> tuple[str, ...]:
    """저장된 조건 JSON을 내부 코드가 없는 자연어 조건 목록으로 변환합니다."""
    try:
        condition = json.loads(condition_snapshot)
        if not isinstance(condition, dict):
            raise TypeError
    except (json.JSONDecodeError, TypeError):
        return ("조건 정보를 읽을 수 없습니다.",)

    display_labels = condition.get("display_labels")
    if not isinstance(display_labels, dict):
        display_labels = {}

    department_labels = dict(filter_options.departments)
    team_labels = dict(filter_options.teams)
    position_labels = dict(filter_options.positions)
    lines: list[str] = []

    department_code = str(condition.get("department_code") or "")
    if department_code:
        department_name = str(
            display_labels.get("department_name")
            or department_labels.get(department_code)
            or "현재 이름을 확인할 수 없는 부서"
        )
        lines.append(f"부서: {department_name}")

    team_code = str(condition.get("team_code") or "")
    if team_code:
        team_name = str(
            display_labels.get("team_name")
            or team_labels.get(team_code)
            or "현재 이름을 확인할 수 없는 업무팀"
        )
        lines.append(f"업무팀: {team_name}")

    position_code = str(condition.get("position_code") or "")
    if position_code:
        position_name = str(
            display_labels.get("position_name")
            or position_labels.get(position_code)
            or "현재 이름을 확인할 수 없는 직급"
        )
        lines.append(f"직급: {position_name}")

    tenure_year_band_months = condition.get("tenure_year_band_months")
    tenure_months = condition.get("tenure_months")
    if tenure_months in (None, ""):
        tenure_months = condition.get("tenure_min_months")
    maximum_tenure_months = condition.get("tenure_max_months")
    if tenure_year_band_months not in (None, ""):
        start_label = _tenure_months_label(tenure_year_band_months)
        try:
            end_months = int(tenure_year_band_months) + 11
        except (TypeError, ValueError):
            lines.append("연차: 저장된 정보를 확인할 수 없습니다.")
        else:
            end_label = _tenure_months_label(end_months)
            lines.append(f"연차: {start_label} ~ {end_label}")
    elif tenure_months not in (None, "") and maximum_tenure_months not in (None, ""):
        lines.append(
            "근속기간: "
            f"{_tenure_months_label(tenure_months)} 이상, "
            f"{_tenure_months_label(maximum_tenure_months)} 이하"
        )
    elif tenure_months not in (None, ""):
        lines.append(f"근속기간: {_tenure_months_label(tenure_months)} 이상")
    elif maximum_tenure_months not in (None, ""):
        lines.append(
            f"근속기간: {_tenure_months_label(maximum_tenure_months)} 이하"
        )

    hire_date_from = condition.get("hire_date_from")
    hire_date_to = condition.get("hire_date_to")
    if hire_date_from and hire_date_to:
        lines.append(f"입사일: {hire_date_from}부터 {hire_date_to}까지")
    elif hire_date_from:
        lines.append(f"입사일: {hire_date_from} 이후")
    elif hire_date_to:
        lines.append(f"입사일: {hire_date_to} 이전")

    if not lines:
        lines.append("대상 범위: 전체 재직자")

    selection_mode = str(condition.get("selection_mode") or "")
    selected_target_count = condition.get("selected_target_count")
    if selection_mode == "manual" and selected_target_count not in (None, ""):
        lines.append(f"대상 선택: 체크박스로 {selected_target_count}명 선택")
    elif selection_mode == "all" and selected_target_count not in (None, ""):
        excluded_target_count = int(condition.get("excluded_target_count") or 0)
        if excluded_target_count:
            lines.append(
                "대상 선택: 검색 결과 전체에서 "
                f"{excluded_target_count}명 제외, {selected_target_count}명 선택"
            )
        else:
            lines.append(f"대상 선택: 검색 결과 전체 {selected_target_count}명 선택")
    selection_keyword = str(condition.get("selection_keyword") or "").strip()
    if selection_keyword:
        lines.append(f"결과 내 검색: {selection_keyword}")

    reference_date = condition.get("reference_date")
    if reference_date and (
        tenure_year_band_months not in (None, "")
        or tenure_months not in (None, "")
        or maximum_tenure_months not in (None, "")
    ):
        lines.append(f"근속 계산 기준일: {reference_date}")
    return tuple(lines)


def calculate_target_fingerprint(queryset: object) -> str:
    """정렬된 전체 대상 사원번호로 대상 집합 변경 감지용 SHA-256을 계산합니다."""
    return calculate_employee_number_fingerprint(
        EmployeeRepository.iter_employee_numbers(queryset)
    )


def calculate_employee_number_fingerprint(employee_numbers: Iterable[str]) -> str:
    """사원번호 순서열을 구분자와 함께 SHA-256으로 변환합니다."""
    digest = sha256()
    for employee_no in employee_numbers:
        digest.update(employee_no.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def search_targets(
    cleaned_condition: dict[str, object],
    keyword: str = "",
    page_number: object = 1,
    display_labels: dict[str, str] | None = None,
) -> TargetSearchResult:
    """조건 전체 대상 수를 집계하고 결과 내 검색 목록 한 페이지를 반환합니다."""
    if not settings.DATA_SOURCE_READY:
        raise MountUnavailableError("MySQL 마운트 또는 데모 모드 활성화 후 조회할 수 있습니다.")

    condition = normalize_condition(cleaned_condition)
    if display_labels:
        condition["display_labels"] = dict(display_labels)
    target_queryset = EmployeeRepository.build_target_queryset(condition)
    total_count = target_queryset.count()

    display_queryset = EmployeeRepository.search_within_results(
        target_queryset,
        keyword,
    )
    display_count = display_queryset.count()
    selection_scope_fingerprint = calculate_target_fingerprint(display_queryset)
    paginator = Paginator(display_queryset, settings.WELFARE_RESULT_PAGE_SIZE)
    page = paginator.get_page(page_number)
    reference_date = condition["reference_date"]
    page.object_list = list(page.object_list)
    for employee in page.object_list:
        employee.tenure_label = format_tenure(employee.hire_date, reference_date)

    token_payload = {
        "condition": serialize_condition(condition),
        "selection_keyword": keyword.strip(),
        "selection_scope_count": display_count,
        "selection_scope_fingerprint": selection_scope_fingerprint,
        "request_key": uuid4().hex,
    }
    selection_token = signing.dumps(
        token_payload,
        salt=SELECTION_TOKEN_SALT,
        compress=True,
    )
    return TargetSearchResult(
        condition=condition,
        total_count=total_count,
        display_count=display_count,
        page=page,
        keyword=keyword.strip(),
        selection_token=selection_token,
    )


def grant_welfare_leave(
    selection_token: str,
    selection_mode: str,
    selected_employee_numbers: Iterable[str],
    excluded_employee_numbers: Iterable[str],
    apply_date: date,
    processed_by: str,
) -> GrantResult:
    """개별 또는 전체 선택 상태를 조회 범위에서 재검증하고 일괄 저장합니다."""
    if not settings.DATA_SOURCE_READY:
        raise MountUnavailableError(
            "MySQL 마운트 또는 데모 모드 활성화 전에는 일괄 부여할 수 없습니다."
        )
    if not processed_by.strip():
        raise OperatorMissingError("처리 관리자 식별자를 설정하세요.")

    try:
        payload = signing.loads(
            selection_token,
            salt=SELECTION_TOKEN_SALT,
            max_age=settings.WELFARE_SELECTION_TOKEN_MAX_AGE,
        )
    except signing.BadSignature as error:
        raise SelectionTokenError(
            "대상자 조회 정보가 만료되었거나 올바르지 않습니다. 다시 조회하세요."
        ) from error

    try:
        serialized_condition = payload["condition"]
        selection_keyword = str(payload.get("selection_keyword") or "")
        expected_scope_count = int(payload["selection_scope_count"])
        expected_scope_fingerprint = str(payload["selection_scope_fingerprint"])
        request_key = str(payload["request_key"])
        if not isinstance(serialized_condition, dict):
            raise TypeError
    except (KeyError, TypeError, ValueError) as error:
        raise SelectionTokenError(
            "대상자 조회 정보 형식이 올바르지 않습니다. 다시 조회하세요."
        ) from error

    normalized_selected_numbers = tuple(
        dict.fromkeys(
            str(employee_no).strip()
            for employee_no in selected_employee_numbers
            if str(employee_no).strip()
        )
    )
    normalized_excluded_numbers = tuple(
        dict.fromkeys(
            str(employee_no).strip()
            for employee_no in excluded_employee_numbers
            if str(employee_no).strip()
        )
    )
    if selection_mode not in {"manual", "all"}:
        raise SelectionTokenError("대상자 선택 방식이 올바르지 않습니다. 다시 조회하세요.")
    if selection_mode == "manual" and not normalized_selected_numbers:
        raise NoTargetsError("일괄 부여할 대상자를 한 명 이상 선택하세요.")

    condition = deserialize_condition(serialized_condition)
    target_queryset = EmployeeRepository.build_target_queryset(condition)
    selection_scope_queryset = EmployeeRepository.search_within_results(
        target_queryset,
        selection_keyword,
    )
    current_scope_count = selection_scope_queryset.count()
    current_scope_fingerprint = calculate_target_fingerprint(
        selection_scope_queryset
    )
    if (
        current_scope_count != expected_scope_count
        or current_scope_fingerprint != expected_scope_fingerprint
    ):
        raise TargetSetChangedError(
            "조회 이후 검색 결과가 변경되었습니다. 대상자 조회를 다시 실행하세요."
        )

    if selection_mode == "all":
        excluded_queryset = EmployeeRepository.filter_by_employee_numbers(
            selection_scope_queryset,
            normalized_excluded_numbers,
        )
        existing_excluded_numbers = tuple(
            EmployeeRepository.iter_employee_numbers(excluded_queryset)
        )
        if set(existing_excluded_numbers) != set(normalized_excluded_numbers):
            raise SelectionTokenError(
                "현재 조회·검색 결과에 포함되지 않은 제외 대상이 전달되었습니다. "
                "다시 조회하세요."
            )
        selected_queryset = EmployeeRepository.exclude_employee_numbers(
            selection_scope_queryset,
            normalized_excluded_numbers,
        )
    else:
        selected_queryset = EmployeeRepository.filter_by_employee_numbers(
            selection_scope_queryset,
            normalized_selected_numbers,
        )
    employee_numbers = list(EmployeeRepository.iter_employee_numbers(selected_queryset))
    if (
        selection_mode == "manual"
        and set(employee_numbers) != set(normalized_selected_numbers)
    ):
        raise SelectionTokenError(
            "현재 조회·검색 결과에 포함되지 않은 대상자가 선택되었습니다. 다시 조회하세요."
        )
    current_count = len(employee_numbers)
    if current_count < 1:
        raise NoTargetsError("일괄 부여할 대상자를 한 명 이상 선택하세요.")

    try:
        policy = WelfareLeavePolicyRepository.get_active(
            str(condition.get("policy_code") or "")
        )
    except WelfareLeavePolicy.DoesNotExist as error:
        raise PolicyNotFoundError(
            "선택한 복지연차 정책이 없거나 비활성 상태입니다."
        ) from error

    existing_batch = WelfareLeaveGrantRepository.get_by_request_key(request_key)
    if existing_batch is not None:
        return GrantResult(batch=existing_batch, duplicate_request=True)

    processed_at = timezone.now()
    batch_id = f"WG-{processed_at:%Y%m%d%H%M%S}-{uuid4().hex[:8]}"
    condition["selection_mode"] = selection_mode
    condition["selection_keyword"] = selection_keyword
    condition["selected_target_count"] = current_count
    condition["excluded_target_count"] = (
        len(normalized_excluded_numbers) if selection_mode == "all" else 0
    )
    condition_snapshot = json.dumps(
        serialize_condition(condition),
        ensure_ascii=False,
        sort_keys=True,
    )

    try:
        with transaction.atomic():
            batch = WelfareLeaveGrantRepository.create_batch(
                batch_id=batch_id,
                policy_code=policy.policy_code,
                policy_name=policy.policy_name,
                condition_snapshot=condition_snapshot,
                target_count=current_count,
                grant_days=policy.grant_days,
                apply_date=apply_date,
                processed_at=processed_at,
                processed_by=processed_by.strip(),
                status="success",
                request_key=request_key,
            )
            created_count = WelfareLeaveGrantRepository.bulk_create_targets(
                batch_id=batch_id,
                employee_numbers=employee_numbers,
                grant_days=policy.grant_days,
                processed_at=processed_at,
            )
            if created_count != current_count:
                raise TargetSetChangedError(
                    "대상 상세 저장 건수가 집계 인원과 일치하지 않습니다."
                )
    except IntegrityError:
        existing_batch = WelfareLeaveGrantRepository.get_by_request_key(request_key)
        if existing_batch is not None:
            return GrantResult(batch=existing_batch, duplicate_request=True)
        raise

    return GrantResult(batch=batch, duplicate_request=False)


def get_grant_history(
    page_number: object = 1,
    filter_options: FilterOptions | None = None,
) -> Page:
    """최근 부여 내역에 대상 사원번호와 자연어 조건을 결합해 반환합니다."""
    queryset = WelfareLeaveGrantRepository.history_queryset()
    page = Paginator(queryset, settings.WELFARE_HISTORY_PAGE_SIZE).get_page(
        page_number
    )
    batches = list(page.object_list)
    page.object_list = batches
    employee_number_map = WelfareLeaveGrantRepository.employee_numbers_by_batch_ids(
        batch.batch_id for batch in batches
    )
    resolved_options = filter_options or FilterOptions((), (), ())
    for batch in batches:
        batch.employee_numbers = employee_number_map.get(batch.batch_id, ())
        batch.condition_lines = format_history_condition_lines(
            batch.condition_snapshot,
            resolved_options,
        )
    return page
