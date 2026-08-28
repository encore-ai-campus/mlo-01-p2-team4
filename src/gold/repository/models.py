"""마운트될 기존 MySQL 테이블을 매핑하는 비관리 Django ORM 모델입니다."""

from django.conf import settings
from django.db import models


class EmployeeRecord(models.Model):
    """부서·팀·직급·입사일을 보유한 기존 사원 정보 테이블입니다."""

    employee_no = models.CharField(
        max_length=50,
        primary_key=True,
        db_column=settings.MYSQL_EMPLOYEE_NO_COLUMN,
    )
    employee_name = models.CharField(
        max_length=100,
        db_column=settings.MYSQL_EMPLOYEE_NAME_COLUMN,
    )
    department_code = models.CharField(
        max_length=50,
        db_column=settings.MYSQL_EMPLOYEE_DEPARTMENT_CODE_COLUMN,
    )
    department_name = models.CharField(
        max_length=100,
        db_column=settings.MYSQL_EMPLOYEE_DEPARTMENT_NAME_COLUMN,
    )
    team_code = models.CharField(
        max_length=50,
        db_column=settings.MYSQL_EMPLOYEE_TEAM_CODE_COLUMN,
    )
    team_name = models.CharField(
        max_length=100,
        db_column=settings.MYSQL_EMPLOYEE_TEAM_NAME_COLUMN,
    )
    position_code = models.CharField(
        max_length=50,
        db_column=settings.MYSQL_EMPLOYEE_POSITION_CODE_COLUMN,
    )
    position_name = models.CharField(
        max_length=100,
        db_column=settings.MYSQL_EMPLOYEE_POSITION_NAME_COLUMN,
    )
    hire_date = models.DateField(
        db_column=settings.MYSQL_EMPLOYEE_HIRE_DATE_COLUMN,
    )
    active_yn = models.CharField(
        max_length=20,
        db_column=settings.MYSQL_EMPLOYEE_ACTIVE_COLUMN,
    )

    class Meta:
        managed = False
        db_table = settings.MYSQL_EMPLOYEE_TABLE

    def __str__(self) -> str:
        """사원번호와 이름을 결합한 표시값을 반환합니다."""
        return f"{self.employee_no} - {self.employee_name}"


class WelfareLeavePolicy(models.Model):
    """실제 사내 복지연차 종류와 부여 일수를 제공하는 정책 테이블입니다."""

    policy_code = models.CharField(
        max_length=50,
        primary_key=True,
        db_column=settings.MYSQL_POLICY_CODE_COLUMN,
    )
    policy_name = models.CharField(
        max_length=100,
        db_column=settings.MYSQL_POLICY_NAME_COLUMN,
    )
    criteria_name = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        db_column=settings.MYSQL_POLICY_CRITERIA_COLUMN,
    )
    criteria_detail = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        db_column=settings.MYSQL_POLICY_DETAIL_COLUMN,
    )
    grant_days = models.DecimalField(
        max_digits=7,
        decimal_places=1,
        db_column=settings.MYSQL_POLICY_DAYS_COLUMN,
    )
    active_yn = models.CharField(
        max_length=20,
        db_column=settings.MYSQL_POLICY_ACTIVE_COLUMN,
    )

    class Meta:
        managed = False
        db_table = settings.MYSQL_POLICY_TABLE

    def __str__(self) -> str:
        """정책명과 부여 일수를 표시합니다."""
        return f"{self.policy_name} ({self.grant_days}일)"


class WelfareLeaveGrantBatch(models.Model):
    """한 번의 조건 기반 일괄 부여 실행과 조건 스냅샷을 저장합니다."""

    batch_id = models.CharField(
        max_length=64,
        primary_key=True,
        db_column=settings.MYSQL_BATCH_ID_COLUMN,
    )
    policy_code = models.CharField(
        max_length=50,
        db_column=settings.MYSQL_BATCH_POLICY_CODE_COLUMN,
    )
    policy_name = models.CharField(
        max_length=100,
        db_column=settings.MYSQL_BATCH_POLICY_NAME_COLUMN,
    )
    condition_snapshot = models.TextField(
        db_column=settings.MYSQL_BATCH_CONDITION_COLUMN,
    )
    target_count = models.PositiveIntegerField(
        db_column=settings.MYSQL_BATCH_TARGET_COUNT_COLUMN,
    )
    grant_days = models.DecimalField(
        max_digits=7,
        decimal_places=1,
        db_column=settings.MYSQL_BATCH_GRANT_DAYS_COLUMN,
    )
    apply_date = models.DateField(
        db_column=settings.MYSQL_BATCH_APPLY_DATE_COLUMN,
    )
    processed_at = models.DateTimeField(
        db_column=settings.MYSQL_BATCH_PROCESSED_AT_COLUMN,
    )
    processed_by = models.CharField(
        max_length=100,
        db_column=settings.MYSQL_BATCH_PROCESSED_BY_COLUMN,
    )
    status = models.CharField(
        max_length=30,
        db_column=settings.MYSQL_BATCH_STATUS_COLUMN,
    )
    request_key = models.CharField(
        max_length=64,
        unique=True,
        db_column=settings.MYSQL_BATCH_REQUEST_KEY_COLUMN,
    )

    class Meta:
        managed = False
        db_table = settings.MYSQL_GRANT_BATCH_TABLE
        ordering = ("-processed_at",)

    def __str__(self) -> str:
        """배치 식별자와 처리 인원을 표시합니다."""
        return f"{self.batch_id}: {self.target_count}명"


class WelfareLeaveGrantTarget(models.Model):
    """일괄 부여 배치에 포함된 사원별 처리 결과를 저장합니다."""

    target_id = models.CharField(
        max_length=64,
        primary_key=True,
        db_column=settings.MYSQL_TARGET_ID_COLUMN,
    )
    batch_id = models.CharField(
        max_length=64,
        db_column=settings.MYSQL_TARGET_BATCH_ID_COLUMN,
    )
    employee_no = models.CharField(
        max_length=50,
        db_column=settings.MYSQL_TARGET_EMPLOYEE_NO_COLUMN,
    )
    grant_days = models.DecimalField(
        max_digits=7,
        decimal_places=1,
        db_column=settings.MYSQL_TARGET_GRANT_DAYS_COLUMN,
    )
    status = models.CharField(
        max_length=30,
        db_column=settings.MYSQL_TARGET_STATUS_COLUMN,
    )
    failure_reason = models.TextField(
        null=True,
        blank=True,
        db_column=settings.MYSQL_TARGET_FAILURE_REASON_COLUMN,
    )
    processed_at = models.DateTimeField(
        db_column=settings.MYSQL_TARGET_PROCESSED_AT_COLUMN,
    )

    class Meta:
        managed = False
        db_table = settings.MYSQL_GRANT_TARGET_TABLE
        constraints = [
            models.UniqueConstraint(
                fields=("batch_id", "employee_no"),
                name="uq_welfare_batch_employee",
            )
        ]

    def __str__(self) -> str:
        """배치와 대상 사원번호를 표시합니다."""
        return f"{self.batch_id} - {self.employee_no}"
