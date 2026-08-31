"""MySQL 접속과 ORM 필수 테이블·컬럼 매핑을 점검합니다."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from repository.mount_repository import DatabaseMountRepository


class Command(BaseCommand):
    """민감정보를 출력하지 않고 MySQL 마운트 상태를 검증합니다."""

    help = "MySQL 연결 및 복지연차 기능의 필수 테이블·컬럼을 점검합니다."

    def add_arguments(self, parser: object) -> None:
        """테이블 검증을 생략하는 연결 전용 선택지를 등록합니다."""
        parser.add_argument(
            "--connection-only",
            action="store_true",
            help="테이블·컬럼을 제외하고 MySQL 접속만 점검합니다.",
        )

    def handle(self, *args: object, **options: object) -> None:
        """마운트 상태를 확인하고 실패 시 안전한 오류 메시지를 반환합니다."""
        if not settings.MYSQL_MOUNTED:
            raise CommandError(
                "MYSQL_MOUNTED=false입니다. .env 설정 후 true로 변경하세요."
            )

        status = DatabaseMountRepository.check(
            validate_schema=not bool(options["connection_only"])
        )
        if not status.connected:
            raise CommandError(status.message)
        if not status.schema_valid:
            for item in status.missing_items:
                self.stderr.write(f"누락: {item}")
            raise CommandError(status.message)

        database = settings.DATABASES["default"]
        self.stdout.write(
            self.style.SUCCESS(
                "MySQL 마운트 점검 완료 "
                f"(host={database.get('HOST')}, database={database.get('NAME')})"
            )
        )
