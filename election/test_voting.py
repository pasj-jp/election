"""共通ルールの変更が電子投票の受付・確定に及ぼす影響を検証する。"""
import hashlib
from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Ballot, Candidate, Election, ElectionCycle, MemberSnapshot, VoterParticipation


class ElectronicVotingTests(TestCase):
    def setUp(self):
        cycle = ElectionCycle.objects.create(year=2045, name="2045年度選挙")
        member = MemberSnapshot.objects.create(
            cycle=cycle, member_no="v001", last_name="投票", first_name="太郎",
            email="voter@example.com", employee_type="正会員",
            representative_category=MemberSnapshot.RepresentativeCategory.GENERAL,
            is_eligible_voter=True,
        )
        now = timezone.now()
        self.election = Election.objects.create(
            cycle=cycle, office=Election.Office.PRESIDENT,
            phase=Election.Phase.FINAL, status=Election.Status.OPEN,
            start_at=now - timedelta(days=1), end_at=now + timedelta(days=1),
        )
        self.candidate = Candidate.objects.create(
            election=self.election, member=member, status=Candidate.Status.QUALIFIED,
        )
        self.voter = VoterParticipation.objects.get(election=self.election, member=member)
        token = "existing-voting-token"
        # 既存のSHA-256形式で保存されたURLが、共通化後も検証できること。
        self.voter.token_hash = hashlib.sha256(token.encode()).hexdigest()
        self.voter.save(update_fields=["token_hash"])
        self.entry_url = reverse("election:vote_entry", args=[token])
        self.confirm_url = reverse("election:ballot_confirm")
        self.submit_url = reverse("election:ballot_submit")

    def enter_and_confirm(self, client):
        self.assertRedirects(client.get(self.entry_url), reverse("election:ballot"), status_code=303)
        response = client.post(self.confirm_url, {"candidate": [self.candidate.pk]})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "election/ballot_confirm.html")

    def test_vote_completes_and_another_session_cannot_vote_again(self):
        other_client = Client()
        self.enter_and_confirm(self.client)
        self.enter_and_confirm(other_client)
        response = self.client.post(self.submit_url)
        self.assertTemplateUsed(response, "election/vote_completed.html")
        self.assertEqual(response.status_code, 200)
        ballot = Ballot.objects.get(election=self.election)
        self.assertEqual(ballot.voting_method, Ballot.VotingMethod.ELECTRONIC)
        self.assertEqual(list(ballot.choices.values_list("candidate_id", flat=True)), [self.candidate.pk])
        self.voter.refresh_from_db()
        self.assertIsNotNone(self.voter.voted_at)
        self.assertNotIn("election_vote", self.client.session)
        response = other_client.post(self.submit_url)
        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, "election/already_voted.html")
        self.assertEqual(Ballot.objects.filter(election=self.election).count(), 1)

    def test_closed_election_is_rechecked_at_submission(self):
        self.enter_and_confirm(self.client)
        self.election.status = Election.Status.CLOSED
        self.election.save(update_fields=["status"])
        response = self.client.post(self.submit_url)
        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, "election/election_closed.html")
        self.assertFalse(Ballot.objects.exists())

    def test_candidate_eligibility_is_rechecked_at_submission(self):
        self.enter_and_confirm(self.client)
        self.candidate.status = Candidate.Status.DECLINED
        self.candidate.save(update_fields=["status"])
        response = self.client.post(self.submit_url)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Ballot.objects.exists())
        self.voter.refresh_from_db()
        self.assertIsNone(self.voter.voted_at)

    def test_confirmation_rejects_invalid_or_duplicate_selections(self):
        self.client.get(self.entry_url)
        for ids in [[], ["invalid"], [self.candidate.pk, self.candidate.pk], [999999]]:
            with self.subTest(ids=ids):
                response = self.client.post(self.confirm_url, {"candidate": ids})
                self.assertEqual(response.status_code, 400)
                self.assertNotIn("selected_candidate_ids", self.client.session)
        self.assertFalse(Ballot.objects.exists())

    def test_submission_requires_voting_session(self):
        response = self.client.post(self.submit_url)
        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, "election/invalid_session.html")
        self.assertFalse(Ballot.objects.exists())
