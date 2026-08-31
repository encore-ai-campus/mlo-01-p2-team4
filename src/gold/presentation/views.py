"""조건 기반 복지연차 일괄 부여 및 처리 내역 화면을 제공합니다."""

import logging

from django.conf import settings
from django.core.paginator import Paginator
from django.db import DatabaseError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from service.welfare_leave_service import (
    FilterOptions,
    MountUnavailableError,
    NoTargetsError,
    OperatorMissingError,
    PolicyNotFoundError,
    SelectionTokenError,
    TargetSetChangedError,
    get_active_policies,
    get_filter_options,
    get_grant_history,
    get_mount_status,
    grant_welfare_leave,
    search_targets,
)

from .forms import BulkGrantForm, ResultSearchForm, TargetConditionForm


logger = logging.getLogger(__name__)
CONDITION_FIELD_NAMES = tuple(TargetConditionForm.base_fields)


def _policy_choices(policies: tuple[object, ...]) -> list[tuple[str, str]]:
    """정책 모델을 화면 선택값으로 변환합니다."""
    return [
        (
            policy.policy_code,
            policy.policy_name,
        )
        for policy in policies
    ]


def _preserved_condition_fields(request: HttpRequest) -> tuple[tuple[str, str], ...]:
    """결과 내 검색과 페이지 이동에 유지할 대상자 조건값을 반환합니다."""
    preserved: list[tuple[str, str]] = []
    for name in CONDITION_FIELD_NAMES:
        value = request.GET.get(name, "")
        if value:
            preserved.append((name, value))
    return tuple(preserved)


def _condition_display_labels(
    cleaned_condition: dict[str, object],
    filter_options: FilterOptions,
    policies: tuple[object, ...],
) -> dict[str, str]:
    """선택된 조건 코드의 화면 표시명을 이력 스냅샷용으로 반환합니다."""
    label_sources = {
        "department_name": (
            str(cleaned_condition.get("department_code") or ""),
            dict(filter_options.departments),
        ),
        "team_name": (
            str(cleaned_condition.get("team_code") or ""),
            dict(filter_options.teams),
        ),
        "position_name": (
            str(cleaned_condition.get("position_code") or ""),
            dict(filter_options.positions),
        ),
        "policy_name": (
            str(cleaned_condition.get("policy_code") or ""),
            {policy.policy_code: policy.policy_name for policy in policies},
        ),
    }
    labels: dict[str, str] = {}
    for label_name, (code, label_map) in label_sources.items():
        if code and code in label_map:
            labels[label_name] = str(label_map[code])
    return labels


