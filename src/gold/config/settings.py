"""사내 연차 관리 프로젝트의 Django 설정입니다."""

import os
from pathlib import Path

from dotenv import load_dotenv
from django.core.exceptions import ImproperlyConfigured


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    """환경변수 문자열을 Boolean 값으로 변환합니다."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    """쉼표로 구분된 환경변수를 문자열 목록으로 변환합니다."""
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "development-only-change-this-key")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost")
MYSQL_MOUNTED = env_bool("MYSQL_MOUNTED", False)
DEMO_MODE = env_bool("DEMO_MODE", False)

if MYSQL_MOUNTED and DEMO_MODE:
    raise ImproperlyConfigured(
        "MYSQL_MOUNTED와 DEMO_MODE는 동시에 true로 설정할 수 없습니다."
    )

# Repository와 Service는 실제 MySQL과 로컬 데모 DB를 같은 업무 흐름으로 사용합니다.
DATA_SOURCE_READY = MYSQL_MOUNTED or DEMO_MODE
DEMO_EMPLOYEE_COUNT = int(os.getenv("DEMO_EMPLOYEE_COUNT", "800"))

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "presentation",
    "repository",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

if DEMO_MODE:
    # 데모 데이터와 부여 결과를 서버 재시작 후에도 확인할 수 있도록 파일 DB를 씁니다.
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "demo.sqlite3",
        }
    }
elif not MYSQL_MOUNTED:
    # MySQL 마운트 전에도 빈 화면과 설정을 점검할 수 있도록 메모리 DB를 사용합니다.
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:",
        }
    }
    # 미마운트 모드는 업무 테이블을 사용하지 않으므로 마이그레이션 점검을 비활성화합니다.
    MIGRATION_MODULES = {
        "contenttypes": None,
        "presentation": None,
        "repository": None,
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": os.getenv("MYSQL_DATABASE", ""),
            "USER": os.getenv("MYSQL_USER", ""),
            "PASSWORD": os.getenv("MYSQL_PASSWORD", ""),
            "HOST": os.getenv("MYSQL_HOST", "127.0.0.1"),
            "PORT": os.getenv("MYSQL_PORT", "3306"),
            "CONN_MAX_AGE": int(os.getenv("MYSQL_CONN_MAX_AGE", "60")),
            "OPTIONS": {
                "charset": "utf8mb4",
                "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
            },
        }
    }

LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# 실제 물리 테이블·컬럼명이 확정되면 .env 값만 교체할 수 있습니다.
MYSQL_EMPLOYEE_TABLE = os.getenv("MYSQL_EMPLOYEE_TABLE", "employee_info")
MYSQL_EMPLOYEE_NO_COLUMN = os.getenv("MYSQL_EMPLOYEE_NO_COLUMN", "ngr_no")
MYSQL_EMPLOYEE_NAME_COLUMN = os.getenv("MYSQL_EMPLOYEE_NAME_COLUMN", "mgr_nm")
MYSQL_EMPLOYEE_DEPARTMENT_CODE_COLUMN = os.getenv(
    "MYSQL_EMPLOYEE_DEPARTMENT_CODE_COLUMN", "department_code"
)
MYSQL_EMPLOYEE_DEPARTMENT_NAME_COLUMN = os.getenv(
    "MYSQL_EMPLOYEE_DEPARTMENT_NAME_COLUMN", "department_name"
)
MYSQL_EMPLOYEE_TEAM_CODE_COLUMN = os.getenv(
    "MYSQL_EMPLOYEE_TEAM_CODE_COLUMN", "team_code"
)
MYSQL_EMPLOYEE_TEAM_NAME_COLUMN = os.getenv(
    "MYSQL_EMPLOYEE_TEAM_NAME_COLUMN", "team_name"
)
MYSQL_EMPLOYEE_POSITION_CODE_COLUMN = os.getenv(
    "MYSQL_EMPLOYEE_POSITION_CODE_COLUMN", "position_code"
)
MYSQL_EMPLOYEE_POSITION_NAME_COLUMN = os.getenv(
    "MYSQL_EMPLOYEE_POSITION_NAME_COLUMN", "position_name"
)
MYSQL_EMPLOYEE_ACTIVE_COLUMN = os.getenv("MYSQL_EMPLOYEE_ACTIVE_COLUMN", "mgr_yn")
MYSQL_EMPLOYEE_HIRE_DATE_COLUMN = os.getenv(
    "MYSQL_EMPLOYEE_HIRE_DATE_COLUMN", "mgr_hire_dtm"
)
MYSQL_EMPLOYEE_ACTIVE_VALUE = os.getenv("MYSQL_EMPLOYEE_ACTIVE_VALUE", "Y")

MYSQL_POLICY_TABLE = os.getenv("MYSQL_POLICY_TABLE", "welfare_leave_policy")
MYSQL_POLICY_CODE_COLUMN = os.getenv("MYSQL_POLICY_CODE_COLUMN", "policy_code")
MYSQL_POLICY_NAME_COLUMN = os.getenv("MYSQL_POLICY_NAME_COLUMN", "policy_name")
MYSQL_POLICY_CRITERIA_COLUMN = os.getenv(
    "MYSQL_POLICY_CRITERIA_COLUMN", "criteria_name"
)
MYSQL_POLICY_DETAIL_COLUMN = os.getenv(
    "MYSQL_POLICY_DETAIL_COLUMN", "criteria_detail"
)
MYSQL_POLICY_DAYS_COLUMN = os.getenv("MYSQL_POLICY_DAYS_COLUMN", "grant_days")
MYSQL_POLICY_ACTIVE_COLUMN = os.getenv("MYSQL_POLICY_ACTIVE_COLUMN", "active_yn")
MYSQL_POLICY_ACTIVE_VALUE = os.getenv("MYSQL_POLICY_ACTIVE_VALUE", "Y")

MYSQL_GRANT_BATCH_TABLE = os.getenv(
    "MYSQL_GRANT_BATCH_TABLE", "welfare_leave_grant_batch"
)
MYSQL_BATCH_ID_COLUMN = os.getenv("MYSQL_BATCH_ID_COLUMN", "batch_id")
MYSQL_BATCH_POLICY_CODE_COLUMN = os.getenv(
    "MYSQL_BATCH_POLICY_CODE_COLUMN", "policy_code"
)
MYSQL_BATCH_POLICY_NAME_COLUMN = os.getenv(
    "MYSQL_BATCH_POLICY_NAME_COLUMN", "policy_name"
)
MYSQL_BATCH_CONDITION_COLUMN = os.getenv(
    "MYSQL_BATCH_CONDITION_COLUMN", "condition_snapshot"
)
MYSQL_BATCH_TARGET_COUNT_COLUMN = os.getenv(
    "MYSQL_BATCH_TARGET_COUNT_COLUMN", "target_count"
)
MYSQL_BATCH_GRANT_DAYS_COLUMN = os.getenv(
    "MYSQL_BATCH_GRANT_DAYS_COLUMN", "grant_days"
)
MYSQL_BATCH_APPLY_DATE_COLUMN = os.getenv(
    "MYSQL_BATCH_APPLY_DATE_COLUMN", "apply_date"
)
MYSQL_BATCH_PROCESSED_AT_COLUMN = os.getenv(
    "MYSQL_BATCH_PROCESSED_AT_COLUMN", "processed_at"
)
MYSQL_BATCH_PROCESSED_BY_COLUMN = os.getenv(
    "MYSQL_BATCH_PROCESSED_BY_COLUMN", "processed_by"
)
MYSQL_BATCH_STATUS_COLUMN = os.getenv("MYSQL_BATCH_STATUS_COLUMN", "status")
MYSQL_BATCH_REQUEST_KEY_COLUMN = os.getenv(
    "MYSQL_BATCH_REQUEST_KEY_COLUMN", "request_key"
)

MYSQL_GRANT_TARGET_TABLE = os.getenv(
    "MYSQL_GRANT_TARGET_TABLE", "welfare_leave_grant_target"
)
MYSQL_TARGET_ID_COLUMN = os.getenv("MYSQL_TARGET_ID_COLUMN", "target_id")
MYSQL_TARGET_BATCH_ID_COLUMN = os.getenv(
    "MYSQL_TARGET_BATCH_ID_COLUMN", "batch_id"
)
MYSQL_TARGET_EMPLOYEE_NO_COLUMN = os.getenv(
    "MYSQL_TARGET_EMPLOYEE_NO_COLUMN", "employee_no"
)
MYSQL_TARGET_GRANT_DAYS_COLUMN = os.getenv(
    "MYSQL_TARGET_GRANT_DAYS_COLUMN", "grant_days"
)
MYSQL_TARGET_STATUS_COLUMN = os.getenv("MYSQL_TARGET_STATUS_COLUMN", "status")
MYSQL_TARGET_FAILURE_REASON_COLUMN = os.getenv(
    "MYSQL_TARGET_FAILURE_REASON_COLUMN", "failure_reason"
)
MYSQL_TARGET_PROCESSED_AT_COLUMN = os.getenv(
    "MYSQL_TARGET_PROCESSED_AT_COLUMN", "processed_at"
)

WELFARE_RESULT_PAGE_SIZE = int(os.getenv("WELFARE_RESULT_PAGE_SIZE", "20"))
WELFARE_HISTORY_PAGE_SIZE = int(os.getenv("WELFARE_HISTORY_PAGE_SIZE", "20"))
WELFARE_SELECTION_TOKEN_MAX_AGE = int(
    os.getenv("WELFARE_SELECTION_TOKEN_MAX_AGE", "1800")
)
WELFARE_OPERATOR_ID = os.getenv("WELFARE_OPERATOR_ID", "").strip()
if DEMO_MODE and not WELFARE_OPERATOR_ID:
    WELFARE_OPERATOR_ID = "demo-admin"
WELFARE_BULK_BATCH_SIZE = int(os.getenv("WELFARE_BULK_BATCH_SIZE", "1000"))
