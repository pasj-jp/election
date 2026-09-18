from datetime import timedelta

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import CommandError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .admin import CandidateAdmin, order_admin_models
from .forms import ElectionAdminForm
from .management.command_utils import get_selected_election
from .models import (
    Ballot,
    BallotChoice,
    Candidate,
    Election,
    ElectionCycle,
    LotteryDraw,
    MemberSnapshot,
    VoterParticipation,
)
from .services.counting import preview_election_count
from .views import should_show_candidate_route_labels, validate_vote


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


class PreliminaryElectionCandidateGenerationTest(TestCase):

    def setUp(self):
        self.cycle = ElectionCycle.objects.create(
            year=2028,
            name="2028年度選挙",
        )
        self.eligible_member = MemberSnapshot.objects.create(
            cycle=self.cycle,
            member_no="m001",
            last_name="山田",
            first_name="太郎",
            email="taro@example.com",
            employee_type="正会員",
            representative_category=(
                MemberSnapshot.RepresentativeCategory.GENERAL
            ),
            is_eligible_voter=True,
        )
        self.ineligible_member = MemberSnapshot.objects.create(
            cycle=self.cycle,
            member_no="m002",
            last_name="佐藤",
            first_name="花子",
            email="hanako@example.com",
            employee_type="準会員",
            representative_category=(
                MemberSnapshot.RepresentativeCategory.GENERAL
            ),
            is_eligible_voter=False,
        )

    def create_election(self, phase):
        now = timezone.now()
        return Election.objects.create(
            cycle=self.cycle,
            office=Election.Office.PRESIDENT,
            phase=phase,
            start_at=now,
            end_at=now + timedelta(days=1),
        )

    def test_candidates_are_generated_when_preliminary_is_created(self):
        election = self.create_election(Election.Phase.PRELIMINARY)

        candidates = Candidate.objects.filter(election=election)
        self.assertEqual(candidates.count(), 1)
        self.assertEqual(candidates.get().member, self.eligible_member)
        self.assertEqual(candidates.get().status, Candidate.Status.ELIGIBLE)
        voters = VoterParticipation.objects.filter(election=election)
        self.assertEqual(voters.count(), 1)
        self.assertEqual(voters.get().member, self.eligible_member)

    def test_only_voters_are_generated_for_final_election(self):
        election = self.create_election(Election.Phase.FINAL)

        self.assertFalse(Candidate.objects.filter(election=election).exists())
        voters = VoterParticipation.objects.filter(election=election)
        self.assertEqual(voters.count(), 1)
        self.assertEqual(voters.get().member, self.eligible_member)

    def test_editing_preliminary_does_not_generate_candidates_again(self):
        election = self.create_election(Election.Phase.PRELIMINARY)

        election.end_at += timedelta(days=1)
        election.save()

        self.assertEqual(
            Candidate.objects.filter(election=election).count(),
            1,
        )
        self.assertEqual(
            VoterParticipation.objects.filter(election=election).count(),
            1,
        )


