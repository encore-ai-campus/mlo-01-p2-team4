"""MySQL 환경 설정의 격리된 단위 테스트."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import src.silver.mysql_settings as mysql_settings_module
from src.bronze.environment import load_dotenv
from src.silver.mysql_settings import MySQLSettingsError


MYSQL_ENV_KEYS = (
    "MYSQL_DATABASE",
    "MYSQL_USER",
    "MYSQL_PASSWORD",
    "MYSQL_HOST",
    "MYSQL_PORT",
)
TEST_PASSWORD = "unit-test-secret"


def _replace_mysql_environment(
    monkeypatch: pytest.MonkeyPatch,
    values: dict[str, str],
) -> None:
    """실제 MySQL 환경과 격리된 테스트용 process env를 구성한다."""

    for key in MYSQL_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def _valid_environment(**changes: str) -> dict[str, str]:
    values = {
        "MYSQL_DATABASE": "silver_test",
        "MYSQL_USER": "silver_user",
        "MYSQL_PASSWORD": TEST_PASSWORD,
        "MYSQL_HOST": "127.0.0.1",
        "MYSQL_PORT": "3306",
    }
    values.update(changes)
    return values


def test_from_environment_loads_temp_dotenv_with_process_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """임시 dotenv를 읽되 process env가 우선하고 password는 repr에서 숨긴다."""

    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            (
                "MYSQL_DATABASE=dotenv_database",
                "MYSQL_USER=dotenv_user",
                "MYSQL_PASSWORD=dotenv_secret",
                "MYSQL_HOST=mysql.internal",
                "MYSQL_PORT=3307",
            )
        ),
        encoding="utf-8",
    )
    _replace_mysql_environment(
        monkeypatch,
        {
            "MYSQL_USER": "process_user",
            "MYSQL_PASSWORD": TEST_PASSWORD,
        },
    )
    monkeypatch.setattr(
        mysql_settings_module,
        "load_dotenv",
        lambda: load_dotenv(env_path, environ=os.environ, required=True),
    )

    settings = mysql_settings_module.from_environment()

    assert settings.database == "dotenv_database"
    assert settings.user == "process_user"
    assert settings.password == TEST_PASSWORD
    assert settings.host == "mysql.internal"
    assert settings.port == 3307
    assert TEST_PASSWORD not in repr(settings)
    assert settings.connection_kwargs() == {
        "host": "mysql.internal",
        "port": 3307,
        "user": "process_user",
        "password": TEST_PASSWORD,
        "database": "dotenv_database",
        "autocommit": False,
        "charset": "utf8mb4",
        "connection_timeout": 10,
    }


def test_missing_environment_key_names_key_without_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """필수 key 누락 오류는 key 이름만 밝히고 password 값은 숨긴다."""

    values = _valid_environment()
    del values["MYSQL_HOST"]
    _replace_mysql_environment(monkeypatch, values)
    monkeypatch.setattr(mysql_settings_module, "load_dotenv", lambda: False)

    with pytest.raises(MySQLSettingsError, match="MYSQL_HOST") as error:
        mysql_settings_module.from_environment()

    assert TEST_PASSWORD not in str(error.value)


def test_empty_password_is_allowed_for_passwordless_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """비밀번호 없는 로컬 계정은 빈 MYSQL_PASSWORD 값을 그대로 사용한다."""

    _replace_mysql_environment(
        monkeypatch,
        _valid_environment(MYSQL_PASSWORD=""),
    )
    monkeypatch.setattr(mysql_settings_module, "load_dotenv", lambda: False)

    settings = mysql_settings_module.from_environment()

    assert settings.password == ""
    assert settings.connection_kwargs()["password"] == ""


def test_missing_password_key_fails_even_though_empty_value_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """빈 값과 key 자체의 누락을 구분한다."""

    values = _valid_environment()
    del values["MYSQL_PASSWORD"]
    _replace_mysql_environment(monkeypatch, values)
    monkeypatch.setattr(mysql_settings_module, "load_dotenv", lambda: False)

    with pytest.raises(MySQLSettingsError, match="MYSQL_PASSWORD"):
        mysql_settings_module.from_environment()


@pytest.mark.parametrize("port", ["not-an-integer", "0", "65536"])
def test_invalid_or_out_of_range_port_fails_without_secret(
    port: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """정수가 아니거나 범위를 벗어난 port는 설정 생성을 중단한다."""

    _replace_mysql_environment(monkeypatch, _valid_environment(MYSQL_PORT=port))
    monkeypatch.setattr(mysql_settings_module, "load_dotenv", lambda: False)

    with pytest.raises(MySQLSettingsError, match="MYSQL_PORT") as error:
        mysql_settings_module.from_environment()

    assert TEST_PASSWORD not in str(error.value)
