from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count, Q

from election.models import (
    Ballot,
    Candidate,
    Election,
    ElectionCycle,
)


class Command(BaseCommand):
    help = "会長本選挙を開票し、最多得票者を当選とします"

    def add_arguments(self, parser):
        parser.add_argument(
            "--cycle",
            type=int,
            required=True,
        )

        parser.add_argument(
            "--dry-run",
            action="store_true",
        )

    def handle(self, *args, **options):
        year = options["cycle"]
        dry_run = options["dry_run"]

        try:
            cycle = ElectionCycle.objects.get(
                year=year
            )
        except ElectionCycle.DoesNotExist:
            raise CommandError(
                "ElectionCycleが存在しません。"
            )

        try:
            election = Election.objects.get(
                cycle=cycle,
                office=Election.Office.PRESIDENT,
                phase=Election.Phase.FINAL,
            )
        except Election.DoesNotExist:
            raise CommandError(
                "会長本選挙が存在しません。"
            )

        if election.status not in [
            Election.Status.CLOSED,
            Election.Status.COUNTED,
        ]:
            raise CommandError(
                "会長本選挙をCLOSEDにしてから開票してください。"
            )

        candidates = list(
            Candidate.objects
            .filter(
                election=election,
                status__in=[
                    Candidate.Status.QUALIFIED,
                    Candidate.Status.ACCEPTED,
                    Candidate.Status.ELECTED,
                    Candidate.Status.NOT_ELECTED,
                    Candidate.Status.LOTTERY,
                ],
            )
            .select_related("member")
            .annotate(
                vote_count=Count(
                    "ballot_choices__ballot",
                    filter=Q(
                        ballot_choices__ballot__election=election
                    ),
                    distinct=True,
                )
            )
            .order_by(
                "-vote_count",
                "member__member_no",
            )
        )

        if not candidates:
            raise CommandError(
                "会長候補者が存在しません。"
            )

        self.stdout.write(
            f"Election : {election}"
        )

        self.stdout.write(
            f"Ballots  : "
            f"{Ballot.objects.filter(election=election).count()}"
        )

        self.stdout.write("")

        for candidate in candidates:
            self.stdout.write(
                f"{candidate.vote_count:4} "
                f"{candidate.member.member_no:10} "
                f"{candidate.member.last_name} "
                f"{candidate.member.first_name}"
            )

        top_vote = candidates[0].vote_count

        top_candidates = [
            c
            for c in candidates
            if c.vote_count == top_vote
        ]

        if len(top_candidates) > 1:

            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "最多得票が同票です。"
                    "会長選挙の同票処理が必要です。"
                )
            )

            for candidate in top_candidates:
                self.stdout.write(
                    f"  {candidate.member.member_no} "
                    f"{candidate.member.last_name} "
                    f"{candidate.member.first_name} "
                    f"{candidate.vote_count}票"
                )

            return

        winner = top_candidates[0]

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"当選予定: "
                f"{winner.member.last_name} "
                f"{winner.member.first_name} "
                f"({winner.vote_count}票)"
            )
        )

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    "*** DRY RUN ***"
                )
            )
            return

        with transaction.atomic():

            for candidate in candidates:

                if candidate.pk == winner.pk:
                    candidate.status = (
                        Candidate.Status.ELECTED
                    )
                else:
                    candidate.status = (
                        Candidate.Status.NOT_ELECTED
                    )

                candidate.save(
                    update_fields=["status"]
                )

            election.status = (
                Election.Status.COUNTED
            )

            election.save(
                update_fields=["status"]
            )

        self.stdout.write(
            self.style.SUCCESS(
                "会長選挙結果を確定しました。"
            )
        )
