from django.contrib import admin
from django.test import SimpleTestCase
from django.urls import reverse

from .admin import CandidateAdmin
from .models import Candidate, Election


class HomeViewTest(SimpleTestCase):

    def test_home_page_is_displayed(self):
        response = self.client.get(reverse("election:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "加速器学会選挙システム",
        )
        self.assertContains(
            response,
            reverse("admin:index"),
        )


class CandidateAdminTest(SimpleTestCase):

    def setUp(self):
        self.model_admin = CandidateAdmin(
            Candidate,
            admin.site,
        )

    def candidate_with_vote_count(
        self,
        election_status,
        vote_count=3,
    ):
        candidate = Candidate(
            election=Election(status=election_status),
        )
        candidate._vote_count = vote_count
        return candidate

    def test_vote_count_is_hidden_before_counting(self):
        for status in (
            Election.Status.DRAFT,
            Election.Status.OPEN,
            Election.Status.CLOSED,
        ):
            with self.subTest(status=status):
                candidate = self.candidate_with_vote_count(
                    status
                )

                self.assertEqual(
                    self.model_admin.vote_count_display(
                        candidate
                    ),
                    "—",
                )

    def test_vote_count_is_displayed_after_counting(self):
        candidate = self.candidate_with_vote_count(
            Election.Status.COUNTED
        )

        self.assertEqual(
            self.model_admin.vote_count_display(candidate),
            3,
        )
