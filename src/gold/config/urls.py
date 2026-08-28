"""프로젝트 최상위 URL 설정입니다."""

from django.urls import include, path


urlpatterns = [
    path("", include("presentation.urls")),
]
