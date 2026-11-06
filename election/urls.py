"""選挙システムのURL。公開パスとelection名前空間をここでまとめる。"""
from django.urls import include, path

from . import views


app_name = "election"

urlpatterns = [
    path("accounts/", include("election.auth_urls")),
    path("management/", include("election.management_urls")),
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
