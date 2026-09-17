from django.core.management.base import BaseCommand, CommandError

from election.models import Election, ElectionCycle
from election.services.voter_generation import generate_voters


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
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        year = options["cycle"]
        try:
            cycle = ElectionCycle.objects.get(year=year)
        except ElectionCycle.DoesNotExist as exc:
            raise CommandError(
                f"{year}年度のElectionCycleが存在しません。"
            ) from exc
        try:
            election = Election.objects.get(
                cycle=cycle,
                office=options["office"],
                phase=options["phase"],
            )
        except Election.DoesNotExist as exc:
            raise CommandError("指定したElectionが存在しません。") from exc

        dry_run = options["dry_run"]
        result = generate_voters(election, save=not dry_run)
        self.stdout.write(f"Election : {election}")
        self.stdout.write(f"Eligible voters: {result.total_count}")
        if dry_run:
            self.stdout.write(self.style.WARNING("*** DRY RUN ***"))
            for member in result.members:
                self.stdout.write(
                    f"{member.member_no:10} "
                    f"{member.last_name} {member.first_name}"
                )
            return

        self.stdout.write(
            self.style.SUCCESS("Voter generation completed.")
        )
        self.stdout.write(f"Created : {result.created_count}")
        self.stdout.write(f"Existing: {result.existing_count}")
