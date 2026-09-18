from dataclasses import dataclass

from django.db import transaction

from ..models import Election
from .candidate_generation import generate_preliminary_candidates
from .voter_generation import generate_voters


@dataclass(frozen=True)
class CycleSetupResult:
    created_elections: int
    existing_elections: int
    created_voters: int
    created_candidates: int


def cycle_has_periods(cycle):
    return all((
        cycle.preliminary_start_at,
        cycle.preliminary_end_at,
        cycle.final_start_at,
        cycle.final_end_at,
    ))


@transaction.atomic
def setup_cycle(cycle):
    if not cycle_has_periods(cycle):
        raise ValueError("予備選挙と本選挙の期間を設定してください。")

    definitions = (
        (Election.Office.PRESIDENT, ""),
        (
            Election.Office.REPRESENTATIVE,
            Election.RepresentativeCategory.GENERAL,
        ),
        (
            Election.Office.REPRESENTATIVE,
            Election.RepresentativeCategory.CORPORATE,
        ),
    )
    created_elections = 0
    existing_elections = 0
    created_voters = 0
    created_candidates = 0

    for phase, start_at, end_at in (
        (
            Election.Phase.PRELIMINARY,
            cycle.preliminary_start_at,
            cycle.preliminary_end_at,
        ),
        (
            Election.Phase.FINAL,
            cycle.final_start_at,
            cycle.final_end_at,
        ),
    ):
        for office, category in definitions:
            voter_count_before = 0
            candidate_count_before = 0
            existing_election = Election.objects.filter(
                cycle=cycle,
                office=office,
                phase=phase,
                representative_category=category,
            ).first()
            if existing_election:
                voter_count_before = (
                    existing_election.voter_participations.count()
                )
                candidate_count_before = (
                    existing_election.candidates.count()
                )
            election, created = Election.objects.update_or_create(
                cycle=cycle,
                office=office,
                phase=phase,
                representative_category=category,
                defaults={
                    "start_at": start_at,
                    "end_at": end_at,
                },
            )
            if created:
                created_elections += 1
            else:
                existing_elections += 1

            generate_voters(election)
            created_voters += (
                election.voter_participations.count()
                - voter_count_before
            )
            if phase == Election.Phase.PRELIMINARY:
                generate_preliminary_candidates(election)
                created_candidates += (
                    election.candidates.count()
                    - candidate_count_before
                )

    return CycleSetupResult(
        created_elections=created_elections,
        existing_elections=existing_elections,
        created_voters=created_voters,
        created_candidates=created_candidates,
    )
