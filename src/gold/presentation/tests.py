"""미마운트 화면, 검색 조건 검증과 근속기간 계산 단위 테스트입니다."""

from datetime import date
from decimal import Decimal
import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch
from unittest.mock import MagicMock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from repository.management.commands.prepare_demo import (
    DEMO_EMPLOYEE_COUNT,
    build_demo_employee,
)
from service.welfare_leave_service import (
    FilterOptions,
    calculate_employee_number_fingerprint,
    calculate_tenure_months,
    format_history_condition_lines,
    grant_welfare_leave,
    normalize_condition,
    subtract_months,
)

from .forms import BulkGrantForm, TargetConditionForm


EMPTY_OPTIONS = FilterOptions((), (), ())
TEST_POLICY_CHOICES = [("test-policy", "테스트 정책")]


def make_condition_form(data: dict[str, object]) -> TargetConditionForm:
    """동일한 빈 조직 선택값으로 조건 Form 테스트 객체를 생성합니다."""
    return TargetConditionForm(
        data,
        filter_options=EMPTY_OPTIONS,
        policy_choices=TEST_POLICY_CHOICES,
    )


class TargetConditionFormTests(SimpleTestCase):
    """전체 조회와 선택형 근속 조건 Form 동작을 확인합니다."""

    def test_empty_target_conditions_are_allowed(self) -> None:
        """조건을 모두 비운 요청도 전체 데이터 조회를 위해 허용해야 합니다."""
        form = make_condition_form({})
        self.assertTrue(form.is_valid())

    def test_manual_tenure_inputs_are_removed(self) -> None:
        """최소·최대 근속 및 입사일 범위 입력 필드는 제공하지 않아야 합니다."""
        field_names = set(TargetConditionForm.base_fields)
        self.assertNotIn("hire_date_from", field_names)
        self.assertNotIn("hire_date_to", field_names)
        self.assertNotIn("tenure_min_months", field_names)
        self.assertNotIn("tenure_max_months", field_names)

    def test_only_predefined_tenure_conditions_are_allowed(self) -> None:
        """목록에 없는 임의 근속개월 값은 검증에 실패해야 합니다."""
        form = make_condition_form(
            {
                "policy_code": "test-policy",
                "tenure_condition": "61",
            }
        )
        self.assertFalse(form.is_valid())


