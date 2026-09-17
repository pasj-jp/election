from django.core.management.base import BaseCommand, CommandError

from election.models import Election, ElectionCycle
from election.services.candidate_generation import (
    generate_preliminary_candidates,
)


class Command(BaseCommand):
    help = "代議員予備選挙の推薦対象者CandidateをMemberSnapshotから生成します"

    def add_arguments(self, parser):
        parser.add_argument(
            "--cycle",
            type=int,
            required=True,
            help="選挙年度。例: --cycle 2027",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="DBを変更せず生成予定だけ確認します",
        )

    def handle(self, *args, **options):
        year = options["cycle"]
        dry_run = options["dry_run"]
        try:
            cycle = ElectionCycle.objects.get(year=year)
        except ElectionCycle.DoesNotExist as exc:
            raise CommandError(
                f"{year}年度のElectionCycleが存在しません。"
            ) from exc
        try:
            election = Election.objects.get(
                cycle=cycle,
                office=Election.Office.REPRESENTATIVE,
                phase=Election.Phase.PRELIMINARY,
            )
        except Election.DoesNotExist as exc:
            raise CommandError(
                f"{year}年度の代議員予備選挙が存在しません。"
            ) from exc

        result = generate_preliminary_candidates(election, save=not dry_run)
        if not result.total_count:
            raise CommandError(
                "推薦対象となる正会員が0名です。"
                "先に会員名簿CSVを取り込んでください。"
            )

        self.stdout.write(f"Election   : {election}")
        self.stdout.write(f"Candidates : {result.total_count}")
        self.stdout.write(f"Corporate  : {result.corporate_count}")
        self.stdout.write(f"General    : {result.general_count}")
        if dry_run:
            self.stdout.write(self.style.WARNING("*** DRY RUN ***"))
            for member in result.members:
                self.stdout.write(
                    f"{member.member_no:10} "
                    f"{member.last_name} {member.first_name} "
                    f"[{member.get_representative_category_display()}]"
                )
            return

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS("Candidate generation completed.")
        )
        self.stdout.write(f"Created : {result.created_count}")
        self.stdout.write(f"Existing: {result.existing_count}")
