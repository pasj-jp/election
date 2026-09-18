from django.core.management.base import BaseCommand, CommandError

from election.management.command_utils import (
    add_category_argument,
    get_selected_election,
)

from election.models import ElectionCycle, VoterParticipation


class Command(BaseCommand):
    help = (
        "指定した選挙について、未送信・token未発行・"
        "複数回送信試行などのメール関連状況を一覧表示します"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--cycle",
            type=int,
            required=True,
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

        parser.add_argument(
            "--all",
            action="store_true",
            help="問題がない有権者も含めて表示",
        )

    def handle(self, *args, **options):
        year = options["cycle"]
        show_all = options["all"]

        try:
            cycle = ElectionCycle.objects.get(
                year=year
            )
        except ElectionCycle.DoesNotExist:
            raise CommandError(
                f"{year}年度のElectionCycleが存在しません。"
            )

        election = get_selected_election(cycle, options)

        voters = (
            VoterParticipation.objects
            .filter(election=election)
            .select_related("member")
            .order_by("member__member_no")
        )

        self.stdout.write("")
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                str(election)
            )
        )

        self.stdout.write(
            "=" * 100
        )

        self.stdout.write(
            "会員番号    氏名                  Email"
            "                                  Token  Mail  Attempts  Vote"
        )

        self.stdout.write(
            "-" * 100
        )

        issue_count = 0

        for voter in voters:

            token_status = (
                "あり"
                if voter.token_hash
                else "なし"
            )

            mail_status = (
                "送信済"
                if voter.email_sent_at
                else "未送信"
            )

            vote_status = (
                "投票済"
                if voter.voted_at
                else "未投票"
            )

            has_issue = (
                not voter.token_hash
                or not voter.email_sent_at
                or voter.email_send_attempts > 1
            )

            if not show_all and not has_issue:
                continue

            if has_issue:
                issue_count += 1

            member = voter.member

            self.stdout.write(
                f"{member.member_no:10} "
                f"{member.last_name} "
                f"{member.first_name:10} "
                f"{member.email:38} "
                f"{token_status:5} "
                f"{mail_status:6} "
                f"{voter.email_send_attempts:8} "
                f"{vote_status}"
            )

        self.stdout.write("")
        self.stdout.write(
            f"Issues: {issue_count}"
        )
