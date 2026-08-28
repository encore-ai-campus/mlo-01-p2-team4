"""화면 계층 Django 앱 설정입니다."""

from django.apps import AppConfig


class PresentationConfig(AppConfig):
    """연차 관리 화면 앱 설정."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "presentation"