def welfare_leave_grant_view(request: HttpRequest) -> HttpResponse:
    """조건 조회, 대상 집계, 결과 내 검색과 일괄 부여 요청을 처리합니다."""
    mount_status = get_mount_status(validate_schema=False)
    error_message = ""
    query_notice = ""
    search_result = None
    selected_policy = None

    department_code = request.GET.get("department_code", "")
    filter_options = FilterOptions((), (), ())
    policies: tuple[object, ...] = ()
    if mount_status.connected:
        try:
            filter_options = get_filter_options(department_code)
            policies = get_active_policies()
        except DatabaseError:
            logger.exception("검색 조건 또는 복지연차 정책 조회에 실패했습니다.")
            error_message = (
                "MySQL 테이블·컬럼 매핑을 확인한 뒤 마운트 점검을 다시 실행하세요."
            )

    policy_choices = _policy_choices(policies)
    condition_form = TargetConditionForm(
        request.GET or None,
        filter_options=filter_options,
        policy_choices=policy_choices,
    )
    result_search_form = ResultSearchForm(request.GET or None)
    grant_form = BulkGrantForm()

    if request.method == "POST":
        grant_form = BulkGrantForm(request.POST)
        if grant_form.is_valid():
            try:
                result = grant_welfare_leave(
                    selection_token=str(
                        grant_form.cleaned_data["selection_token"]
                    ),
                    selection_mode=str(
                        grant_form.cleaned_data["selection_mode"]
                    ),
                    selected_employee_numbers=grant_form.cleaned_data[
                        "selected_employee_numbers"
                    ],
                    excluded_employee_numbers=grant_form.cleaned_data[
                        "excluded_employee_numbers"
                    ],
                    apply_date=grant_form.cleaned_data["apply_date"],
                    processed_by=settings.WELFARE_OPERATOR_ID,
                )
                history_url = reverse("presentation:grant-history")
                return redirect(f"{history_url}?completed={result.batch.batch_id}")
            except (
                MountUnavailableError,
                NoTargetsError,
                OperatorMissingError,
                PolicyNotFoundError,
                SelectionTokenError,
                TargetSetChangedError,
            ) as error:
                error_message = str(error)
            except DatabaseError:
                logger.exception("복지연차 일괄 부여 저장에 실패했습니다.")
                error_message = (
                    "일괄 부여 저장에 실패했습니다. MySQL 연결과 대상 테이블을 확인하세요."
                )
        else:
            error_message = (
                "대상자를 한 명 이상 선택하고 일괄 부여 확인값과 적용일을 확인하세요."
            )

    elif request.GET.get("query") == "1" and mount_status.connected:
        if condition_form.is_valid() and result_search_form.is_valid():
            try:
                search_result = search_targets(
                    cleaned_condition=condition_form.cleaned_data,
                    keyword=str(result_search_form.cleaned_data.get("keyword") or ""),
                    page_number=request.GET.get("page", 1),
                    display_labels=_condition_display_labels(
                        condition_form.cleaned_data,
                        filter_options,
                        policies,
                    ),
                )
                grant_form = BulkGrantForm(
                    initial={
                        "selection_token": search_result.selection_token,
                        "apply_date": timezone.localdate(),
                    }
                )
                selected_policy_code = str(
                    condition_form.cleaned_data.get("policy_code") or ""
                )
                selected_policy = next(
                    (
                        policy
                        for policy in policies
                        if policy.policy_code == selected_policy_code
                    ),
                    None,
                )
                target_filter_names = (
                    "department_code",
                    "team_code",
                    "position_code",
                    "tenure_condition",
                )
                if not any(
                    condition_form.cleaned_data.get(name)
                    for name in target_filter_names
                ):
                    query_notice = (
                        "대상자 조건이 입력되지 않아 전체 재직 데이터를 조회했습니다."
                    )
            except DatabaseError:
                logger.exception("조건 기반 대상자 조회에 실패했습니다.")
                error_message = (
                    "대상자 조회에 실패했습니다. MySQL 스키마 매핑을 확인하세요."
                )
        elif not error_message:
            error_message = "대상자 조건을 확인하세요."

    pagination_query = request.GET.copy()
    pagination_query.pop("page", None)
    context = {
        "active_menu": "grant",
        "mount_status": mount_status,
        "condition_form": condition_form,
        "result_search_form": result_search_form,
        "grant_form": grant_form,
        "search_result": search_result,
        "selected_policy": selected_policy,
        "preserved_condition_fields": _preserved_condition_fields(request),
        "pagination_query": pagination_query.urlencode(),
        "operator_ready": bool(settings.WELFARE_OPERATOR_ID),
        "can_query": mount_status.connected,
        "can_grant": bool(
            search_result
            and search_result.total_count > 0
            and selected_policy
            and settings.WELFARE_OPERATOR_ID
        ),
        "error_message": error_message,
        "query_notice": query_notice,
        "demo_employee_count": settings.DEMO_EMPLOYEE_COUNT,
    }
    return render(request, "presentation/welfare_leave_grant.html", context)


def grant_history_view(request: HttpRequest) -> HttpResponse:
    """저장된 조건·인원·부여 일수·처리일을 최근 순으로 표시합니다."""
    mount_status = get_mount_status(validate_schema=False)
    error_message = ""
    history_page = Paginator([], settings.WELFARE_HISTORY_PAGE_SIZE).get_page(1)

    if mount_status.connected:
        try:
            history_filter_options = get_filter_options()
            history_page = get_grant_history(
                request.GET.get("page", 1),
                filter_options=history_filter_options,
            )
        except DatabaseError:
            logger.exception("복지연차 부여 내역 조회에 실패했습니다.")
            error_message = "부여 내역 테이블과 컬럼 매핑을 확인하세요."

    context = {
        "active_menu": "history",
        "mount_status": mount_status,
        "history_page": history_page,
        "completed_batch_id": request.GET.get("completed", ""),
        "error_message": error_message,
    }
    return render(request, "presentation/welfare_leave_history.html", context)
