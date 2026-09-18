from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.core.management.base import BaseCommand, CommandError
from django.template.loader import render_to_string

from election.management.command_utils import (
    add_category_argument,
    get_selected_election,
)

from election.models import (
    ElectionCycle,
)


class DummyMember:
    last_name = "テスト"
    first_name = "太郎"
    email = "test@example.jp"


class Command(BaseCommand):
    help = (
        "選挙案内メールを任意のテストアドレスへ送信します。"
        "実際の有権者tokenは使用しません。"
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

        parser.add_argument(
            "--to",
            required=True,
            help="テストメール送信先",
        )
        add_category_argument(parser)

        parser.add_argument(
            "--base-url",
            required=True,
            help="例: https://vote.pasj.jp/v/",
        )

        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="SMTP送信せず内容だけ表示",
        )

    def handle(self, *args, **options):
        year = options["cycle"]
        recipient = options["to"]
        base_url = options["base_url"]
        dry_run = options["dry_run"]

        if not base_url.endswith("/"):
            base_url += "/"

        if not base_url.startswith("https://"):
            raise CommandError(
                "投票URLにはHTTPSを使用してください。"
            )

        try:
            cycle = ElectionCycle.objects.get(
                year=year
            )
        except ElectionCycle.DoesNotExist:
            raise CommandError(
                f"{year}年度のElectionCycleが存在しません。"
            )

        election = get_selected_election(cycle, options)

        #
        # テスト用ダミーURL。
        # 実際には有効ではない。
        #
        voting_url = (
            f"{base_url}"
            "TEST-TOKEN-NOT-VALID"
            "/"
        )

        subject = (
            f"【テスト】【日本加速器学会】"
            f"{election.cycle.year}年度 "
            f"{election.get_office_display()}"
            f"{election.get_phase_display()}のご案内"
        )

        body = render_to_string(
            "election/email/voting_invitation.txt",
            {
                "member": DummyMember(),
                "election": election,
                "voting_url": voting_url,
            },
        )

        self.stdout.write("")
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                "テストメール内容"
            )
        )

        self.stdout.write("=" * 70)

        self.stdout.write(
            f"From    : {settings.DEFAULT_FROM_EMAIL}"
        )

        self.stdout.write(
            f"To      : {recipient}"
        )

        self.stdout.write(
            f"Subject : {subject}"
        )

        self.stdout.write("")

        self.stdout.write(body)

        self.stdout.write("=" * 70)

        if dry_run:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "*** DRY RUN: メールは送信していません ***"
                )
            )
            return

        connection = get_connection(
            fail_silently=False
        )

        try:
            connection.open()

            message = EmailMessage(
                subject=subject,
                body=body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[
                    recipient
                ],
                connection=connection,
            )

            sent = message.send(
                fail_silently=False
            )

            if sent != 1:
                raise RuntimeError(
                    "SMTP backendが送信成功を返しませんでした。"
                )

        except Exception as exc:
            raise CommandError(
                f"テストメール送信に失敗しました: "
                f"{type(exc).__name__}: {exc}"
            )

        finally:
            connection.close()

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"テストメールを {recipient} へ送信しました。"
            )
        )

        self.stdout.write(
            self.style.WARNING(
                "メール内のTEST-TOKEN-NOT-VALIDは"
                "意図的に無効なURLです。"
            )
        )
