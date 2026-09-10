from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from election.models import Candidate, Election, ElectionCycle, MemberSnapshot


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
        except ElectionCycle.DoesNotExist:
            raise CommandError(
                f"{year}年度のElectionCycleが存在しません。"
            )

        try:
            election = Election.objects.get(
                cycle=cycle,
                office=Election.Office.REPRESENTATIVE,
                phase=Election.Phase.PRELIMINARY,
            )
        except Election.DoesNotExist:
            raise CommandError(
                f"{year}年度の代議員予備選挙が存在しません。"
            )

        members = (
            MemberSnapshot.objects
            .filter(
                cycle=cycle,
                is_eligible_voter=True,
            )
            .order_by("member_no")
        )

        total = members.count()

        if total == 0:
            raise CommandError(
                "推薦対象となる正会員が0名です。"
                "先に会員名簿CSVを取り込んでください。"
            )

        corporate_count = members.filter(
            representative_category=(
                MemberSnapshot.RepresentativeCategory.CORPORATE
            )
        ).count()

        general_count = members.filter(
            representative_category=(
                MemberSnapshot.RepresentativeCategory.GENERAL
            )
        ).count()

        self.stdout.write(f"Election   : {election}")
        self.stdout.write(f"Candidates : {total}")
        self.stdout.write(f"Corporate  : {corporate_count}")
        self.stdout.write(f"General    : {general_count}")

        if dry_run:
            self.stdout.write(
                self.style.WARNING("*** DRY RUN ***")
            )

            for member in members:
                category = (
                    member.get_representative_category_display()
                )

                self.stdout.write(
                    f"{member.member_no:10} "
                    f"{member.last_name} {member.first_name} "
                    f"[{category}]"
                )

            return

        created_count = 0
        existing_count = 0

        with transaction.atomic():
            for member in members:
                candidate, created = (
                    Candidate.objects.get_or_create(
                        election=election,
                        member=member,
                        defaults={
                            "status": Candidate.Status.ELIGIBLE,
                        },
                    )
                )

                if created:
                    created_count += 1
                else:
                    existing_count += 1

        self.stdout.write("")
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
