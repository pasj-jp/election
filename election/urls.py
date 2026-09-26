from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from .auth_views import ManagementLoginView, ManagementPasswordChangeView

from . import views


app_name = "election"

urlpatterns = [
    path("accounts/login/", ManagementLoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(next_page=reverse_lazy("election:login")), name="logout"),
    path("accounts/password-change/", ManagementPasswordChangeView.as_view(), name="password_change"),
    path("accounts/password-change/done/", auth_views.PasswordChangeDoneView.as_view(
        template_name="election/auth/password_change_done.html"), name="password_change_done"),
    path("accounts/password-reset/", auth_views.PasswordResetView.as_view(
        template_name="election/auth/password_reset.html",
        email_template_name="election/auth/password_reset_email.txt",
        subject_template_name="election/auth/password_reset_subject.txt",
        success_url=reverse_lazy("election:password_reset_done")), name="password_reset"),
    path("accounts/password-reset/done/", auth_views.PasswordResetDoneView.as_view(
        template_name="election/auth/password_reset_done.html"), name="password_reset_done"),
    path("accounts/reset/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(
        template_name="election/auth/password_reset_confirm.html",
        success_url=reverse_lazy("election:password_reset_complete")), name="password_reset_confirm"),
    path("accounts/reset/complete/", auth_views.PasswordResetCompleteView.as_view(
        template_name="election/auth/password_reset_complete.html"), name="password_reset_complete"),
    path("management/", views.management_cycle_list, name="management_cycle_list"),
    path("management/new/", views.management_cycle_form, name="management_cycle_create"),
    path("management/<int:cycle_year>/", views.management_cycle_detail, name="management_cycle_detail"),
    path("management/<int:cycle_year>/edit/", views.management_cycle_form, name="management_cycle_edit"),
    path("management/<int:cycle_year>/elections/<int:election_id>/voters/", views.management_voters, name="management_voters"),
    path(
        "management/<int:cycle_year>/elections/<int:election_id>/status/",
        views.management_election_status,
        name="management_election_status",
    ),
    path("management/<int:cycle_year>/elections/<int:election_id>/candidates/", views.management_candidates, name="management_candidates"),
    path("management/<int:cycle_year>/elections/<int:election_id>/candidates/<int:candidate_id>/status/", views.management_candidate_status, name="management_candidate_status"),
    path("management/<int:cycle_year>/elections/<int:election_id>/candidates/add/<int:member_id>/", views.management_candidate_add, name="management_candidate_add"),
    path("management/<int:cycle_year>/elections/<int:election_id>/candidates/<int:candidate_id>/remove/", views.management_candidate_remove, name="management_candidate_remove"),
    path("management/<int:cycle_year>/elections/<int:election_id>/email/", views.management_email_preview, name="management_email_preview"),
    path("management/<int:cycle_year>/elections/<int:election_id>/email/send/", views.management_email_send, name="management_email_send"),
    path("management/<int:cycle_year>/elections/<int:election_id>/result-csv/", views.management_result_csv, name="management_result_csv"),
    path("management/<int:cycle_year>/elections/<int:election_id>/count/", views.management_count_preview, name="management_count_preview"),
    path("management/<int:cycle_year>/elections/<int:election_id>/count/confirm/", views.management_count_confirm, name="management_count_confirm"),
    path("management/<int:cycle_year>/elections/<int:election_id>/paper-ballot/", views.management_paper_ballot, name="management_paper_ballot"),
    path(
        "",
        views.home,
        name="home",
    ),
    path(
        "v/<str:token>/",
        views.vote_entry,
        name="vote_entry",
    ),
    path(
        "ballot/",
        views.ballot,
        name="ballot",
    ),
    path(
        "ballot/confirm/",
        views.ballot_confirm,
        name="ballot_confirm",
    ),
    path(
        "ballot/submit/",
        views.ballot_submit,
        name="ballot_submit",
    ),
]
