import csv
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
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
    CandidateStatusChange,
    Election,
    ElectionCycle,
    LotteryDraw,
    MemberSnapshot,
    VoterParticipation,
)
from .services.counting import commit_election_count, preview_election_count
from .services.cycle_setup import setup_cycle
from .services.paper_voting import accept_paper_votes, create_paper_ballot
from .views import (
    get_valid_candidate_statuses,
    should_show_candidate_route_labels,
    validate_vote,
)


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
            reverse("election:management_cycle_list"),
        )
        self.assertNotContains(response, reverse("admin:index"))


class ManagementCycleViewTest(TestCase):

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="manager", email="manager@example.com", password="password"
        )
        self.client.force_login(self.user)
        self.now = timezone.now().replace(microsecond=0)

    def test_anonymous_user_is_redirected_to_admin_login(self):
        self.client.logout()
        response = self.client.get(reverse("election:management_cycle_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response.url)

    def test_creating_cycle_builds_six_elections(self):
        response = self.client.post(reverse("election:management_cycle_create"), {
            "year": 2040,
            "name": "2040年度選挙",
            "preliminary_start_at": "2040-01-01T09:00",
            "preliminary_end_at": "2040-01-08T09:00",
            "final_start_at": "2040-02-01T09:00",
            "final_end_at": "2040-02-08T09:00",
        })
        cycle = ElectionCycle.objects.get(year=2040)
        self.assertRedirects(
            response,
            reverse("election:management_cycle_detail", args=[cycle.year]),
        )
        self.assertEqual(response.url, "/management/2040/")
        self.assertEqual(cycle.elections.count(), 6)

    def test_detail_only_shows_selected_cycle_elections(self):
        cycle = ElectionCycle.objects.create(
            year=2041, name="2041年度選挙",
            preliminary_start_at=self.now,
            preliminary_end_at=self.now + timedelta(days=7),
            final_start_at=self.now + timedelta(days=14),
            final_end_at=self.now + timedelta(days=21),
        )
        other = ElectionCycle.objects.create(
            year=2042, name="別年度の選挙",
            preliminary_start_at=self.now,
            preliminary_end_at=self.now + timedelta(days=7),
            final_start_at=self.now + timedelta(days=14),
            final_end_at=self.now + timedelta(days=21),
        )
        setup_cycle(cycle)
        setup_cycle(other)
        response = self.client.get(
            reverse("election:management_cycle_detail", args=[cycle.year])
        )
        self.assertContains(response, "2041年度選挙")
        self.assertNotContains(response, "別年度の選挙")
        self.assertEqual(len(response.context["elections"]), 6)
        self.assertEqual(
            [
                (election.phase, election.office, election.representative_category)
                for election in response.context["elections"]
            ],
            [
                ("preliminary", "president", ""),
                ("preliminary", "representative", "general"),
                ("preliminary", "representative", "corporate"),
                ("final", "president", ""),
                ("final", "representative", "general"),
                ("final", "representative", "corporate"),
            ],
        )

    def test_manager_can_only_see_assigned_cycle(self):
        manager = get_user_model().objects.create_user(
            username="limited-manager", password="password", is_staff=True
        )
        assigned_group = Group.objects.create(name="2044年度選挙管理委員")
        manager.groups.add(assigned_group)
        assigned = ElectionCycle.objects.create(
            year=2044, name="担当年度",
            preliminary_start_at=self.now,
            preliminary_end_at=self.now + timedelta(days=7),
            final_start_at=self.now + timedelta(days=14),
            final_end_at=self.now + timedelta(days=21),
        )
        assigned.manager_groups.add(assigned_group)
        hidden = ElectionCycle.objects.create(
            year=2045, name="担当外年度",
            preliminary_start_at=self.now,
            preliminary_end_at=self.now + timedelta(days=7),
            final_start_at=self.now + timedelta(days=14),
            final_end_at=self.now + timedelta(days=21),
        )
        setup_cycle(assigned)
        setup_cycle(hidden)
        self.client.force_login(manager)

        response = self.client.get(reverse("election:management_cycle_list"))
        self.assertContains(response, "担当年度")
        self.assertNotContains(response, "担当外年度")
        self.assertNotContains(response, "新しい年度を作成")
        detail = self.client.get(reverse(
            "election:management_cycle_detail", args=[assigned.year]
        ))
        assigned_election = assigned.elections.first()
        email_member = MemberSnapshot.objects.create(
            cycle=assigned,
            member_no="email001",
            last_name="送信",
            first_name="対象",
            email="email-target@example.com",
            employee_type="正会員",
            is_eligible_voter=True,
        )
        email_voter = VoterParticipation.objects.create(
            election=assigned_election, member=email_member
        )
        email_url = reverse(
            "election:management_email_preview",
            args=[assigned.year, assigned_election.pk],
        )
        count_url = reverse(
            "election:management_count_preview",
            args=[assigned.year, assigned_election.pk],
        )
        paper_url = reverse(
            "election:management_paper_ballot",
            args=[assigned.year, assigned_election.pk],
        )
        self.assertNotContains(detail, email_url)
        self.assertNotContains(detail, count_url)
        self.assertNotContains(detail, paper_url)
        self.assertNotContains(detail, "会員リスト取込")
        self.assertContains(detail, reverse(
            "election:management_voters", args=[assigned.year, assigned_election.pk]
        ))
        assigned_election.status = Election.Status.OPEN
        assigned_election.start_at = self.now - timedelta(days=1)
        assigned_election.end_at = self.now + timedelta(days=1)
        assigned_election.save(update_fields=["status", "start_at", "end_at"])
        open_detail = self.client.get(reverse(
            "election:management_cycle_detail", args=[assigned.year]
        ))
        self.assertContains(open_detail, email_url)
        self.assertContains(open_detail, paper_url)
        self.assertNotContains(open_detail, count_url)
        self.assertEqual(self.client.get(paper_url).status_code, 200)
        email_preview = self.client.get(email_url)
        self.assertEqual(email_preview.status_code, 200)
        self.assertContains(email_preview, "にメールを送信します。よろしいですか？")
        self.assertContains(email_preview, "data-open-email-dialog")
        self.assertContains(email_preview, "data-close-email-dialog")
        email_voter.email_sent_at = self.now
        email_voter.email_send_attempts = 1
        email_voter.save(
            update_fields=["email_sent_at", "email_send_attempts"]
        )
        sent_preview = self.client.get(email_url)
        self.assertContains(sent_preview, "送信済み")
        self.assertContains(sent_preview, "sent-button")
        self.assertNotContains(sent_preview, "data-open-email-dialog")
        blocked_send = self.client.post(
            reverse(
                "election:management_email_send",
                args=[assigned.year, assigned_election.pk],
            ),
            follow=True,
        )
        self.assertContains(
            blocked_send, "一括送信は再実行できません。"
        )
        assigned_election.status = Election.Status.CLOSED
        assigned_election.save(update_fields=["status"])
        closed_detail = self.client.get(reverse(
            "election:management_cycle_detail", args=[assigned.year]
        ))
        self.assertContains(closed_detail, count_url)
        self.assertNotContains(closed_detail, email_url)
        self.assertNotContains(closed_detail, paper_url)
        assigned_election.status = Election.Status.COUNTED
        assigned_election.save(update_fields=["status"])
        counted_detail = self.client.get(reverse(
            "election:management_cycle_detail", args=[assigned.year]
        ))
        self.assertContains(counted_detail, "選挙結果")
        self.assertContains(counted_detail, count_url)
        self.assertEqual(
            self.client.get(reverse(
                "admin:election_election_change", args=[assigned_election.pk]
            )).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(reverse(
                "admin:election_electioncycle_import_members", args=[assigned.pk]
            )).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(reverse(
                "election:management_cycle_detail", args=[hidden.year]
            )).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse("election:management_cycle_create")).status_code,
            403,
        )
        hidden_election = hidden.elections.first()
        response = self.client.post(
            reverse("election:management_election_status", args=[
                hidden.year, hidden_election.pk,
            ]),
            {"status": Election.Status.OPEN},
        )
        self.assertEqual(response.status_code, 403)
        hidden_election.refresh_from_db()
        self.assertEqual(hidden_election.status, Election.Status.DRAFT)

    def test_superuser_can_see_unassigned_cycles(self):
        cycle = ElectionCycle.objects.create(year=2046, name="未割当年度")
        response = self.client.get(reverse("election:management_cycle_list"))
        self.assertContains(response, cycle.name)
        self.assertContains(response, "新しい年度を作成")
        detail = self.client.get(reverse(
            "election:management_cycle_detail", args=[cycle.year]
        ))
        self.assertContains(detail, "data-open-cycle-dialog")
        self.assertContains(detail, reverse(
            "election:management_cycle_edit", args=[cycle.year]
        ))
        self.assertNotContains(detail, "会員リスト取込")

    def test_voter_management_marks_scoped_voter_as_paper(self):
        manager = get_user_model().objects.create_user(
            username="paper-manager", password="password", is_staff=True
        )
        group = Group.objects.create(name="2048年度選挙管理委員")
        manager.groups.add(group)
        cycle = ElectionCycle.objects.create(
            year=2048, name="2048年度選挙",
            preliminary_start_at=self.now,
            preliminary_end_at=self.now + timedelta(days=7),
            final_start_at=self.now + timedelta(days=14),
            final_end_at=self.now + timedelta(days=21),
        )
        cycle.manager_groups.add(group)
        member = MemberSnapshot.objects.create(
            cycle=cycle, member_no="v001", last_name="書面", first_name="希望",
            email="paper-choice@example.com", employee_type="正会員",
            representative_category=MemberSnapshot.RepresentativeCategory.GENERAL,
            is_eligible_voter=True,
        )
        setup_cycle(cycle)
        election = cycle.elections.filter(
            phase=Election.Phase.PRELIMINARY,
            office=Election.Office.PRESIDENT,
        ).get()
        election.status = Election.Status.OPEN
        election.save(update_fields=["status"])
        voter = VoterParticipation.objects.get(election=election, member=member)
        self.client.force_login(manager)
        response = self.client.post(
            reverse("election:management_voters", args=[cycle.year, election.pk]),
            {"voters": [voter.pk]},
        )
        voter.refresh_from_db()
        self.assertIsNotNone(voter.voted_at)
        self.assertEqual(voter.voting_method, VoterParticipation.VotingMethod.PAPER)
        self.assertRedirects(
            response,
            reverse(
                "election:management_voters", args=[cycle.year, election.pk]
            ),
        )

        other_election = cycle.elections.exclude(pk=election.pk).first()
        other_voter = VoterParticipation.objects.get(
            election=other_election, member=member
        )
        response = self.client.post(
            reverse("election:management_voters", args=[cycle.year, election.pk]),
            {"voters": [other_voter.pk]},
        )
        self.assertEqual(response.status_code, 403)

    def test_candidate_roster_rules_and_manual_add_routes(self):
        manager = get_user_model().objects.create_user(
            username="candidate-manager", password="password", is_staff=True
        )
        group = Group.objects.create(name="2047年度選挙管理委員")
        manager.groups.add(group)
        cycle = ElectionCycle.objects.create(
            year=2047, name="2047年度選挙",
            preliminary_start_at=self.now,
            preliminary_end_at=self.now + timedelta(days=7),
            final_start_at=self.now + timedelta(days=14),
            final_end_at=self.now + timedelta(days=21),
        )
        cycle.manager_groups.add(group)
        general = MemberSnapshot.objects.create(
            cycle=cycle, member_no="c001", last_name="候補", first_name="太郎",
            email="candidate@example.com", employee_type="正会員",
            representative_category=MemberSnapshot.RepresentativeCategory.GENERAL,
            is_eligible_voter=True,
        )
        self_candidate = MemberSnapshot.objects.create(
            cycle=cycle, member_no="c002", last_name="立候補", first_name="花子",
            email="self@example.com", employee_type="正会員",
            representative_category=MemberSnapshot.RepresentativeCategory.GENERAL,
            is_eligible_voter=True,
        )
        delegate_recommended = MemberSnapshot.objects.create(
            cycle=cycle, member_no="c004", last_name="代議員", first_name="推薦",
            email="delegate-recommended@example.com", employee_type="正会員",
            representative_category=MemberSnapshot.RepresentativeCategory.GENERAL,
            is_eligible_voter=True,
        )
        corporate = MemberSnapshot.objects.create(
            cycle=cycle, member_no="c003", last_name="企業", first_name="次郎",
            email="corporate@example.com", employee_type="正会員",
            representative_category=MemberSnapshot.RepresentativeCategory.CORPORATE,
            is_eligible_voter=True,
        )
        setup_cycle(cycle)
        preliminary = cycle.elections.get(
            office=Election.Office.REPRESENTATIVE,
            phase=Election.Phase.PRELIMINARY,
            representative_category=Election.RepresentativeCategory.GENERAL,
        )
        preliminary_candidate = Candidate.objects.get(
            election=preliminary, member=general
        )
        self.client.force_login(manager)
        status_url = reverse("election:management_candidate_status", args=[
            cycle.year, preliminary.pk, preliminary_candidate.pk,
        ])

        self.client.post(status_url, {"status": Candidate.Status.QUALIFIED})
        preliminary_candidate.refresh_from_db()
        self.assertEqual(preliminary_candidate.status, Candidate.Status.ELIGIBLE)
        self.assertFalse(
            CandidateStatusChange.objects.filter(candidate=preliminary_candidate).exists()
        )

        final = cycle.elections.get(
            office=Election.Office.REPRESENTATIVE,
            phase=Election.Phase.FINAL,
            representative_category=Election.RepresentativeCategory.GENERAL,
        )
        add_url = lambda member: reverse(
            "election:management_candidate_add",
            args=[cycle.year, final.pk, member.pk],
        )
        page = self.client.get(
            reverse(
                "election:management_candidates", args=[cycle.year, final.pk]
            ),
            {"q": "候補"},
        )
        self.assertNotContains(page, "本選挙進出者として追加")
        self.assertContains(page, "代議員推薦として追加")
        self.assertContains(page, "立候補承諾として追加")
        self.client.post(add_url(general), {"route": "qualified"})
        self.assertFalse(
            Candidate.objects.filter(election=final, member=general).exists()
        )

        self.client.post(
            add_url(general), {"route": "delegate_recommended"}
        )
        corrected = Candidate.objects.get(election=final, member=general)
        self.assertEqual(
            corrected.status, Candidate.Status.DELEGATE_RECOMMENDED
        )
        addition = CandidateStatusChange.objects.get(
            candidate=corrected,
            previous_status=Candidate.Status.DELEGATE_RECOMMENDED,
            new_status=Candidate.Status.DELEGATE_RECOMMENDED,
        )
        self.assertEqual(addition.changed_by, manager)

        self.client.post(add_url(self_candidate), {"route": "accepted"})
        accepted = Candidate.objects.get(election=final, member=self_candidate)
        self.assertEqual(accepted.status, Candidate.Status.ACCEPTED)

        self.client.post(
            add_url(delegate_recommended), {"route": "delegate_recommended"}
        )
        recommended = Candidate.objects.get(
            election=final, member=delegate_recommended
        )
        self.assertEqual(
            recommended.status, Candidate.Status.DELEGATE_RECOMMENDED
        )

        self.client.post(add_url(general), {"route": "accepted"})
        self.assertEqual(Candidate.objects.filter(election=final, member=general).count(), 1)
        self.client.post(add_url(corporate), {"route": "accepted"})
        self.assertFalse(Candidate.objects.filter(election=final, member=corporate).exists())

        self.client.post(reverse(
            "election:management_candidate_remove",
            args=[cycle.year, final.pk, corrected.pk],
        ))
        corrected.refresh_from_db()
        self.assertEqual(corrected.status, Candidate.Status.DISQUALIFIED)
        change = CandidateStatusChange.objects.get(
            candidate=corrected,
            new_status=Candidate.Status.DISQUALIFIED,
        )
        self.assertEqual(
            change.previous_status, Candidate.Status.DELEGATE_RECOMMENDED
        )
        self.assertEqual(change.new_status, Candidate.Status.DISQUALIFIED)
        self.assertEqual(change.changed_by, manager)

    def test_status_can_be_updated_from_cycle_dashboard(self):
        cycle = ElectionCycle.objects.create(
            year=2043, name="2043年度選挙",
            preliminary_start_at=self.now,
            preliminary_end_at=self.now + timedelta(days=7),
            final_start_at=self.now + timedelta(days=14),
            final_end_at=self.now + timedelta(days=21),
        )
        setup_cycle(cycle)
        election = cycle.elections.first()
        response = self.client.post(
            reverse(
                "election:management_election_status",
                args=[cycle.year, election.pk],
            ),
            {"status": Election.Status.OPEN},
        )
        election.refresh_from_db()
        self.assertEqual(election.status, Election.Status.OPEN)
        self.assertRedirects(
            response,
            reverse("election:management_cycle_detail", args=[cycle.year]),
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

    def test_manifesto_is_editable(self):
        field = Candidate._meta.get_field("manifesto")

        self.assertTrue(field.editable)
        self.assertEqual(field.verbose_name, "抱負")

    def test_manifesto_is_applicable_only_to_president_final(self):
        cases = [
            (Election.Office.PRESIDENT, Election.Phase.FINAL, True),
            (Election.Office.PRESIDENT, Election.Phase.PRELIMINARY, False),
            (Election.Office.REPRESENTATIVE, Election.Phase.FINAL, False),
        ]
        for office, phase, expected in cases:
            with self.subTest(office=office, phase=phase):
                candidate = Candidate(
                    election=Election(office=office, phase=phase),
                )
                self.assertEqual(
                    self.model_admin.is_manifesto_applicable(candidate),
                    expected,
                )

        self.assertFalse(
            self.model_admin.is_manifesto_applicable(None)
        )

    def test_president_candidate_cannot_be_nomination_accepted(self):
        candidate = Candidate(
            election=Election(
                pk=1,
                office=Election.Office.PRESIDENT,
                phase=Election.Phase.FINAL,
            ),
            status=Candidate.Status.ACCEPTED,
        )

        with self.assertRaisesMessage(
            ValidationError,
            "立候補承諾を選択できるのは代議員選挙",
        ):
            candidate.clean()

    def test_accepted_status_is_valid_only_for_representative_final(self):
        president = Election(
            office=Election.Office.PRESIDENT,
            phase=Election.Phase.FINAL,
        )
        representative = Election(
            office=Election.Office.REPRESENTATIVE,
            phase=Election.Phase.FINAL,
        )

        self.assertNotIn(
            Candidate.Status.ACCEPTED,
            get_valid_candidate_statuses(president),
        )
        self.assertIn(
            Candidate.Status.ACCEPTED,
            get_valid_candidate_statuses(representative),
        )
        self.assertIn(
            Candidate.Status.DELEGATE_RECOMMENDED,
            get_valid_candidate_statuses(president),
        )
        self.assertIn(
            Candidate.Status.DELEGATE_RECOMMENDED,
            get_valid_candidate_statuses(representative),
        )


class PaperVotingTest(TestCase):

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="paper-admin",
            email="paper@example.com",
            password="password",
        )
        self.client.force_login(self.user)
        self.cycle = ElectionCycle.objects.create(
            year=2035,
            name="2035年度選挙",
        )
        self.member = MemberSnapshot.objects.create(
            cycle=self.cycle,
            member_no="p001",
            last_name="書面",
            first_name="太郎",
            email="paper-voter@example.com",
            employee_type="正会員",
            representative_category=(
                MemberSnapshot.RepresentativeCategory.GENERAL
            ),
            is_eligible_voter=True,
        )
        now = timezone.now()
        self.election = Election.objects.create(
            cycle=self.cycle,
            office=Election.Office.PRESIDENT,
            phase=Election.Phase.FINAL,
            status=Election.Status.CLOSED,
            start_at=now - timedelta(days=2),
            end_at=now - timedelta(days=1),
        )
        self.candidate = Candidate.objects.create(
            election=self.election,
            member=self.member,
            status=Candidate.Status.QUALIFIED,
        )
        self.voter = VoterParticipation.objects.get(
            election=self.election,
            member=self.member,
        )

    def test_paper_reception_blocks_electronic_vote(self):
        result = accept_paper_votes([self.voter.pk])

        self.voter.refresh_from_db()
        self.assertEqual(result["accepted"], 1)
        self.assertIsNotNone(self.voter.voted_at)
        self.assertEqual(
            self.voter.voting_method,
            VoterParticipation.VotingMethod.PAPER,
        )

        second_result = accept_paper_votes([self.voter.pk])
        self.assertEqual(second_result["accepted"], 0)
        self.assertEqual(second_result["already_voted"], 1)

    def test_admin_can_accept_selected_voter_as_paper_vote(self):
        response = self.client.post(
            reverse("admin:election_voterparticipation_changelist"),
            {
                "action": "accept_as_paper_vote",
                "_selected_action": [self.voter.pk],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "1名を書面投票受付済みにしました。",
        )
        self.voter.refresh_from_db()
        self.assertEqual(
            self.voter.voting_method,
            VoterParticipation.VotingMethod.PAPER,
        )

    @patch("election.admin.call_command")
    def test_admin_can_resend_email_to_selected_voter(self, call_command_mock):
        response = self.client.post(
            reverse("admin:election_voterparticipation_changelist"),
            {
                "action": "resend_voting_emails",
                "_selected_action": [self.voter.pk],
            },
            follow=True,
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "投票メールを1名に再送しました。")
        call_command_mock.assert_called_once()
        self.assertEqual(
            call_command_mock.call_args.args[0], "resend_voting_email"
        )
        self.assertEqual(
            call_command_mock.call_args.kwargs["member"],
            self.member.member_no,
        )

    def test_paper_ballot_is_anonymous_and_marked_as_paper(self):
        ballot = create_paper_ballot(
            self.election,
            [self.candidate],
        )

        self.assertEqual(
            ballot.voting_method,
            Ballot.VotingMethod.PAPER,
        )
        self.assertEqual(
            ballot.choices.get().candidate,
            self.candidate,
        )
        self.assertFalse(hasattr(ballot, "voter_participation"))

    def test_admin_can_enter_one_paper_ballot(self):
        url = reverse(
            "admin:election_election_paper_ballot",
            args=[self.election.pk],
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "書面票入力")

        response = self.client.post(
            url,
            {"candidates": [self.candidate.pk]},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "書面票を匿名票として1票登録しました。",
        )
        self.assertEqual(
            Ballot.objects.filter(
                election=self.election,
                voting_method=Ballot.VotingMethod.PAPER,
            ).count(),
            1,
        )

    def test_counted_election_rejects_paper_ballots(self):
        self.election.status = Election.Status.COUNTED
        self.election.save(update_fields=["status"])

        with self.assertRaises(ValidationError):
            create_paper_ballot(self.election, [self.candidate])


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


class ElectionCycleSetupTest(TestCase):

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="cycle-admin",
            email="cycle@example.com",
            password="password",
        )
        self.client.force_login(self.user)
        self.now = timezone.now().replace(microsecond=0)
        self.cycle = ElectionCycle.objects.create(
            year=2036,
            name="2036年度選挙",
            preliminary_start_at=self.now,
            preliminary_end_at=self.now + timedelta(days=7),
            final_start_at=self.now + timedelta(days=14),
            final_end_at=self.now + timedelta(days=21),
        )

    def csv_file(self):
        return SimpleUploadedFile(
            "members.csv",
            (
                CSV_HEADER
                + "m101,一般 太郎,正会員,大学,加速器大学,"
                "general@example.com\n"
                + "m102,企業 花子,正会員,企業関係,加速器株式会社,"
                "corporate@example.com\n"
            ).encode("utf-8-sig"),
            content_type="text/csv",
        )

    def test_setup_creates_six_elections_with_shared_periods(self):
        result = setup_cycle(self.cycle)

        elections = Election.objects.filter(cycle=self.cycle)
        self.assertEqual(result.created_elections, 6)
        self.assertEqual(elections.count(), 6)
        self.assertEqual(
            elections.filter(phase=Election.Phase.PRELIMINARY).count(),
            3,
        )
        self.assertEqual(
            elections.filter(phase=Election.Phase.FINAL).count(),
            3,
        )
        self.assertFalse(
            elections.exclude(status=Election.Status.DRAFT).exists()
        )
        for election in elections:
            expected = (
                (
                    self.cycle.preliminary_start_at,
                    self.cycle.preliminary_end_at,
                )
                if election.phase == Election.Phase.PRELIMINARY
                else (
                    self.cycle.final_start_at,
                    self.cycle.final_end_at,
                )
            )
            self.assertEqual(
                (election.start_at, election.end_at),
                expected,
            )

    def test_setup_updates_periods_without_duplicating_elections(self):
        setup_cycle(self.cycle)
        changed_start = self.now + timedelta(days=1)
        self.cycle.preliminary_start_at = changed_start
        self.cycle.save(update_fields=["preliminary_start_at"])

        result = setup_cycle(self.cycle)

        self.assertEqual(result.created_elections, 0)
        self.assertEqual(result.existing_elections, 6)
        self.assertEqual(self.cycle.elections.count(), 6)
        self.assertFalse(
            self.cycle.elections.filter(
                phase=Election.Phase.PRELIMINARY,
            ).exclude(start_at=changed_start).exists()
        )

    def test_cycle_import_populates_voters_and_preliminary_candidates(self):
        setup_cycle(self.cycle)
        response = self.client.post(
            reverse(
                "admin:election_electioncycle_import_members",
                args=[self.cycle.pk],
            ),
            {"csv_file": self.csv_file()},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "会員リストを取り込みました。")
        elections = Election.objects.filter(cycle=self.cycle)
        self.assertEqual(elections.count(), 6)
        for election in elections:
            self.assertEqual(election.voter_participations.count(), 2)
            if election.phase == Election.Phase.PRELIMINARY:
                expected_candidates = (
                    2
                    if election.office == Election.Office.PRESIDENT
                    else 1
                )
                self.assertEqual(
                    election.candidates.count(),
                    expected_candidates,
                )

    def test_cycle_admin_creation_automatically_creates_elections(self):
        response = self.client.post(
            reverse("admin:election_electioncycle_add"),
            {
                "year": 2037,
                "name": "2037年度選挙",
                "preliminary_start_at_0": "2037-01-01",
                "preliminary_start_at_1": "09:00:00",
                "preliminary_end_at_0": "2037-01-08",
                "preliminary_end_at_1": "09:00:00",
                "final_start_at_0": "2037-02-01",
                "final_start_at_1": "09:00:00",
                "final_end_at_0": "2037-02-08",
                "final_end_at_1": "09:00:00",
                "_save": "保存",
            },
        )

        self.assertEqual(response.status_code, 302)
        cycle = ElectionCycle.objects.get(year=2037)
        self.assertEqual(cycle.elections.count(), 6)


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

    def test_counted_final_result_can_be_downloaded_as_csv(self):
        change_response = self.client.get(reverse(
            "admin:election_election_change",
            args=[self.election.pk],
        ))
        self.assertContains(change_response, "結果CSVをダウンロード")

        response = self.client.get(reverse(
            "admin:election_election_result_csv",
            args=[self.election.pk],
        ))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn(
            'filename="president-2029.csv"',
            response["Content-Disposition"],
        )
        content = response.content.decode("utf-8-sig")
        rows = list(csv.DictReader(StringIO(content)))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["member_no"], "m002")
        self.assertEqual(rows[0]["vote_count"], "2")

    def test_result_csv_is_unavailable_before_counting(self):
        self.election.status = Election.Status.CLOSED
        self.election.save(update_fields=["status"])

        response = self.client.get(
            reverse(
                "admin:election_election_result_csv",
                args=[self.election.pk],
            ),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "CSVを出力できるのは開票済みの本選挙だけです。",
        )

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
        self.election.office = Election.Office.REPRESENTATIVE
        self.election.representative_category = (
            Election.RepresentativeCategory.GENERAL
        )
        self.election.save(
            update_fields=["office", "representative_category"]
        )
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

    def test_president_manifesto_is_displayed_on_ballot(self):
        self.high_vote.manifesto = (
            "学会の発展に尽力します。\n若手を支援します。"
        )
        self.high_vote.save(update_fields=["manifesto"])

        rendered = render_to_string("election/ballot.html", {
            "election": self.election,
            "candidates": [self.high_vote],
            "vote_limit": self.election.vote_limit,
            "selected_candidate_ids": [],
            "show_candidate_route_labels": False,
        })

        self.assertIn("学会の発展に尽力します。", rendered)
        self.assertIn("<br>", rendered)


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
        commit_election_count(preliminary)
        final_candidate = Candidate.objects.get(election=final, member=candidate.member)
        self.assertEqual(final_candidate.status, Candidate.Status.QUALIFIED)

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