class CountPreviewTest(TestCase):

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="count-admin",
            email="count@example.com",
            password="password",
        )
        self.client.force_login(self.user)
        self.cycle = ElectionCycle.objects.create(
            year=2029,
            name="2029年度選挙",
        )
        now = timezone.now()
        self.election = Election.objects.create(
            cycle=self.cycle,
            office=Election.Office.PRESIDENT,
            phase=Election.Phase.FINAL,
            status=Election.Status.COUNTED,
            start_at=now - timedelta(days=2),
            end_at=now - timedelta(days=1),
        )
        candidates = []
        for number, name in (("m001", "山田"), ("m002", "佐藤")):
            member = MemberSnapshot.objects.create(
                cycle=self.cycle,
                member_no=number,
                last_name=name,
                first_name="太郎",
                email=f"{number}@example.com",
                employee_type="正会員",
                representative_category=(
                    MemberSnapshot.RepresentativeCategory.GENERAL
                ),
                is_eligible_voter=True,
            )
            candidates.append(Candidate.objects.create(
                election=self.election,
                member=member,
                status=Candidate.Status.QUALIFIED,
            ))
        low_vote, high_vote = candidates
        for candidate in (high_vote, high_vote, low_vote):
            ballot = Ballot.objects.create(election=self.election)
            BallotChoice.objects.create(
                ballot=ballot,
                candidate=candidate,
            )
        self.low_vote = low_vote
        self.high_vote = high_vote

    def test_candidates_are_ordered_by_vote_count_descending(self):
        preview = preview_election_count(self.election)

        self.assertEqual(
            [candidate.pk for candidate in preview["candidates"]],
            [self.high_vote.pk, self.low_vote.pk],
        )

    def test_counted_election_has_read_only_preview(self):
        change_response = self.client.get(reverse(
            "admin:election_election_change",
            args=[self.election.pk],
        ))
        self.assertContains(change_response, "開票プレビュー")

        preview_response = self.client.get(reverse(
            "admin:election_election_count_preview",
            args=[self.election.pk],
        ))
        self.assertContains(preview_response, "投票総数")
        self.assertNotContains(preview_response, "この内容で開票を確定")

    def test_tied_president_election_can_be_decided_by_lottery(self):
        ballot = Ballot.objects.create(election=self.election)
        BallotChoice.objects.create(
            ballot=ballot,
            candidate=self.low_vote,
        )
        self.election.status = Election.Status.CLOSED
        self.election.save(update_fields=["status"])

        response = self.client.post(reverse(
            "admin:election_election_count_confirm",
            args=[self.election.pk],
        ))

        self.assertEqual(response.status_code, 302)
        self.election.refresh_from_db()
        self.assertEqual(self.election.status, Election.Status.COUNTED)
        lottery = LotteryDraw.objects.get(
            election=self.election,
            category=LotteryDraw.Category.PRESIDENT,
        )
        self.assertEqual(lottery.seats_remaining, 1)
        self.assertEqual(lottery.candidates.count(), 2)
        self.assertEqual(
            Candidate.objects.filter(
                election=self.election,
                status=Candidate.Status.LOTTERY,
            ).count(),
            2,
        )

        preview_response = self.client.get(reverse(
            "admin:election_lotterydraw_preview",
            args=[lottery.pk],
        ))
        self.assertEqual(preview_response.status_code, 200)

        execute_response = self.client.post(reverse(
            "admin:election_lotterydraw_execute",
            args=[lottery.pk],
        ))
        self.assertEqual(execute_response.status_code, 302)
        self.assertEqual(
            Candidate.objects.filter(
                election=self.election,
                status=Candidate.Status.ELECTED,
            ).count(),
            1,
        )
        self.assertEqual(
            Candidate.objects.filter(
                election=self.election,
                status=Candidate.Status.NOT_ELECTED,
            ).count(),
            1,
        )
        result_response = self.client.get(reverse(
            "admin:election_election_count_preview",
            args=[self.election.pk],
        ))
        self.assertContains(result_response, "抽選は実行済みです")

    def test_candidate_route_labels_appear_only_when_accepted_exists(self):
        self.high_vote.status = Candidate.Status.ACCEPTED
        self.high_vote.save(update_fields=["status"])
        candidates = list(
            Candidate.objects.filter(election=self.election)
            .select_related("member")
            .order_by("member__member_no")
        )
        show_labels = should_show_candidate_route_labels(self.election)

        self.assertTrue(show_labels)
        for template_name in (
            "election/ballot.html",
            "election/ballot_confirm.html",
        ):
            with self.subTest(template_name=template_name):
                rendered = render_to_string(template_name, {
                    "election": self.election,
                    "candidates": candidates,
                    "vote_limit": self.election.vote_limit,
                    "selected_candidate_ids": [],
                    "show_candidate_route_labels": show_labels,
                })
                self.assertIn("山田 太郎(推)", rendered)
                self.assertIn("佐藤 太郎(立)", rendered)
                if template_name == "election/ballot.html":
                    self.assertIn("(立)：立候補", rendered)
                    self.assertIn("(推)：予備選挙による推薦", rendered)

        self.high_vote.status = Candidate.Status.QUALIFIED
        self.high_vote.save(update_fields=["status"])
        self.assertFalse(
            should_show_candidate_route_labels(self.election)
        )
        rendered = render_to_string("election/ballot.html", {
            "election": self.election,
            "candidates": candidates,
            "vote_limit": self.election.vote_limit,
            "selected_candidate_ids": [],
            "show_candidate_route_labels": False,
        })
        self.assertNotIn("(推)", rendered)
        self.assertNotIn("(立)", rendered)
        self.assertNotIn("候補者区分の説明", rendered)


