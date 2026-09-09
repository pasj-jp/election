from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count, Q

from election.models import (
    Ballot,
    Candidate,
    Election,
    ElectionCycle,
)


NOMINATION_THRESHOLD = 10


class Command(BaseCommand):
    help = (
        "会長予備選挙を集計し、"
        "10票以上の被推薦者を本選挙候補者として登録します"
    )

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
            cycle = ElectionCycle.objects.get(year=year)
        except ElectionCycle.DoesNotExist:
            raise CommandError(
                f"{year}年度のElectionCycleが存在しません。"
            )

        try:
            preliminary = Election.objects.get(
                cycle=cycle,
                office=Election.Office.PRESIDENT,
                phase=Election.Phase.PRELIMINARY,
            )
        except Election.DoesNotExist:
            raise CommandError(
                "会長予備選挙が存在しません。"
            )

        try:
            final = Election.objects.get(
                cycle=cycle,
                office=Election.Office.PRESIDENT,
                phase=Election.Phase.FINAL,
            )
        except Election.DoesNotExist:
            raise CommandError(
                "会長本選挙が存在しません。"
            )

        if preliminary.status not in [
            Election.Status.CLOSED,
            Election.Status.COUNTED,
        ]:
            raise CommandError(
                "会長予備選挙をCLOSEDにしてから集計してください。"
            )

        if final.status != Election.Status.DRAFT:
            raise CommandError(
                "会長本選挙がDRAFTではありません。"
            )

        if Ballot.objects.filter(
            election=final
        ).exists():
            raise CommandError(
                "会長本選挙の投票データが既に存在します。"
            )

        candidates = (
            Candidate.objects
            .filter(
                election=preliminary,
                status=Candidate.Status.ELIGIBLE,
            )
            .select_related("member")
            .annotate(
                nomination_count=Count(
                    "ballot_choices__ballot",
                    filter=Q(
                        ballot_choices__ballot__election=preliminary
                    ),
                    distinct=True,
                )
            )
            .order_by(
                "-nomination_count",
                "member__member_no",
            )
        )

        qualified = [
            c
            for c in candidates
            if c.nomination_count >= NOMINATION_THRESHOLD
        ]

        self.stdout.write(
            f"Election  : {preliminary}"
        )

        self.stdout.write(
            f"Ballots   : "
            f"{Ballot.objects.filter(election=preliminary).count()}"
        )

        self.stdout.write(
            f"Threshold : {NOMINATION_THRESHOLD}"
        )

        self.stdout.write(
            f"Qualified : {len(qualified)}"
        )

        self.stdout.write("")

        for candidate in candidates:
            marker = (
                "*"
                if candidate.nomination_count
                >= NOMINATION_THRESHOLD
                else " "
            )

            member = candidate.member

            self.stdout.write(
                f"{marker} "
                f"{candidate.nomination_count:4} "
                f"{member.member_no:10} "
                f"{member.last_name} "
                f"{member.first_name}"
            )

        if dry_run:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "*** DRY RUN ***"
                )
            )
            return

        created_count = 0
        existing_count = 0

        with transaction.atomic():

            for candidate in qualified:

                obj, created = (
                    Candidate.objects.get_or_create(
                        election=final,
                        member=candidate.member,
                        defaults={
                            "status":
                                Candidate.Status.QUALIFIED,
                        },
                    )
                )

                if created:
                    created_count += 1
                else:
                    existing_count += 1

            preliminary.status = (
                Election.Status.COUNTED
            )

            preliminary.save(
                update_fields=["status"]
            )

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                "会長予備選挙の集計が完了しました。"
            )
        )

        self.stdout.write(
            f"Created : {created_count}"
        )

        self.stdout.write(
            f"Existing: {existing_count}"
        )