class BulkGrantFormTests(SimpleTestCase):
    """체크박스 대상 목록과 일괄 부여 확인값을 검증합니다."""

    def test_multiple_selected_employees_are_normalized(self) -> None:
        """여러 사원번호를 순서대로 받고 중복 값은 한 번만 유지해야 합니다."""
        form = BulkGrantForm(
            {
                "selection_token": "signed-token",
                "selection_mode": "manual",
                "selected_employee_numbers": ["EMP001", "EMP002", "EMP001"],
                "apply_date": "2026-08-28",
                "confirm_all": "on",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["selected_employee_numbers"],
            ("EMP001", "EMP002"),
        )

    def test_selected_employee_and_confirmation_are_required(self) -> None:
        """대상 선택이나 확인 체크가 빠지면 저장 요청을 거부해야 합니다."""
        form = BulkGrantForm(
            {
                "selection_token": "signed-token",
                "selection_mode": "manual",
                "apply_date": "2026-08-28",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("selected_employee_numbers", form.errors)
        self.assertIn("confirm_all", form.errors)

    def test_all_selection_mode_allows_an_empty_exclusion_list(self) -> None:
        """전체 선택은 해제한 사원이 없어도 정상 Form으로 처리해야 합니다."""
        form = BulkGrantForm(
            {
                "selection_token": "signed-token",
                "selection_mode": "all",
                "apply_date": "2026-08-28",
                "confirm_all": "on",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)


class TenureCalculationTests(SimpleTestCase):
    """입사일 경계와 월말 보정 계산을 확인합니다."""

    def test_completed_tenure_months_are_calculated(self) -> None:
        """기준일 이전에 완료된 개월만 근속기간에 포함해야 합니다."""
        self.assertEqual(
            calculate_tenure_months(date(2021, 8, 29), date(2026, 8, 28)),
            59,
        )

    def test_subtract_months_adjusts_month_end(self) -> None:
        """31일이 없는 달로 이동할 때 해당 월의 마지막 날을 사용해야 합니다."""
        self.assertEqual(subtract_months(date(2026, 3, 31), 1), date(2026, 2, 28))

    @patch(
        "service.welfare_leave_service.timezone.localdate",
        return_value=date(2026, 8, 28),
    )
    def test_tenure_year_band_is_calculated_from_today(self, _mock_date: object) -> None:
        """5년 조건은 오늘 기준 완료 근속 60~71개월 구간으로 변환해야 합니다."""
        condition = normalize_condition({"tenure_condition": "60"})
        self.assertEqual(condition["reference_date"], date(2026, 8, 28))
        self.assertEqual(condition["tenure_year_band_months"], 60)
        self.assertEqual(condition["tenure_hire_date_upper"], date(2021, 8, 28))
        self.assertEqual(
            condition["tenure_hire_date_lower_exclusive"],
            date(2020, 8, 28),
        )

    def test_target_fingerprint_is_order_sensitive(self) -> None:
        """대상 사원 순서열이 달라지면 집합 지문도 달라져야 합니다."""
        first = calculate_employee_number_fingerprint(["employee-a", "employee-b"])
        second = calculate_employee_number_fingerprint(["employee-b", "employee-a"])
        self.assertNotEqual(first, second)


class HistoryConditionFormattingTests(SimpleTestCase):
    """저장 조건이 코드 없는 자연어 이력으로 변환되는지 확인합니다."""

    def test_condition_codes_are_replaced_with_saved_display_names(self) -> None:
        """새 이력은 조회 당시 저장한 조직 명칭과 자연어 근속기간을 사용해야 합니다."""
        snapshot = json.dumps(
            {
                "department_code": "D001",
                "team_code": "T001",
                "position_code": "P001",
                "tenure_year_band_months": 60,
                "reference_date": "2026-08-28",
                "display_labels": {
                    "department_name": "데이터전략부",
                    "team_name": "분석팀",
                    "position_name": "사원",
                },
            },
            ensure_ascii=False,
        )
        lines = format_history_condition_lines(snapshot, EMPTY_OPTIONS)
        self.assertEqual(
            lines,
            (
                "부서: 데이터전략부",
                "업무팀: 분석팀",
                "직급: 사원",
                "연차: 5년 ~ 5년 11개월",
                "근속 계산 기준일: 2026-08-28",
            ),
        )
        self.assertNotIn("D001", " ".join(lines))
        self.assertNotIn("T001", " ".join(lines))
        self.assertNotIn("P001", " ".join(lines))

    def test_legacy_condition_codes_use_current_name_mappings(self) -> None:
        """표시명 스냅샷이 없는 기존 이력도 현재 조직 명칭으로 읽을 수 있어야 합니다."""
        options = FilterOptions(
            departments=(("D001", "데이터전략부"),),
            teams=(("T001", "분석팀"),),
            positions=(("P001", "사원"),),
        )
        snapshot = json.dumps(
            {
                "department_code": "D001",
                "team_code": "T001",
                "position_code": "P001",
            }
        )
        self.assertEqual(
            format_history_condition_lines(snapshot, options),
            ("부서: 데이터전략부", "업무팀: 분석팀", "직급: 사원"),
        )

    def test_empty_target_condition_is_explained_in_plain_language(self) -> None:
        """필터가 없는 이력은 전체 재직자를 대상으로 했다고 표시해야 합니다."""
        snapshot = json.dumps({"policy_code": "POLICY-001"})
        self.assertEqual(
            format_history_condition_lines(snapshot, EMPTY_OPTIONS),
            ("대상 범위: 전체 재직자",),
        )

    def test_manual_selection_is_explained_in_plain_language(self) -> None:
        """개별 선택 이력에는 선택 인원과 결과 내 검색어를 함께 표시해야 합니다."""
        snapshot = json.dumps(
            {
                "selection_mode": "manual",
                "selected_target_count": 2,
                "selection_keyword": "김민수",
            },
            ensure_ascii=False,
        )
        self.assertEqual(
            format_history_condition_lines(snapshot, EMPTY_OPTIONS),
            (
                "대상 범위: 전체 재직자",
                "대상 선택: 체크박스로 2명 선택",
                "결과 내 검색: 김민수",
            ),
        )

    def test_all_selection_with_exclusions_is_explained_in_plain_language(self) -> None:
        """전체 선택 후 일부 해제한 이력은 선택·제외 인원을 함께 표시해야 합니다."""
        snapshot = json.dumps(
            {
                "selection_mode": "all",
                "selected_target_count": 298,
                "excluded_target_count": 2,
            },
            ensure_ascii=False,
        )
        self.assertEqual(
            format_history_condition_lines(snapshot, EMPTY_OPTIONS),
            (
                "대상 범위: 전체 재직자",
                "대상 선택: 검색 결과 전체에서 2명 제외, 298명 선택",
            ),
        )


@override_settings(DATA_SOURCE_READY=True)
class SelectedTargetGrantTests(SimpleTestCase):
    """체크된 사원만 서버 재검증 후 저장되는지 확인합니다."""

    def test_only_checked_employees_are_saved(self) -> None:
        """조회 범위 전체가 아니라 전달된 부분집합만 상세 저장해야 합니다."""
        scope_queryset = MagicMock()
        scope_queryset.count.return_value = 3
        selected_queryset = MagicMock()
        scope_numbers = ["EMP001", "EMP002", "EMP003"]
        selected_numbers = ["EMP001", "EMP003"]
        payload = {
            "condition": {"policy_code": "POLICY-001"},
            "selection_keyword": "",
            "selection_scope_count": 3,
            "selection_scope_fingerprint": calculate_employee_number_fingerprint(
                scope_numbers
            ),
            "request_key": "request-key",
        }
        batch = SimpleNamespace(batch_id="BATCH-001")
        policy = SimpleNamespace(
            policy_code="POLICY-001",
            policy_name="테스트 복지연차",
            grant_days=Decimal("1.0"),
        )

        def iter_numbers(queryset: object):
            return iter(scope_numbers if queryset is scope_queryset else selected_numbers)

        with (
            patch("service.welfare_leave_service.signing.loads", return_value=payload),
            patch(
                "service.welfare_leave_service.EmployeeRepository.build_target_queryset",
                return_value=MagicMock(),
            ),
            patch(
                "service.welfare_leave_service.EmployeeRepository.search_within_results",
                return_value=scope_queryset,
            ),
            patch(
                "service.welfare_leave_service.EmployeeRepository.filter_by_employee_numbers",
                return_value=selected_queryset,
            ) as filter_selected,
            patch(
                "service.welfare_leave_service.EmployeeRepository.iter_employee_numbers",
                side_effect=iter_numbers,
            ),
            patch(
                "service.welfare_leave_service.WelfareLeavePolicyRepository.get_active",
                return_value=policy,
            ),
            patch(
                "service.welfare_leave_service.WelfareLeaveGrantRepository.get_by_request_key",
                return_value=None,
            ),
            patch(
                "service.welfare_leave_service.WelfareLeaveGrantRepository.create_batch",
                return_value=batch,
            ) as create_batch,
            patch(
                "service.welfare_leave_service.WelfareLeaveGrantRepository.bulk_create_targets",
                return_value=2,
            ) as create_targets,
            patch(
                "service.welfare_leave_service.transaction.atomic",
                return_value=nullcontext(),
            ),
        ):
            result = grant_welfare_leave(
                selection_token="signed-token",
                selection_mode="manual",
                selected_employee_numbers=("EMP003", "EMP001"),
                excluded_employee_numbers=(),
                apply_date=date(2026, 8, 28),
                processed_by="admin",
            )

        self.assertIs(result.batch, batch)
        filter_selected.assert_called_once_with(
            scope_queryset,
            ("EMP003", "EMP001"),
        )
        self.assertEqual(create_batch.call_args.kwargs["target_count"], 2)
        self.assertEqual(
            list(create_targets.call_args.kwargs["employee_numbers"]),
            selected_numbers,
        )

    def test_all_selection_excludes_only_unchecked_employees(self) -> None:
        """전체 300명 중 두 명을 해제하면 나머지 298명만 저장해야 합니다."""
        scope_queryset = MagicMock()
        scope_queryset.count.return_value = 300
        excluded_queryset = MagicMock()
        selected_queryset = MagicMock()
        scope_numbers = [f"EMP{sequence:03d}" for sequence in range(1, 301)]
        excluded_numbers = ["EMP001", "EMP002"]
        selected_numbers = scope_numbers[2:]
        payload = {
            "condition": {"policy_code": "POLICY-001"},
            "selection_keyword": "",
            "selection_scope_count": 300,
            "selection_scope_fingerprint": calculate_employee_number_fingerprint(
                scope_numbers
            ),
            "request_key": "all-selection-request",
        }
        batch = SimpleNamespace(batch_id="BATCH-ALL")
        policy = SimpleNamespace(
            policy_code="POLICY-001",
            policy_name="테스트 복지연차",
            grant_days=Decimal("1.0"),
        )

        def iter_numbers(queryset: object):
            if queryset is scope_queryset:
                return iter(scope_numbers)
            if queryset is excluded_queryset:
                return iter(excluded_numbers)
            return iter(selected_numbers)

        with (
            patch("service.welfare_leave_service.signing.loads", return_value=payload),
            patch(
                "service.welfare_leave_service.EmployeeRepository.build_target_queryset",
                return_value=MagicMock(),
            ),
            patch(
                "service.welfare_leave_service.EmployeeRepository.search_within_results",
                return_value=scope_queryset,
            ),
            patch(
                "service.welfare_leave_service.EmployeeRepository.filter_by_employee_numbers",
                return_value=excluded_queryset,
            ),
            patch(
                "service.welfare_leave_service.EmployeeRepository.exclude_employee_numbers",
                return_value=selected_queryset,
            ) as exclude_selected,
            patch(
                "service.welfare_leave_service.EmployeeRepository.iter_employee_numbers",
                side_effect=iter_numbers,
            ),
            patch(
                "service.welfare_leave_service.WelfareLeavePolicyRepository.get_active",
                return_value=policy,
            ),
            patch(
                "service.welfare_leave_service.WelfareLeaveGrantRepository.get_by_request_key",
                return_value=None,
            ),
            patch(
                "service.welfare_leave_service.WelfareLeaveGrantRepository.create_batch",
                return_value=batch,
            ) as create_batch,
            patch(
                "service.welfare_leave_service.WelfareLeaveGrantRepository.bulk_create_targets",
                return_value=298,
            ) as create_targets,
            patch(
                "service.welfare_leave_service.transaction.atomic",
                return_value=nullcontext(),
            ),
        ):
            result = grant_welfare_leave(
                selection_token="signed-token",
                selection_mode="all",
                selected_employee_numbers=(),
                excluded_employee_numbers=excluded_numbers,
                apply_date=date(2026, 8, 28),
                processed_by="admin",
            )

        self.assertIs(result.batch, batch)
        exclude_selected.assert_called_once_with(
            scope_queryset,
            tuple(excluded_numbers),
        )
        self.assertEqual(create_batch.call_args.kwargs["target_count"], 298)
        self.assertEqual(
            list(create_targets.call_args.kwargs["employee_numbers"]),
            selected_numbers,
        )
        snapshot = json.loads(create_batch.call_args.kwargs["condition_snapshot"])
        self.assertEqual(snapshot["selection_mode"], "all")
        self.assertEqual(snapshot["excluded_target_count"], 2)


class DemoDataBuilderTests(SimpleTestCase):
    """데모 사원 800명이 반복 실행해도 같은 값으로 생성되는지 확인합니다."""

    def test_exactly_eight_hundred_unique_demo_employees_are_generated(self) -> None:
        """데모 사원번호는 800개이며 서로 중복되지 않아야 합니다."""
        rows = [
            build_demo_employee(sequence)
            for sequence in range(1, DEMO_EMPLOYEE_COUNT + 1)
        ]
        employee_numbers = {str(row["employee_no"]) for row in rows}
        self.assertEqual(DEMO_EMPLOYEE_COUNT, 800)
        self.assertEqual(len(rows), 800)
        self.assertEqual(len(employee_numbers), 800)
        self.assertIn("DEMO000001", employee_numbers)
        self.assertIn("DEMO000800", employee_numbers)
        self.assertEqual(
            {row["hire_date"].year for row in rows},
            set(range(2012, 2026)),
        )

    def test_demo_employee_generation_is_deterministic(self) -> None:
        """같은 순번을 다시 생성하면 모든 필드가 같아야 합니다."""
        self.assertEqual(build_demo_employee(42), build_demo_employee(42))


@override_settings(MYSQL_MOUNTED=False, DEMO_MODE=False, DATA_SOURCE_READY=False)
class UnmountedViewTests(SimpleTestCase):
    """실제 MySQL 없이 임의 데이터를 노출하지 않는 빈 화면을 확인합니다."""

    def test_grant_page_runs_without_mysql(self) -> None:
        """미마운트 상태에서도 일괄 부여 화면은 정상 응답해야 합니다."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "MySQL 미마운트")
        self.assertContains(response, "대상자 조건 설정")

    def test_history_page_runs_without_mysql(self) -> None:
        """미마운트 상태에서도 부여 내역 빈 화면은 정상 응답해야 합니다."""
        response = self.client.get("/history/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "표시할 부여 내역이 없습니다.")

    def test_mount_check_command_rejects_unmounted_state(self) -> None:
        """미마운트 상태의 관리 명령은 명확한 오류로 종료해야 합니다."""
        with self.assertRaisesRegex(CommandError, "MYSQL_MOUNTED=false"):
            call_command("check_mysql_mount")
