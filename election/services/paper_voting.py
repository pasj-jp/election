from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from ..models import Ballot, BallotChoice, Election, VoterParticipation
from ..views import get_valid_candidate_statuses, validate_vote


@transaction.atomic
def accept_paper_votes(voter_ids):
    voters = list(
        VoterParticipation.objects
        .select_for_update()
        .select_related("election")
        .filter(pk__in=voter_ids)
    )
    accepted = 0
    already_voted = 0
    counted = 0
    accepted_at = timezone.now()

    for voter in voters:
        if voter.election.status == Election.Status.COUNTED:
            counted += 1
            continue
        if voter.voted_at is not None:
            already_voted += 1
            continue
        voter.voted_at = accepted_at
        voter.voting_method = VoterParticipation.VotingMethod.PAPER
        voter.save(update_fields=["voted_at", "voting_method"])
        accepted += 1

    return {
        "accepted": accepted,
        "already_voted": already_voted,
        "counted": counted,
    }


@transaction.atomic
def create_paper_ballot(election, candidates):
    if election.status == Election.Status.COUNTED:
        raise ValidationError("開票済みの選挙には書面票を登録できません。")

    candidates = list(candidates)
    candidate_ids = [candidate.pk for candidate in candidates]
    error = validate_vote(election, candidate_ids)
    if error:
        raise ValidationError(error)

    valid_ids = set(
        election.candidates.filter(
            pk__in=candidate_ids,
            status__in=get_valid_candidate_statuses(election),
        ).values_list("pk", flat=True)
    )
    if valid_ids != set(candidate_ids):
        raise ValidationError("投票対象外の候補者が含まれています。")

    ballot = Ballot.objects.create(
        election=election,
        voting_method=Ballot.VotingMethod.PAPER,
    )
    BallotChoice.objects.bulk_create([
        BallotChoice(ballot=ballot, candidate=candidate)
        for candidate in candidates
    ])
    return ballot
