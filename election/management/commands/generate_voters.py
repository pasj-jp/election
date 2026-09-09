from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from election.models import (
    Election,
    ElectionCycle,
    MemberSnapshot,
    VoterParticipation,
)


class Command(BaseCommand):
    help = "指定された選挙の有権者一覧を生成します"

    def add_arguments(self, parser):
        parser.add_argument(
            "--cycle",
            type=int,
            required=True,
            help="選挙年度。例: --cycle 2027",
        )

        parser.add_argument(
            "--office",
            choices=["president", "representative"],
            required=True,
        )

        parser.add_argument(
            "--phase",
            choices=["preliminary", "final"],
            required=True,
        )

        parser.add_argument(
            "--dry-run",
            action="store_true",
        )

    def handle(self, *args, **options):

        year = options["cycle"]
        office = options["office"]
        phase = options["phase"]
        dry_run = options["dry_run"]

        try:
            cycle = ElectionCycle.objects.get(
                year=year
            )
        except ElectionCycle.DoesNotExist:
            raise CommandError(
                f"{year}年度のElectionCycleが存在しません。"
            )

        try:
            election = Election.objects.get(
                cycle=cycle,
                office=office,
                phase=phase,
            )
        except Election.DoesNotExist:
            raise CommandError(
                "指定したElectionが存在しません。"
            )

        members = MemberSnapshot.objects.filter(
            cycle=cycle,
            is_eligible_voter=True,
        ).order_by("member_no")

        self.stdout.write(
            f"Election : {election}"
        )

        self.stdout.write(
            f"Eligible voters: {members.count()}"
        )

        if dry_run:
            self.stdout.write(
                self.style.WARNING("*** DRY RUN ***")
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
                    VoterParticipation.objects.get_or_create(
                        election=election,
                        member=member,
                    )
                )

                if created:
                    created_count += 1
                else:
                    existing_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                "Voter generation completed."
            )
        )

        self.stdout.write(
            f"Created : {created_count}"
        )

        self.stdout.write(
            f"Existing: {existing_count}"
        )
