from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from election.models import (
    Candidate,
    Election,
    ElectionCycle,
    MemberSnapshot,
)


class Command(BaseCommand):
    help = "会長予備選挙の推薦対象者を生成します"

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
                phase=Election.Phase.PRELIMINARY,
            )
        except Election.DoesNotExist:
            raise CommandError(
                "会長予備選挙が存在しません。"
            )

        members = (
            MemberSnapshot.objects
            .filter(
                cycle=cycle,
                is_eligible_voter=True,
            )
            .order_by("member_no")
        )

        self.stdout.write(
            f"Election  : {election}"
        )

        self.stdout.write(
            f"Candidates: {members.count()}"
        )

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    "*** DRY RUN ***"
                )
            )

            for member in members:
                self.stdout.write(
                    f"{member.member_no:10} "
                    f"{member.last_name} "
                    f"{member.first_name}"
                )

            return

        created_count = 0
        existing_count = 0

        with transaction.atomic():

            for member in members:

                obj, created = (
                    Candidate.objects.get_or_create(
                        election=election,
                        member=member,
                        defaults={
                            "status":
                                Candidate.Status.ELIGIBLE,
                        },
                    )
                )

                if created:
                    created_count += 1
                else:
                    existing_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                "Candidate generation completed."
            )
        )

        self.stdout.write(
            f"Created : {created_count}"
        )

        self.stdout.write(
            f"Existing: {existing_count}"
        )
