import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Q

from election.models import (
    Candidate,
    Election,
    ElectionCycle,
    LotteryDraw,
    MemberSnapshot,
)


class Command(BaseCommand):
    help = (
        "代議員本選挙の最終結果を表示し、"
        "必要に応じてCSVへ出力します"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--cycle",
            type=int,
            required=True,
            help="選挙年度。例: --cycle 2027",
        )

        parser.add_argument(
            "--output",
            help="CSV出力先。省略時は画面表示のみ",
        )
        parser.add_argument(
            "--category",
            choices=["general", "corporate"],
            required=True,
        )

    def handle(self, *args, **options):
        year = options["cycle"]
        output = options["output"]

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
                office=Election.Office.REPRESENTATIVE,
                phase=Election.Phase.FINAL,
                representative_category=options["category"],
            )
        except Election.DoesNotExist:
            raise CommandError(
                f"{year}年度の代議員本選挙が存在しません。"
            )

        #
        # 未実行の抽選が残っていないか確認
        #
        pending_lotteries = LotteryDraw.objects.filter(
            election=election,
            executed_at__isnull=True,
        )

        if pending_lotteries.exists():
            self.stdout.write(
                self.style.ERROR(
                    "未実行の抽選があります。"
                )
            )

            for lottery in pending_lotteries:
                self.stdout.write(
                    f"  {lottery.get_category_display()} "
                    f"得票={lottery.vote_count} "
                    f"残議席={lottery.seats_remaining}"
                )

            raise CommandError(
                "抽選完了後に最終結果を確定してください。"
            )

        #
        # 候補者と得票数
        #
        candidates = (
            Candidate.objects
            .filter(
                election=election,
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
        )

        general = list(
            candidates.filter(
                member__representative_category=(
                    MemberSnapshot
                    .RepresentativeCategory
                    .GENERAL
                ),
            ).order_by(
                "-vote_count",
                "member__member_no",
            )
        )

        corporate = list(
            candidates.filter(
                member__representative_category=(
                    MemberSnapshot
                    .RepresentativeCategory
                    .CORPORATE
                ),
            ).order_by(
                "-vote_count",
                "member__member_no",
            )
        )

        general_elected = [
            c for c in general
            if c.status == Candidate.Status.ELECTED
        ]

        corporate_elected = [
            c for c in corporate
            if c.status == Candidate.Status.ELECTED
        ]

        #
        # 定数チェック
        #
        if len(general_elected) != 25:
            raise CommandError(
                "一般枠の当選者数が25名ではありません。"
                f" 現在: {len(general_elected)}名"
            )

        if len(corporate_elected) != 5:
            raise CommandError(
                "企業枠の当選者数が5名ではありません。"
                f" 現在: {len(corporate_elected)}名"
            )

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"{year}年度 代議員選挙 最終結果"
            )
        )

        self.stdout.write(
            "=" * 70
        )

        rows = []

        self.show_category(
            "一般枠",
            general,
            rows,
        )

        self.show_category(
            "企業枠",
            corporate,
            rows,
        )

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                "当選者数: 一般枠25名 + 企業枠5名 = 30名"
            )
        )

        #
        # 抽選情報
        #
        lotteries = (
            LotteryDraw.objects
            .filter(
                election=election,
                executed_at__isnull=False,
            )
            .order_by("category")
        )

        if lotteries.exists():

            self.stdout.write("")
            self.stdout.write(
                "【抽選実施記録】"
            )

            for lottery in lotteries:

                self.stdout.write(
                    f"{lottery.get_category_display()}: "
                    f"境界得票={lottery.vote_count}, "
                    f"残議席={lottery.seats_remaining}"
                )

                self.stdout.write(
                    f"  実行日時: {lottery.executed_at}"
                )

                self.stdout.write(
                    f"  Algorithm: {lottery.algorithm}"
                )

                self.stdout.write(
                    f"  Seed: {lottery.seed}"
                )

                self.stdout.write(
                    f"  Result hash: {lottery.result_hash}"
                )

        #
        # CSV
        #
        if output:
            self.write_csv(
                output,
                rows,
            )

    def show_category(
        self,
        category_name,
        candidates,
        rows,
    ):
        self.stdout.write("")
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"【{category_name}】"
            )
        )

        self.stdout.write(
            "結果  得票  会員番号    氏名                    所属"
        )

        self.stdout.write(
            "-" * 70
        )

        for candidate in candidates:

            member = candidate.member

            if candidate.status == Candidate.Status.ELECTED:
                result = "当選"

            elif candidate.status == Candidate.Status.NOT_ELECTED:
                result = "落選"

            elif candidate.status == Candidate.Status.LOTTERY:
                result = "抽選"

            else:
                result = candidate.get_status_display()

            self.stdout.write(
                f"{result:4} "
                f"{candidate.vote_count:4} "
                f"{member.member_no:10} "
                f"{member.last_name} "
                f"{member.first_name} "
                f"{member.affiliation}"
            )

            rows.append(
                {
                    "category": category_name,
                    "result": result,
                    "vote_count": candidate.vote_count,
                    "member_no": member.member_no,
                    "last_name": member.last_name,
                    "first_name": member.first_name,
                    "affiliation": member.affiliation,
                    "business_category":
                        member.business_category,
                }
            )

    def write_csv(
        self,
        output,
        rows,
    ):
        path = Path(output)

        if path.exists():
            raise CommandError(
                f"CSVファイルが既に存在します: {path}"
            )

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with path.open(
            "x",
            encoding="utf-8-sig",
            newline="",
        ) as f:

            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "category",
                    "result",
                    "vote_count",
                    "member_no",
                    "last_name",
                    "first_name",
                    "affiliation",
                    "business_category",
                ],
            )

            writer.writeheader()
            writer.writerows(rows)

        path.chmod(0o600)

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"CSVを出力しました: {path}"
            )
        )
