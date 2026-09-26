"""選挙管理用の認証画面。トークンとパスワード検証はDjango標準を使う。"""
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth import views as auth_views
from django.contrib.auth.views import redirect_to_login
from django.urls import reverse, reverse_lazy
from django.views.decorators.http import require_GET


class ManagementLoginView(auth_views.LoginView):
    template_name = "election/auth/login.html"
    authentication_form = AdminAuthenticationForm
    next_page = reverse_lazy("election:management_cycle_list")


class ManagementPasswordChangeView(auth_views.PasswordChangeView):
    template_name = "election/auth/password_change.html"
    success_url = reverse_lazy("election:password_change_done")


@require_GET
def admin_login_redirect(request):
    # 認証は専用画面だけで行い、nextの安全性はLoginViewで検証する。
    return redirect_to_login(
        request.GET.get("next") or reverse("admin:index"),
        login_url="election:login",
    )
