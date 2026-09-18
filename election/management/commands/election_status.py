from django.core.management.base import BaseCommand, CommandError

from election.management.command_utils import (
    add_category_argument,
    get_selected_election,
)

from election.models import (
    Ballot,
    ElectionCycle,
    VoterParticipation,
)


class Command(BaseCommand):
    help = (
        "指定した選挙の有権者数、メール送信状況、"
        "投票状況、投票率、匿名投票DBの整合性を表示します"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--cycle",
            type=int,
            required=True,
            help="選挙年度。例: --cycle 2027",
        )

        parser.add_argument(
            "--office",
            choices=[
                "president",
                "representative",
            ],
            required=True,
        )

        parser.add_argument(
            "--phase",
            choices=[
                "preliminary",
                "final",
            ],
            required=True,
        )
        add_category_argument(parser)

    def handle(self, *args, **options):
        year = options["cycle"]

        try:
            cycle = ElectionCycle.objects.get(
                year=year
            )
        except ElectionCycle.DoesNotExist:
            raise CommandError(
                f"{year}年度のElectionCycleが存在しません。"
            )

        election = get_selected_election(cycle, options)

        voters = VoterParticipation.objects.filter(
            election=election
        )

        total_voters = voters.count()

        token_issued = voters.filter(
            token_hash__isnull=False
        ).count()

        token_not_issued = voters.filter(
            token_hash__isnull=True
        ).count()

        email_sent = voters.filter(
            email_sent_at__isnull=False
        ).count()

        email_not_sent = voters.filter(
            email_sent_at__isnull=True
        ).count()

        voted = voters.filter(
            voted_at__isnull=False
        ).count()

        not_voted = voters.filter(
            voted_at__isnull=True
        ).count()

        ballot_count = Ballot.objects.filter(
            election=election
        ).count()

        if total_voters:
            turnout = (
                voted
                / total_voters
                * 100
            )
        else:
            turnout = 0.0

        #
        # 基本情報
        #
        self.stdout.write("")
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                str(election)
            )
        )

        self.stdout.write(
            "=" * 60
        )

        self.stdout.write(
            f"Status            : "
            f"{election.get_status_display()}"
        )

        self.stdout.write(
            f"Start             : "
            f"{election.start_at}"
        )

        self.stdout.write(
            f"End               : "
            f"{election.end_at}"
        )

        self.stdout.write("")

        #
        # 有権者
        #
        self.stdout.write(
            self.style.MIGRATE_LABEL(
                "【有権者】"
            )
        )

        self.stdout.write(
            f"有権者数          : {total_voters}"
        )

        self.stdout.write(
            f"Token発行済       : {token_issued}"
        )

        self.stdout.write(
            f"Token未発行       : {token_not_issued}"
        )

        self.stdout.write("")

        #
        # メール
        #
        self.stdout.write(
            self.style.MIGRATE_LABEL(
                "【メール】"
            )
        )

        self.stdout.write(
            f"送信済み          : {email_sent}"
        )

        self.stdout.write(
            f"未送信            : {email_not_sent}"
        )

        self.stdout.write("")

        #
        # 投票
        #
        self.stdout.write(
            self.style.MIGRATE_LABEL(
                "【投票】"
            )
        )

        self.stdout.write(
            f"投票済み          : {voted}"
        )

        self.stdout.write(
            f"未投票            : {not_voted}"
        )

        self.stdout.write(
            f"投票率            : {turnout:.2f}%"
        )

        self.stdout.write(
            f"Ballot数          : {ballot_count}"
        )

        self.stdout.write("")

        #
        # 整合性チェック
        #
        self.stdout.write(
            self.style.MIGRATE_LABEL(
                "【整合性チェック】"
            )
        )

        ok = True

        #
        # 投票済み人数とBallot数
        #
        if voted == ballot_count:
            self.stdout.write(
                self.style.SUCCESS(
                    "OK: 投票済み人数とBallot数が一致しています。"
                )
            )
        else:
            ok = False

            self.stdout.write(
                self.style.ERROR(
                    "ERROR: 投票済み人数とBallot数が一致しません。"
                )
            )

            self.stdout.write(
                f"  投票済み人数 : {voted}"
            )

            self.stdout.write(
                f"  Ballot数     : {ballot_count}"
            )

        #
        # メール送信済みなのにtokenがない
        #
        sent_without_token = voters.filter(
            email_sent_at__isnull=False,
            token_hash__isnull=True,
        ).count()

        if sent_without_token == 0:
            self.stdout.write(
                self.style.SUCCESS(
                    "OK: メール送信済み有権者は"
                    "全員tokenを保持しています。"
                )
            )
        else:
            ok = False

            self.stdout.write(
                self.style.ERROR(
                    "ERROR: メール送信済みなのに"
                    f"tokenがない有権者が {sent_without_token} 名います。"
                )
            )

        #
        # 投票済みなのにtokenがない
        #
        voted_without_token = voters.filter(
            voted_at__isnull=False,
            token_hash__isnull=True,
        ).count()

        if voted_without_token == 0:
            self.stdout.write(
                self.style.SUCCESS(
                    "OK: 投票済み有権者は"
                    "全員tokenを保持しています。"
                )
            )
        else:
            ok = False

            self.stdout.write(
                self.style.ERROR(
                    "ERROR: 投票済みなのに"
                    f"tokenがない有権者が {voted_without_token} 名います。"
                )
            )

        #
        # 投票済み人数が有権者数を超えていないか
        #
        if voted <= total_voters:
            self.stdout.write(
                self.style.SUCCESS(
                    "OK: 投票済み人数は有権者数以下です。"
                )
            )
        else:
            ok = False

            self.stdout.write(
                self.style.ERROR(
                    "ERROR: 投票済み人数が有権者数を超えています。"
                )
            )

        self.stdout.write("")

        if ok:
            self.stdout.write(
                self.style.SUCCESS(
                    "Overall status: OK"
                )
            )
        else:
            self.stdout.write(
                self.style.ERROR(
                    "Overall status: ERROR"
                )
            )
