from dataclasses import dataclass

from django.db import transaction

from election.models import MemberSnapshot, VoterParticipation


@dataclass(frozen=True)
class VoterGenerationResult:
    members: tuple[MemberSnapshot, ...]
    created_count: int
    existing_count: int

    @property
    def total_count(self):
        return len(self.members)


def generate_voters(election, *, save=True):
    members = tuple(
        MemberSnapshot.objects.filter(
            cycle=election.cycle,
            is_eligible_voter=True,
        ).order_by("member_no")
    )
    created_count = 0
    existing_count = 0

    if save:
        with transaction.atomic():
            for member in members:
                _, created = VoterParticipation.objects.get_or_create(
                    election=election,
                    member=member,
                )
                if created:
                    created_count += 1
                else:
                    existing_count += 1

    return VoterGenerationResult(
        members=members,
        created_count=created_count,
        existing_count=existing_count,
    )
