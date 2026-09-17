from django.core.management.base import BaseCommand, CommandError

from election.models import Election, ElectionCycle
from election.services.candidate_generation import (
    generate_preliminary_candidates,
)


class Command(BaseCommand):
    help = "会長予備選挙の推薦対象者を生成します"

    def add_arguments(self, parser):
        parser.add_argument("--cycle", type=int, required=True)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        year = options["cycle"]
        dry_run = options["dry_run"]
        try:
            cycle = ElectionCycle.objects.get(year=year)
        except ElectionCycle.DoesNotExist as exc:
            raise CommandError("ElectionCycleが存在しません。") from exc
        try:
            election = Election.objects.get(
                cycle=cycle,
                office=Election.Office.PRESIDENT,
                phase=Election.Phase.PRELIMINARY,
            )
        except Election.DoesNotExist as exc:
            raise CommandError("会長予備選挙が存在しません。") from exc

        result = generate_preliminary_candidates(election, save=not dry_run)
        self.stdout.write(f"Election  : {election}")
        self.stdout.write(f"Candidates: {result.total_count}")
        if dry_run:
            self.stdout.write(self.style.WARNING("*** DRY RUN ***"))
            for member in result.members:
                self.stdout.write(
                    f"{member.member_no:10} "
                    f"{member.last_name} {member.first_name}"
                )
            return

        self.stdout.write(
            self.style.SUCCESS("Candidate generation completed.")
        )
        self.stdout.write(f"Created : {result.created_count}")
        self.stdout.write(f"Existing: {result.existing_count}")
