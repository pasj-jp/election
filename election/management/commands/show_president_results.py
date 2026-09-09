import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Q

from election.models import (
    Ballot,
    Candidate,
    Election,
    ElectionCycle,
)


class Command(BaseCommand):
    help = (
        "会長本選挙の最終結果を表示し、"
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
                office=Election.Office.PRESIDENT,
                phase=Election.Phase.FINAL,
            )
        except Election.DoesNotExist:
            raise CommandError(
                f"{year}年度の会長本選挙が存在しません。"
            )

        #
        # 開票済みであることを要求
        #
        if election.status != Election.Status.COUNTED:
            raise CommandError(
                "会長本選挙がまだ開票済みではありません。"
            )

        candidates = list(
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
            .order_by(
                "-vote_count",
                "member__member_no",
            )
        )

        if not candidates:
            raise CommandError(
                "会長候補者が存在しません。"
            )

        winners = [
            candidate
            for candidate in candidates
            if candidate.status
            == Candidate.Status.ELECTED
        ]

        #
        # 会長は必ず1名
        #
        if len(winners) != 1:
            raise CommandError(
                "会長当選者が1名ではありません。"
                f" 現在: {len(winners)}名"
            )

        total_ballots = (
            Ballot.objects
            .filter(election=election)
            .count()
        )

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"{year}年度 会長選挙 最終結果"
            )
        )

        self.stdout.write(
            "=" * 70
        )

        self.stdout.write(
            f"投票総数: {total_ballots}"
        )

        self.stdout.write("")

        self.stdout.write(
            "結果  得票  会員番号    氏名                    所属"
        )

        self.stdout.write(
            "-" * 70
        )

        rows = []

        for candidate in candidates:
            member = candidate.member

            if (
                candidate.status
                == Candidate.Status.ELECTED
            ):
                result = "当選"

            elif (
                candidate.status
                == Candidate.Status.NOT_ELECTED
            ):
                result = "落選"

            else:
                result = (
                    candidate.get_status_display()
                )

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
                    "result": result,
                    "vote_count":
                        candidate.vote_count,
                    "member_no":
                        member.member_no,
                    "last_name":
                        member.last_name,
                    "first_name":
                        member.first_name,
                    "affiliation":
                        member.affiliation,
                }
            )

        winner = winners[0]

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                "当選者: "
                f"{winner.member.last_name} "
                f"{winner.member.first_name} "
                f"（{winner.vote_count}票）"
            )
        )

        if output:
            self.write_csv(
                output,
                rows,
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
                    "result",
                    "vote_count",
                    "member_no",
                    "last_name",
                    "first_name",
                    "affiliation",
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
