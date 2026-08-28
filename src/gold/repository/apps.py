"""Repository Django 앱 설정입니다."""

from django.apps import AppConfig


class RepositoryConfig(AppConfig):
    """기존 MySQL 테이블 모델을 등록합니다."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "repository"
