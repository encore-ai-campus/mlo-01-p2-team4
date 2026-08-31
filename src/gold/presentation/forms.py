"""대상자 조건 조회, 결과 내 검색과 일괄 부여 요청 Form입니다."""

from django import forms
from django.utils import timezone

from service.welfare_leave_service import FilterOptions


class EmployeeNumberListField(forms.Field):
    """동일한 이름의 체크박스로 전달된 사원번호 목록을 정규화합니다."""

    widget = forms.MultipleHiddenInput
    default_error_messages = {
        "required": "일괄 부여할 대상자를 한 명 이상 선택하세요.",
        "invalid": "선택한 대상자 정보가 올바르지 않습니다.",
    }

    def to_python(self, value: object) -> tuple[str, ...]:
        """중복과 공백을 제거한 사원번호 튜플을 반환합니다."""
        if value in (None, "", (), []):
            return ()
        raw_values = value if isinstance(value, (list, tuple)) else (value,)
        employee_numbers: list[str] = []
        seen: set[str] = set()
        for raw_value in raw_values:
            employee_no = str(raw_value).strip()
            if not employee_no or len(employee_no) > 100:
                raise forms.ValidationError(self.error_messages["invalid"])
            if employee_no not in seen:
                seen.add(employee_no)
                employee_numbers.append(employee_no)
        return tuple(employee_numbers)


class TargetConditionForm(forms.Form):
    """복지연차 정책과 선택형 사원 대상자 조건을 검증합니다."""

    department_code = forms.ChoiceField(required=False, label="부서")
    team_code = forms.ChoiceField(required=False, label="업무팀")
    position_code = forms.ChoiceField(required=False, label="직급")
    policy_code = forms.ChoiceField(required=False, label="부여 기준")
    tenure_condition = forms.ChoiceField(
        required=False,
        label="연차",
        choices=(
            ("", "전체"),
            ("12", "1년"),
            ("36", "3년"),
            ("60", "5년"),
            ("120", "10년"),
        ),
    )

    def __init__(
        self,
        *args: object,
        filter_options: FilterOptions,
        policy_choices: list[tuple[str, str]],
        **kwargs: object,
    ) -> None:
        """현재 데이터 소스에서 조회한 부서·팀·직급·정책 선택값을 등록합니다."""
        super().__init__(*args, **kwargs)
        self.fields["department_code"].choices = [
            ("", "전체"),
            *filter_options.departments,
        ]
        self.fields["team_code"].choices = [("", "전체"), *filter_options.teams]
        self.fields["position_code"].choices = [
            ("", "전체"),
            *filter_options.positions,
        ]
        self.fields["policy_code"].choices = [
            ("", "전체"),
            *policy_choices,
        ]


class ResultSearchForm(forms.Form):
    """조회된 전체 대상 범위 안의 사원번호 또는 이름 검색어를 검증합니다."""

    keyword = forms.CharField(
        required=False,
        max_length=100,
        label="결과 내 검색",
        widget=forms.TextInput(
            attrs={
                "placeholder": "이름 또는 직원아이디 검색",
                "autocomplete": "off",
            }
        ),
    )


class BulkGrantForm(forms.Form):
    """서명된 조회 범위, 체크된 대상, 적용일과 부여 확인값을 검증합니다."""

    selection_token = forms.CharField(widget=forms.HiddenInput)
    selection_mode = forms.ChoiceField(
        choices=(("manual", "개별 선택"), ("all", "전체 선택")),
        initial="manual",
        widget=forms.HiddenInput,
    )
    selected_employee_numbers = EmployeeNumberListField(required=False)
    excluded_employee_numbers = EmployeeNumberListField(required=False)
    apply_date = forms.DateField(
        label="적용일",
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    confirm_all = forms.BooleanField(
        required=True,
        label="조회된 전체 대상자에게 일괄 부여함을 확인합니다.",
    )

    def clean(self) -> dict[str, object]:
        """개별 선택 모드에서는 한 명 이상의 사원번호가 필요합니다."""
        cleaned_data = super().clean()
        if (
            cleaned_data.get("selection_mode") == "manual"
            and not cleaned_data.get("selected_employee_numbers")
        ):
            self.add_error(
                "selected_employee_numbers",
                "일괄 부여할 대상자를 한 명 이상 선택하세요.",
            )
        return cleaned_data
