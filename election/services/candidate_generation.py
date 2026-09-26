from dataclasses import dataclass

from django.db import transaction

from election.models import Candidate, Election, MemberSnapshot


@dataclass(frozen=True)
class CandidateGenerationResult:
    members: tuple[MemberSnapshot, ...]
    created_count: int
    existing_count: int

    @property
    def total_count(self):
        return len(self.members)

    @property
    def corporate_count(self):
        return sum(
            member.representative_category
            == MemberSnapshot.RepresentativeCategory.CORPORATE
            for member in self.members
        )

    @property
    def general_count(self):
        return self.total_count - self.corporate_count


def generate_preliminary_candidates(election, *, save=True):
    if election.phase != Election.Phase.PRELIMINARY:
        raise ValueError("候補者を生成できるのは予備選挙だけです。")

    member_filters = {
        "cycle": election.cycle,
        "is_eligible_voter": True,
    }
    if (
        election.office == Election.Office.REPRESENTATIVE
        and election.representative_category == Election.RepresentativeCategory.CORPORATE
    ):
        member_filters["representative_category"] = (
            election.representative_category
        )
    members = tuple(
        MemberSnapshot.objects.filter(**member_filters).order_by("member_no")
    )
    created_count = 0
    existing_count = 0

    if save:
        with transaction.atomic():
            for member in members:
                _, created = Candidate.objects.get_or_create(
                    election=election,
                    member=member,
                    defaults={"status": Candidate.Status.ELIGIBLE},
                )
                if created:
                    created_count += 1
                else:
                    existing_count += 1

    return CandidateGenerationResult(
        members=members,
        created_count=created_count,
        existing_count=existing_count,
    )
