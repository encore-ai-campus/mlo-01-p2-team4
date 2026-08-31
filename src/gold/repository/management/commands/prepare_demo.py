"""로컬 SQLite에 화면 검증용 사원과 복지연차 정책을 준비합니다."""

from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from repository.models import (
    EmployeeRecord,
    WelfareLeaveGrantBatch,
    WelfareLeaveGrantTarget,
    WelfareLeavePolicy,
)


DEMO_EMPLOYEE_COUNT = settings.DEMO_EMPLOYEE_COUNT

SURNAMES = (
    "김",
    "이",
    "박",
    "최",
    "정",
    "강",
    "조",
    "윤",
    "장",
    "임",
)

GIVEN_NAMES = (
    "민서",
    "도윤",
    "지우",
    "서준",
    "하은",
    "현우",
    "수빈",
    "지훈",
    "예린",
    "준호",
    "다은",
    "시우",
    "유진",
    "건우",
    "채원",
    "우진",
    "소연",
    "민재",
    "지민",
    "태윤",
    "가은",
    "승현",
    "나연",
    "재민",
    "은서",
    "성민",
    "아린",
    "동현",
    "세은",
    "진우",
)

ORGANIZATIONS = (
    ("D001", "데이터전략부", (("T001", "분석팀"), ("T002", "AI플랫폼팀"), ("T003", "데이터품질팀"))),
    ("D002", "경영지원부", (("T004", "인사팀"), ("T005", "재무팀"), ("T006", "총무팀"))),
    ("D003", "서비스운영부", (("T007", "운영팀"), ("T008", "고객지원팀"), ("T009", "서비스기획팀"))),
    ("D004", "제품개발부", (("T010", "개발1팀"), ("T011", "개발2팀"), ("T012", "QA팀"))),
    ("D005", "마케팅부", (("T013", "마케팅팀"), ("T014", "콘텐츠팀"), ("T015", "브랜드팀"))),
)

POSITIONS = (
    ("P001", "사원"),
    ("P002", "대리"),
    ("P003", "과장"),
    ("P004", "차장"),
    ("P005", "팀장"),
)

DEMO_POLICIES = (
    {
        "policy_code": "DEMO-LONG-SERVICE",
        "policy_name": "장기근속 복지연차(샘플)",
        "criteria_name": "근속기간",
        "criteria_detail": "5년차 근속자 예시",
        "grant_days": Decimal("1.0"),
        "active_yn": "Y",
    },
    {
        "policy_code": "DEMO-REFRESH",
        "policy_name": "리프레시 복지연차(샘플)",
        "criteria_name": "근속기간",
        "criteria_detail": "장기근속자 재충전 예시",
        "grant_days": Decimal("2.0"),
        "active_yn": "Y",
    },
    {
        "policy_code": "DEMO-WELLNESS",
        "policy_name": "건강지원 복지연차(샘플)",
        "criteria_name": "복지지원",
        "criteria_detail": "건강검진 지원 예시",
        "grant_days": Decimal("0.5"),
        "active_yn": "Y",
    },
)


def build_demo_employee(sequence: int) -> dict[str, object]:
    """순번만으로 항상 같은 데모 사원 한 명의 필드값을 생성합니다."""
    zero_based = sequence - 1
    department_code, department_name, teams = ORGANIZATIONS[zero_based % len(ORGANIZATIONS)]
    position_index = (zero_based // len(ORGANIZATIONS)) % len(POSITIONS)
    position_code, position_name = POSITIONS[position_index]
    team_index = (
        zero_based // (len(ORGANIZATIONS) * len(POSITIONS))
    ) % len(teams)
    team_code, team_name = teams[team_index]
    surname = SURNAMES[
        (zero_based // len(GIVEN_NAMES)) % len(SURNAMES)
    ]
    given_name = GIVEN_NAMES[zero_based % len(GIVEN_NAMES)]

    # 2012~2025년 사이에 입사일을 고르게 배치해 근속기간 필터를 시험합니다.
    hire_year = 2012 + ((zero_based * 5) % 14)
    hire_month = ((zero_based * 7) % 12) + 1
    hire_day = ((zero_based * 11) % 28) + 1

    return {
        "employee_no": f"DEMO{sequence:06d}",
        "employee_name": f"{surname}{given_name}",
        "department_code": department_code,
        "department_name": department_name,
        "team_code": team_code,
        "team_name": team_name,
        "position_code": position_code,
        "position_name": position_name,
        "hire_date": date(hire_year, hire_month, hire_day),
        "active_yn": "Y",
    }


class Command(BaseCommand):
    """데모 전용 테이블을 만든 뒤 재실행 가능한 방식으로 샘플 데이터를 저장합니다."""

    help = "DEMO_MODE=true에서 로컬 SQLite 테이블과 설정된 수의 사원을 준비합니다."

    def handle(self, *args: object, **options: object) -> None:
        """운영 MySQL을 차단하고 데모 스키마 생성과 데이터 적재를 실행합니다."""
        if not settings.DEMO_MODE:
            raise CommandError(
                "DEMO_MODE=false입니다. 데모 실행 환경에서만 이 명령을 사용할 수 있습니다."
            )
        if settings.MYSQL_MOUNTED:
            raise CommandError("운영 MySQL이 활성화된 상태에서는 데모 데이터를 만들 수 없습니다.")

        self._apply_framework_migrations()
        self._create_missing_tables()
        self._seed_policies()
        self._seed_employees()

        employee_count = EmployeeRecord.objects.filter(
            employee_no__startswith="DEMO"
        ).count()
        policy_count = WelfareLeavePolicy.objects.filter(
            policy_code__startswith="DEMO-"
        ).count()
        self.stdout.write(
            self.style.SUCCESS(
                f"데모 준비 완료: 사원 {employee_count}명, 복지연차 정책 {policy_count}건"
            )
        )

    def _create_missing_tables(self) -> None:
        """데모 SQLite에 필요한 비관리 ORM 테이블이 없을 때만 생성합니다."""
        required_models = (
            EmployeeRecord,
            WelfareLeavePolicy,
            WelfareLeaveGrantBatch,
            WelfareLeaveGrantTarget,
        )
        existing_tables = set(connection.introspection.table_names())
        with connection.schema_editor() as schema_editor:
            for model in required_models:
                if model._meta.db_table not in existing_tables:
                    schema_editor.create_model(model)
                    existing_tables.add(model._meta.db_table)

    def _apply_framework_migrations(self) -> None:
        """개발 서버 경고가 남지 않도록 Django 기본 메타 테이블을 준비합니다."""
        call_command(
            "migrate",
            "contenttypes",
            interactive=False,
            verbosity=0,
        )

    def _seed_policies(self) -> None:
        """샘플임을 이름에 표시한 데모 정책 세 건을 반복 실행 가능하게 저장합니다."""
        with transaction.atomic():
            for policy in DEMO_POLICIES:
                policy_code = str(policy["policy_code"])
                defaults = {
                    key: value
                    for key, value in policy.items()
                    if key != "policy_code"
                }
                WelfareLeavePolicy.objects.update_or_create(
                    policy_code=policy_code,
                    defaults=defaults,
                )

    def _seed_employees(self) -> None:
        """설정된 수만큼 순번 기반의 재현 가능한 샘플 사원을 저장합니다."""
        with transaction.atomic():
            for sequence in range(1, DEMO_EMPLOYEE_COUNT + 1):
                employee = build_demo_employee(sequence)
                employee_no = str(employee.pop("employee_no"))
                EmployeeRecord.objects.update_or_create(
                    employee_no=employee_no,
                    defaults=employee,
                )
