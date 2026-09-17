from datetime import timedelta

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from .admin import CandidateAdmin, order_admin_models
from .models import Candidate, Election, ElectionCycle, MemberSnapshot


CSV_HEADER = (
    "会員番号,会員名,会員種別,所属,所属所属機関名,"
    "ＭＬ用メールアドレス\n"
)


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


class AdminModelOrderTest(SimpleTestCase):

    def test_workflow_models_are_displayed_first(self):
        app_list = order_admin_models([
            {
                "app_label": "election",
                "models": [
                    {"object_name": "Election"},
                    {"object_name": "Candidate"},
                    {"object_name": "MemberSnapshot"},
                    {"object_name": "VoterParticipation"},
                ],
            },
        ])

        model_names = [
            model["object_name"]
            for model in app_list[0]["models"]
        ]

        self.assertEqual(
            model_names[:3],
            [
                "MemberSnapshot",
                "VoterParticipation",
                "Candidate",
            ],
        )


class ElectionVotingPeriodTest(SimpleTestCase):

    def test_open_election_during_period_is_available(self):
        now = timezone.now()
        election = Election(
            status=Election.Status.OPEN,
            start_at=now - timedelta(minutes=1),
            end_at=now + timedelta(minutes=1),
        )

        self.assertTrue(election.is_voting_open)

    def test_draft_or_outside_period_is_not_available(self):
        now = timezone.now()
        elections = [
            Election(
                status=Election.Status.DRAFT,
                start_at=now - timedelta(minutes=1),
                end_at=now + timedelta(minutes=1),
            ),
            Election(
                status=Election.Status.OPEN,
                start_at=now + timedelta(minutes=1),
                end_at=now + timedelta(minutes=2),
            ),
            Election(
                status=Election.Status.OPEN,
                start_at=now - timedelta(minutes=2),
                end_at=now - timedelta(minutes=1),
            ),
        ]

        for election in elections:
            with self.subTest(election=election):
                self.assertFalse(election.is_voting_open)


class MemberCsvImportAdminTest(TestCase):

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(self.user)
        self.cycle = ElectionCycle.objects.create(
            year=2027,
            name="2027年度選挙",
        )
        self.url = reverse("admin:election_membersnapshot_import_csv")

    def csv_file(self, body, encoding="utf-8-sig"):
        return SimpleUploadedFile(
            "members.csv",
            (CSV_HEADER + body).encode(encoding),
            content_type="text/csv",
        )

    def test_csv_can_be_uploaded_from_admin(self):
        response = self.client.post(
            self.url,
            {
                "cycle": self.cycle.pk,
                "csv_file": self.csv_file(
                    "m001,山田 太郎,正会員,企業関係,加速器株式会社,"
                    "taro@example.com\n"
                ),
            },
            follow=True,
        )

        self.assertRedirects(
            response,
            reverse("admin:election_membersnapshot_changelist"),
        )
        member = MemberSnapshot.objects.get(
            cycle=self.cycle,
            member_no="m001",
        )
        self.assertEqual(member.last_name, "山田")
        self.assertEqual(member.first_name, "太郎")
        self.assertTrue(member.is_eligible_voter)
        self.assertEqual(
            member.representative_category,
            MemberSnapshot.RepresentativeCategory.CORPORATE,
        )
        self.assertContains(response, "新規: 1件")

    def test_cp932_csv_is_supported(self):
        response = self.client.post(
            self.url,
            {
                "cycle": self.cycle.pk,
                "csv_file": self.csv_file(
                    "m002,佐藤 花子,準会員,大学,加速器大学,"
                    "hanako@example.com\n",
                    encoding="cp932",
                ),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            MemberSnapshot.objects.filter(member_no="m002").exists()
        )

    def test_invalid_csv_displays_error_without_importing(self):
        response = self.client.post(
            self.url,
            {
                "cycle": self.cycle.pk,
                "csv_file": self.csv_file(
                    "m001,姓だけ,正会員,大学,加速器大学,user@example.com\n"
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "会員名を姓と名に分割できません")
        self.assertFalse(MemberSnapshot.objects.exists())

    def test_anonymous_user_is_redirected_to_login(self):
        self.client.logout()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response.url)
