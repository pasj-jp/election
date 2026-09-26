"""選挙管理用の認証画面。トークンとパスワード検証はDjango標準を使う。"""
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth import views as auth_views
from django.urls import reverse_lazy


class ManagementLoginView(auth_views.LoginView):
    template_name = "election/auth/login.html"
    authentication_form = AdminAuthenticationForm
    next_page = reverse_lazy("election:management_cycle_list")


class ManagementPasswordChangeView(auth_views.PasswordChangeView):
    template_name = "election/auth/password_change.html"
    success_url = reverse_lazy("election:password_change_done")