class RepresentativeElectionRulesTest(TestCase):

    def setUp(self):
        self.cycle = ElectionCycle.objects.create(
            year=2030,
            name="2030年度選挙",
        )
        self.general_member = self.create_member(
            "g001",
            MemberSnapshot.RepresentativeCategory.GENERAL,
        )
        self.corporate_member = self.create_member(
            "c001",
            MemberSnapshot.RepresentativeCategory.CORPORATE,
        )
        self.now = timezone.now()

    def create_member(self, number, category):
        return MemberSnapshot.objects.create(
            cycle=self.cycle,
            member_no=number,
            last_name="会員",
            first_name=number,
            email=f"{number}@example.com",
            employee_type="正会員",
            representative_category=category,
            is_eligible_voter=True,
        )

    def create_election(self, phase, category, status=Election.Status.DRAFT):
        return Election.objects.create(
            cycle=self.cycle,
            office=Election.Office.REPRESENTATIVE,
            representative_category=category,
            phase=phase,
            status=status,
            start_at=self.now,
            end_at=self.now + timedelta(days=1),
        )

    def test_general_and_corporate_elections_can_exist_separately(self):
        general = self.create_election(
            Election.Phase.PRELIMINARY,
            Election.RepresentativeCategory.GENERAL,
        )
        corporate = self.create_election(
            Election.Phase.PRELIMINARY,
            Election.RepresentativeCategory.CORPORATE,
        )

        self.assertEqual(general.candidates.count(), 1)
        self.assertEqual(general.candidates.get().member, self.general_member)
        self.assertEqual(corporate.candidates.count(), 1)
        self.assertEqual(
            corporate.candidates.get().member,
            self.corporate_member,
        )
        self.assertEqual(general.voter_participations.count(), 2)
        self.assertEqual(corporate.voter_participations.count(), 2)

    def test_vote_limits_follow_category_and_phase(self):
        cases = [
            (Election.Phase.PRELIMINARY, Election.RepresentativeCategory.GENERAL, 10),
            (Election.Phase.PRELIMINARY, Election.RepresentativeCategory.CORPORATE, 2),
            (Election.Phase.FINAL, Election.RepresentativeCategory.GENERAL, 25),
            (Election.Phase.FINAL, Election.RepresentativeCategory.CORPORATE, 5),
        ]
        for phase, category, limit in cases:
            with self.subTest(phase=phase, category=category):
                election = self.create_election(phase, category)
                self.assertEqual(election.vote_limit, limit)
                self.assertIsNone(validate_vote(election, list(range(limit))))
                self.assertIn(
                    f"最大{limit}名",
                    validate_vote(election, list(range(limit + 1))),
                )

    def test_representative_election_requires_category(self):
        election = Election(
            cycle=self.cycle,
            office=Election.Office.REPRESENTATIVE,
            phase=Election.Phase.PRELIMINARY,
            start_at=self.now,
            end_at=self.now + timedelta(days=1),
        )

        with self.assertRaises(ValidationError):
            election.full_clean()

    def test_final_seat_count_depends_on_category(self):
        for category, seats, member in (
            (Election.RepresentativeCategory.GENERAL, 25, self.general_member),
            (Election.RepresentativeCategory.CORPORATE, 5, self.corporate_member),
        ):
            with self.subTest(category=category):
                election = self.create_election(
                    Election.Phase.FINAL,
                    category,
                    status=Election.Status.CLOSED,
                )
                Candidate.objects.create(
                    election=election,
                    member=member,
                    status=Candidate.Status.ACCEPTED,
                )
                preview = preview_election_count(election)
                self.assertEqual(preview["result"]["seats"], seats)

    def test_three_preliminary_votes_qualify_for_same_category_final(self):
        preliminary = self.create_election(
            Election.Phase.PRELIMINARY,
            Election.RepresentativeCategory.GENERAL,
            status=Election.Status.CLOSED,
        )
        final = self.create_election(
            Election.Phase.FINAL,
            Election.RepresentativeCategory.GENERAL,
        )
        candidate = preliminary.candidates.get()
        for _ in range(3):
            ballot = Ballot.objects.create(election=preliminary)
            BallotChoice.objects.create(ballot=ballot, candidate=candidate)

        preview = preview_election_count(preliminary)

        self.assertEqual(preview["threshold"], 3)
        self.assertEqual(preview["qualified"], [candidate])
        self.assertEqual(preview["final"], final)

    def test_voter_screens_display_representative_category(self):
        election = self.create_election(
            Election.Phase.PRELIMINARY,
            Election.RepresentativeCategory.CORPORATE,
        )

        self.assertEqual(
            election.election_type_display,
            "代議員（企業枠）",
        )
        for template_name in (
            "election/already_voted.html",
            "election/election_closed.html",
            "election/vote_completed.html",
        ):
            with self.subTest(template_name=template_name):
                rendered = render_to_string(template_name, {
                    "election": election,
                    "submitted_at": self.now,
                })
                self.assertIn("代議員（企業枠）・予備選挙", rendered)

    def test_command_election_selection_uses_representative_category(self):
        corporate = self.create_election(
            Election.Phase.PRELIMINARY,
            Election.RepresentativeCategory.CORPORATE,
        )
        options = {
            "office": Election.Office.REPRESENTATIVE,
            "phase": Election.Phase.PRELIMINARY,
            "category": Election.RepresentativeCategory.CORPORATE,
        }

        self.assertEqual(
            get_selected_election(self.cycle, options),
            corporate,
        )
        options["category"] = None
        with self.assertRaises(CommandError):
            get_selected_election(self.cycle, options)


