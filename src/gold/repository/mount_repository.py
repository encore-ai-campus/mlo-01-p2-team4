"""MySQL 연결과 필수 물리 스키마의 마운트 상태를 점검합니다."""

from dataclasses import dataclass

from django.conf import settings
from django.db import connection

from .models import (
    EmployeeRecord,
    WelfareLeaveGrantBatch,
    WelfareLeaveGrantTarget,
    WelfareLeavePolicy,
)


@dataclass(frozen=True)
class MountStatus:
    """MySQL 활성화·접속·스키마 검증 결과입니다."""

    mounted: bool
    connected: bool
    schema_valid: bool
    message: str
    missing_items: tuple[str, ...] = ()
    is_demo: bool = False


class DatabaseMountRepository:
    """Django 연결 설정으로 MySQL 및 필수 테이블을 확인합니다."""

    required_models = (
        EmployeeRecord,
        WelfareLeavePolicy,
        WelfareLeaveGrantBatch,
        WelfareLeaveGrantTarget,
    )

    @classmethod
    def check(cls, validate_schema: bool = False) -> MountStatus:
        """마운트 환경변수와 실제 연결 상태를 확인합니다."""
        if settings.DEMO_MODE:
            try:
                connection.ensure_connection()
                missing_items = cls.find_missing_schema_items()
                if missing_items:
                    return MountStatus(
                        mounted=True,
                        connected=False,
                        schema_valid=False,
                        message=(
                            "데모 DB가 준비되지 않았습니다. "
                            "python manage.py prepare_demo를 먼저 실행하세요."
                        ),
                        missing_items=missing_items,
                        is_demo=True,
                    )
                return MountStatus(
                    mounted=True,
                    connected=True,
                    schema_valid=True,
                    message=(
                        f"{settings.DEMO_EMPLOYEE_COUNT}명 샘플 데이터를 사용하는 "
                        "로컬 데모 모드입니다."
                    ),
                    is_demo=True,
                )
            except Exception as error:
                return MountStatus(
                    mounted=True,
                    connected=False,
                    schema_valid=False,
                    message=f"데모 DB 연결에 실패했습니다: {error.__class__.__name__}",
                    is_demo=True,
                )

        if not settings.MYSQL_MOUNTED:
            return MountStatus(
                mounted=False,
                connected=False,
                schema_valid=False,
                message="MySQL이 아직 마운트되지 않았습니다.",
            )

        try:
            connection.ensure_connection()
            if not validate_schema:
                return MountStatus(
                    mounted=True,
                    connected=True,
                    schema_valid=True,
                    message="MySQL 연결이 정상입니다.",
                )

            missing_items = cls.find_missing_schema_items()
            if missing_items:
                return MountStatus(
                    mounted=True,
                    connected=True,
                    schema_valid=False,
                    message="MySQL 연결은 정상이지만 필수 스키마가 부족합니다.",
                    missing_items=missing_items,
                )
            return MountStatus(
                mounted=True,
                connected=True,
                schema_valid=True,
                message="MySQL 연결과 필수 스키마가 모두 정상입니다.",
            )
        except Exception as error:
            return MountStatus(
                mounted=True,
                connected=False,
                schema_valid=False,
                message=f"MySQL 연결에 실패했습니다: {error.__class__.__name__}",
            )

    @classmethod
    def find_missing_schema_items(cls) -> tuple[str, ...]:
        """ORM 매핑에 필요한 테이블과 컬럼 중 누락된 항목을 반환합니다."""
        missing: list[str] = []
        table_names = set(connection.introspection.table_names())

        with connection.cursor() as cursor:
            for model in cls.required_models:
                table_name = model._meta.db_table
                if table_name not in table_names:
                    missing.append(f"table:{table_name}")
                    continue

                description = connection.introspection.get_table_description(
                    cursor,
                    table_name,
                )
                actual_columns = {column.name for column in description}
                for field in model._meta.local_fields:
                    if field.column not in actual_columns:
                        missing.append(f"column:{table_name}.{field.column}")

        return tuple(missing)
