"""연차 관리 화면 URL 설정입니다."""

from django.urls import path

from .views import grant_history_view, welfare_leave_grant_view


app_name = "presentation"

urlpatterns = [
    path("", welfare_leave_grant_view, name="welfare-leave-grant"),
    path("history/", grant_history_view, name="grant-history"),
]