class ElectionAdminFormTest(TestCase):

    def setUp(self):
        self.cycle = ElectionCycle.objects.create(
            year=2031,
            name="2031年度選挙",
        )
        self.now = timezone.now()

    def make_form(self, election_type):
        return ElectionAdminForm(data={
            "cycle": self.cycle.pk,
            "election_type": election_type,
            "phase": Election.Phase.PRELIMINARY,
            "status": Election.Status.DRAFT,
            "start_at": self.now.strftime("%Y-%m-%d %H:%M:%S"),
            "end_at": (
                self.now + timedelta(days=1)
            ).strftime("%Y-%m-%d %H:%M:%S"),
        })

    def test_three_election_types_are_mapped_to_internal_fields(self):
        cases = [
            (
                ElectionAdminForm.ElectionType.PRESIDENT,
                Election.Office.PRESIDENT,
                "",
            ),
            (
                ElectionAdminForm.ElectionType.REPRESENTATIVE_GENERAL,
                Election.Office.REPRESENTATIVE,
                Election.RepresentativeCategory.GENERAL,
            ),
            (
                ElectionAdminForm.ElectionType.REPRESENTATIVE_CORPORATE,
                Election.Office.REPRESENTATIVE,
                Election.RepresentativeCategory.CORPORATE,
            ),
        ]
        for election_type, office, category in cases:
            with self.subTest(election_type=election_type):
                form = self.make_form(election_type)
                self.assertTrue(form.is_valid(), form.errors)
                election = form.save(commit=False)
                self.assertEqual(election.office, office)
                self.assertEqual(
                    election.representative_category,
                    category,
                )

    def test_office_field_has_three_user_facing_choices(self):
        labels = [
            label
            for value, label in ElectionAdminForm().fields[
                "election_type"
            ].choices
        ]

        self.assertEqual(
            labels,
            ["会長", "代議員（一般枠）", "代議員（企業枠）"],
        )
