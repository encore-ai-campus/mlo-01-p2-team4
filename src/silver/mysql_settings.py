"""MySQL 연결에 필요한 환경 설정을 안전하게 구성한다."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Self

from src.bronze.environment import load_dotenv


_REQUIRED_TEXT_KEYS = (
    "MYSQL_DATABASE",
    "MYSQL_USER",
    "MYSQL_HOST",
)
_PASSWORD_KEY = "MYSQL_PASSWORD"
_PORT_KEY = "MYSQL_PORT"


class MySQLSettingsError(ValueError):
    """MySQL 환경 설정이 없거나 고정 계약을 위반한 경우."""


@dataclass(frozen=True, slots=True)
class MySQLSettings:
    """검증을 마친 MySQL 연결 설정.

    ``password``는 객체 표현에 노출되지 않는다. 인스턴스는
    :meth:`from_environment`로 생성해 필수 환경변수를 검증한다.
    """

    database: str
    user: str
    password: str = field(repr=False)
    host: str
    port: int

    @classmethod
    def from_environment(cls) -> Self:
        """통합 ``src/.env``와 현재 프로세스 환경에서 설정을 읽는다.

        ``src.bronze.environment.load_dotenv``가 ``setdefault`` 방식으로
        값을 주입하므로 이미 존재하는 프로세스 환경변수가 항상 우선한다.
        오류에는 환경변수 이름만 포함하고 값은 포함하지 않는다.
        """

        load_dotenv()

        raw_port = os.environ.get(_PORT_KEY, "")
        missing_keys = tuple(
            key for key in _REQUIRED_TEXT_KEYS if not os.environ.get(key, "").strip()
        )
        if _PASSWORD_KEY not in os.environ:
            missing_keys = (*missing_keys, _PASSWORD_KEY)
        if not raw_port.strip():
            missing_keys = (*missing_keys, _PORT_KEY)
        if missing_keys:
            names = ", ".join(missing_keys)
            raise MySQLSettingsError(
                f"필수 MySQL 환경변수가 없거나 비어 있습니다: {names}"
            )

        try:
            port = int(raw_port)
        except ValueError:
            raise MySQLSettingsError(
                "MYSQL_PORT는 1부터 65535 사이의 정수여야 합니다."
            ) from None
        if not 1 <= port <= 65535:
            raise MySQLSettingsError("MYSQL_PORT는 1부터 65535 사이의 정수여야 합니다.")

        return cls(
            database=os.environ["MYSQL_DATABASE"],
            user=os.environ["MYSQL_USER"],
            password=os.environ[_PASSWORD_KEY],
            host=os.environ["MYSQL_HOST"],
            port=port,
        )

    def connection_kwargs(self) -> dict[str, object]:
        """``mysql.connector.connect``에 전달할 고정 인자를 반환한다."""

        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "database": self.database,
            "autocommit": False,
            "charset": "utf8mb4",
            "connection_timeout": 10,
        }


def from_environment() -> MySQLSettings:
    """현재 환경에서 검증된 :class:`MySQLSettings`를 생성한다."""

    return MySQLSettings.from_environment()


__all__ = ["MySQLSettings", "MySQLSettingsError", "from_environment"]
