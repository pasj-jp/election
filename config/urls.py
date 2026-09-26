"""管理サイトと選挙システムのルートURL。"""
from django.contrib import admin
from django.urls import include, path

from election.auth_views import admin_login_redirect

urlpatterns = [
    path("admin/login/", admin_login_redirect),
    path('admin/', admin.site.urls),
    path("", include("election.urls")),
]
