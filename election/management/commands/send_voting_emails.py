import hashlib
import secrets

from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F
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
        "固有投票URLを生成して有権者へ直接メール送信します。"
        "生tokenはファイルへ保存しません。"
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
            "--category",
            choices=["general", "corporate"],
        )

        parser.add_argument(
            "--base-url",
            required=True,
            help=(
                "例: https://vote.pasj.jp/v/"
            ),
        )

        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="メールを送信せず対象者だけ確認",
        )

        parser.add_argument(
            "--limit",
            type=int,
            help=(
                "送信人数を制限する。"
                "テスト送信時に使用"
            ),
        )

        parser.add_argument(
            "--member",
            help=(
                "特定の会員番号だけ送信。"
                "例: --member m151114"
            ),
        )

        parser.add_argument(
            "--replace-tokens",
            action="store_true",
            help=(
                "未送信者に既存tokenがある場合、"
                "それを無効化して新tokenを発行する"
            ),
        )

    def handle(self, *args, **options):

        year = options["cycle"]
        office = options["office"]
        phase = options["phase"]
        category = options["category"] or ""
        if office == Election.Office.REPRESENTATIVE and not category:
            raise CommandError("代議員選挙では--categoryを指定してください。")
        base_url = options["base_url"]
        dry_run = options["dry_run"]
        limit = options["limit"]
        member_no = options["member"]
        replace_tokens = options["replace_tokens"]

        if not base_url.endswith("/"):
            base_url += "/"

        #
        # HTTPS必須
        #
        if not base_url.startswith("https://"):
            raise CommandError(
                "本番投票URLにはHTTPSを使用してください。"
            )

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
                office=office,
                phase=phase,
                representative_category=category,
            )
        except Election.DoesNotExist:
            raise CommandError(
                "指定されたElectionが存在しません。"
            )

        #
        # メール送信はOPEN前でも可能とするが、
        # CLOSED/COUNTEDには送信させない
        #
        if election.status in [
            Election.Status.CLOSED,
            Election.Status.COUNTED,
        ]:
            raise CommandError(
                "終了済みの選挙には案内メールを送信できません。"
            )

        voters = (
            VoterParticipation.objects
            .filter(
                election=election,
                voted_at__isnull=True,
                email_sent_at__isnull=True,
            )
            .select_related(
                "member",
                "election",
                "election__cycle",
            )
            .order_by(
                "member__member_no"
            )
        )

        if member_no:
            voters = voters.filter(
                member__member_no=member_no
            )

        if limit:
            voters = voters[:limit]

        voters = list(voters)

        if not voters:
            self.stdout.write(
                self.style.WARNING(
                    "送信対象者はいません。"
                )
            )
            return

        #
        # CSVテストで既にtoken発行済みの場合
        #
        existing_tokens = [
            voter
            for voter in voters
            if voter.token_hash
        ]

        if (
            existing_tokens
            and not replace_tokens
        ):
            raise CommandError(
                f"{len(existing_tokens)}名に既存tokenがあります。"
                " CSVテスト等で生成済みの可能性があります。"
                " 新tokenへ置換する場合は "
                "--replace-tokens を指定してください。"
            )

        self.stdout.write(
            f"Election : {election}"
        )

        self.stdout.write(
            f"Target   : {len(voters)}"
        )

        if dry_run:

            self.stdout.write(
                self.style.WARNING(
                    "*** DRY RUN ***"
                )
            )

            for voter in voters:
                self.stdout.write(
                    f"{voter.member.member_no:10} "
                    f"{voter.member.last_name} "
                    f"{voter.member.first_name} "
                    f"<{voter.member.email}>"
                )

            return

        sent_count = 0
        failed_count = 0

        connection = get_connection(
            fail_silently=False
        )

        try:
            connection.open()

            for voter in voters:

                try:
                    self.send_one(
                        voter=voter,
                        election=election,
                        base_url=base_url,
                        connection=connection,
                    )

                    sent_count += 1

                    self.stdout.write(
                        self.style.SUCCESS(
                            f"SENT: "
                            f"{voter.member.member_no} "
                            f"{voter.member.email}"
                        )
                    )

                except Exception as exc:

                    failed_count += 1

                    #
                    # tokenはログに絶対出さない
                    #
                    self.stderr.write(
                        self.style.ERROR(
                            f"FAILED: "
                            f"{voter.member.member_no} "
                            f"{voter.member.email} "
                            f"{type(exc).__name__}: "
                            f"{exc}"
                        )
                    )

        finally:
            connection.close()

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                "メール送信処理が終了しました。"
            )
        )

        self.stdout.write(
            f"Sent   : {sent_count}"
        )

        self.stdout.write(
            f"Failed : {failed_count}"
        )

    def send_one(
        self,
        voter,
        election,
        base_url,
        connection,
    ):

        #
        # 新しいtokenをメモリ上だけで生成
        #
        token = secrets.token_urlsafe(32)

        token_hash = hash_token(token)

        voting_url = (
            f"{base_url}{token}/"
        )

        now = timezone.now()

        #
        # まずDBへtoken_hashだけ保存。
        #
        # メール送信エラー時でも生tokenは保存しない。
        #
        with transaction.atomic():

            locked_voter = (
                VoterParticipation.objects
                .select_for_update()
                .get(pk=voter.pk)
            )

            if locked_voter.voted_at:
                raise RuntimeError(
                    "すでに投票済みです。"
                )

            if locked_voter.email_sent_at:
                raise RuntimeError(
                    "すでに案内メール送信済みです。"
                )

            locked_voter.token_hash = token_hash
            locked_voter.token_issued_at = now
            locked_voter.email_send_attempts = (
                F("email_send_attempts") + 1
            )

            locked_voter.save(
                update_fields=[
                    "token_hash",
                    "token_issued_at",
                    "email_send_attempts",
                ]
            )

        subject = (
            f"【日本加速器学会】"
            f"{election.cycle.year}年度 "
            f"{election.get_office_display()}"
            f"{election.get_phase_display()}のご案内"
        )

        body = render_to_string(
            "election/email/voting_invitation.txt",
            {
                "member": voter.member,
                "election": election,
                "voting_url": voting_url,
            },
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

        #
        # SMTP送信
        #
        sent = message.send(
            fail_silently=False
        )

        if sent != 1:
            raise RuntimeError(
                "SMTP backendがメール送信成功を返しませんでした。"
            )

        #
        # 成功後に送信済みを記録
        #
        VoterParticipation.objects.filter(
            pk=voter.pk,
            email_sent_at__isnull=True,
        ).update(
            email_sent_at=timezone.now()
        )
