from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count, Q

from election.models import (
    Ballot,
    Candidate,
    Election,
    ElectionCycle,
)


NOMINATION_THRESHOLD = 3


class Command(BaseCommand):
    help = (
        "代議員予備選挙を集計し、"
        "3票以上の被推薦者を本選挙候補者として登録します"
    )

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
            help="DBを変更せず集計結果のみ表示します",
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

        #
        # 代議員予備選挙
        #
        try:
            preliminary = Election.objects.get(
                cycle=cycle,
                office=Election.Office.REPRESENTATIVE,
                phase=Election.Phase.PRELIMINARY,
            )
        except Election.DoesNotExist:
            raise CommandError(
                f"{year}年度の代議員予備選挙が存在しません。"
            )

        #
        # 代議員本選挙
        #
        try:
            final = Election.objects.get(
                cycle=cycle,
                office=Election.Office.REPRESENTATIVE,
                phase=Election.Phase.FINAL,
            )
        except Election.DoesNotExist:
            raise CommandError(
                f"{year}年度の代議員本選挙が存在しません。"
            )

        #
        # 投票期間中には開票させない
        #
        if preliminary.status not in [
            Election.Status.CLOSED,
            Election.Status.COUNTED,
        ]:
            raise CommandError(
                "予備選挙が終了していません。"
                "status を CLOSED にしてから集計してください。"
            )

        #
        # 本選挙が始まった後に候補者を変更しない
        #
        if final.status != Election.Status.DRAFT:
            raise CommandError(
                "本選挙が準備中(DRAFT)ではありません。"
                "本選挙開始後は候補者を生成できません。"
            )

        #
        # 本選挙ですでに投票が存在する場合も停止
        #
        if Ballot.objects.filter(
            election=final
        ).exists():
            raise CommandError(
                "本選挙の投票データが既に存在します。"
            )

        #
        # 候補者別推薦票数を集計
        #
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

        total_ballots = Ballot.objects.filter(
            election=preliminary
        ).count()

        qualified = [
            candidate
            for candidate in candidates
            if candidate.nomination_count
            >= NOMINATION_THRESHOLD
        ]

        self.stdout.write(
            f"Election        : {preliminary}"
        )
        self.stdout.write(
            f"Ballots         : {total_ballots}"
        )
        self.stdout.write(
            f"Threshold       : {NOMINATION_THRESHOLD}"
        )
        self.stdout.write(
            f"Qualified       : {len(qualified)}"
        )
        self.stdout.write("")

        self.stdout.write(
            "推薦票  枠      会員番号    氏名"
        )
        self.stdout.write(
            "---------------------------------------------"
        )

        for candidate in candidates:
            member = candidate.member

            category = (
                member.get_representative_category_display()
            )

            marker = (
                "*"
                if candidate.nomination_count
                >= NOMINATION_THRESHOLD
                else " "
            )

            self.stdout.write(
                f"{marker} "
                f"{candidate.nomination_count:4} "
                f"{category:6} "
                f"{member.member_no:10} "
                f"{member.last_name} "
                f"{member.first_name}"
            )

        self.stdout.write("")

        if dry_run:
            self.stdout.write(
                self.style.WARNING("*** DRY RUN ***")
            )
            self.stdout.write(
                "* が本選挙進出予定者です。"
            )
            return

        created_count = 0
        existing_count = 0

        with transaction.atomic():
            for candidate in qualified:

                final_candidate, created = (
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

        self.stdout.write(
            self.style.SUCCESS(
                "Representative preliminary count completed."
            )
        )

        self.stdout.write(
            f"Final candidates created : {created_count}"
        )

        self.stdout.write(
            f"Already existed          : {existing_count}"
        )

        self.stdout.write(
            f"Preliminary status       : "
            f"{preliminary.get_status_display()}"
        )
