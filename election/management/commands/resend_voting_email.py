import hashlib
import secrets

from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from election.models import (
    Election,
    ElectionCycle,
    VoterParticipation,
)


def hash_token(token):
    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


class Command(BaseCommand):
    help = (
        "特定会員の投票tokenを再発行し、"
        "案内メールを再送します"
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
            "--member",
            required=True,
            help="会員番号。例: m151114",
        )
        parser.add_argument("--category", choices=["general", "corporate"])

        parser.add_argument(
            "--base-url",
            required=True,
        )

        parser.add_argument(
            "--dry-run",
            action="store_true",
        )

    def handle(self, *args, **options):

        year = options["cycle"]
        office = options["office"]
        phase = options["phase"]
        category = options["category"] or ""
        if office == Election.Office.REPRESENTATIVE and not category:
            raise CommandError("代議員選挙では--categoryを指定してください。")
        member_no = options["member"]
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
                "ElectionCycleが存在しません。"
            )

        try:
            election = Election.objects.get(
                cycle=cycle,
                office=office,
                phase=phase,
                representative_category=category,
            )
        except Election.DoesNotExist:
            raise CommandError(
                "Electionが存在しません。"
            )

        if election.status in [
            Election.Status.CLOSED,
            Election.Status.COUNTED,
        ]:
            raise CommandError(
                "終了済みの選挙です。"
            )

        try:
            voter = (
                VoterParticipation.objects
                .select_related(
                    "member",
                    "election",
                    "election__cycle",
                )
                .get(
                    election=election,
                    member__member_no=member_no,
                )
            )
        except VoterParticipation.DoesNotExist:
            raise CommandError(
                f"{member_no} はこの選挙の有権者ではありません。"
            )

        if voter.voted_at is not None:
            raise CommandError(
                "この会員はすでに投票済みです。"
                "投票URLを再発行することはできません。"
            )

        self.stdout.write(
            f"Election : {election}"
        )

        self.stdout.write(
            f"Member   : "
            f"{voter.member.member_no} "
            f"{voter.member.last_name} "
            f"{voter.member.first_name}"
        )

        self.stdout.write(
            f"Email    : {voter.member.email}"
        )

        self.stdout.write(
            f"Attempts : {voter.email_send_attempts}"
        )

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    "*** DRY RUN: token再発行・送信は行いません ***"
                )
            )
            return

        token = secrets.token_urlsafe(32)
        token_hash = hash_token(token)

        voting_url = (
            f"{base_url}{token}/"
        )

        subject = (
            f"【日本加速器学会】"
            f"{election.cycle.year}年度 "
            f"{election.get_office_display()}"
            f"{election.get_phase_display()}のご案内（再送）"
        )

        body = render_to_string(
            "election/email/voting_invitation.txt",
            {
                "member": voter.member,
                "election": election,
                "voting_url": voting_url,
            },
        )

        #
        # 先に新tokenをDBへ保存。
        # この時点で旧URLは無効になる。
        #
        with transaction.atomic():

            locked = (
                VoterParticipation.objects
                .select_for_update()
                .get(pk=voter.pk)
            )

            if locked.voted_at is not None:
                raise CommandError(
                    "処理中に投票済みとなったため中止しました。"
                )

            locked.token_hash = token_hash
            locked.token_issued_at = timezone.now()
            locked.email_sent_at = None
            locked.email_send_attempts += 1

            locked.save(
                update_fields=[
                    "token_hash",
                    "token_issued_at",
                    "email_sent_at",
                    "email_send_attempts",
                ]
            )

        try:
            connection = get_connection(
                fail_silently=False
            )

            message = EmailMessage(
                subject=subject,
                body=body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[
                    voter.member.email
                ],
                connection=connection,
            )

            sent = message.send(
                fail_silently=False
            )

            if sent != 1:
                raise RuntimeError(
                    "SMTP backendが成功を返しませんでした。"
                )

        except Exception as exc:
            self.stderr.write(
                self.style.ERROR(
                    f"メール送信失敗: "
                    f"{type(exc).__name__}: {exc}"
                )
            )

            self.stderr.write(
                self.style.WARNING(
                    "新tokenは発行済みですが、"
                    "email_sent_atはNULLのままです。"
                    "再度このコマンドを実行すれば"
                    "さらに新しいtokenを発行できます。"
                )
            )

            return

        VoterParticipation.objects.filter(
            pk=voter.pk
        ).update(
            email_sent_at=timezone.now()
        )

        self.stdout.write(
            self.style.SUCCESS(
                "投票URLを再発行し、"
                "メールを送信しました。"
            )
        )

        self.stdout.write(
            self.style.WARNING(
                "旧投票URLは無効になっています。"
            )
        )
