from django.urls import path

from . import views


app_name = "election"

urlpatterns = [
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
