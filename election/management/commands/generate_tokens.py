import csv
import hashlib
import secrets
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from election.management.command_utils import (
    add_category_argument,
    get_selected_election,
)

from election.models import (
    ElectionCycle,
    VoterParticipation,
)


def hash_token(token):
    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


class Command(BaseCommand):
    help = "有権者ごとの固有投票URLトークンを生成します"

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

        parser.add_argument(
            "--base-url",
            required=True,
            help=(
                "投票URLのベース。"
                "例: https://vote.pasj.jp/v/"
            ),
        )

        parser.add_argument(
            "--output",
            required=True,
            help="生成したURLを書き出すCSVファイル",
        )

        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="DBを変更せず対象件数だけ確認します",
        )

        parser.add_argument(
            "--force",
            action="store_true",
            help="未投票者の既存トークンを無効化して再発行します",
        )

    def handle(self, *args, **options):
        year = options["cycle"]
        base_url = options["base_url"]
        output = Path(options["output"])
        dry_run = options["dry_run"]
        force = options["force"]

        if not base_url.endswith("/"):
            base_url += "/"

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

        total = voters.count()

        if total == 0:
            raise CommandError(
                "有権者が0名です。"
                "先にgenerate_votersを実行してください。"
            )

        voted_count = voters.filter(
            voted_at__isnull=False
        ).count()

        already_issued = voters.filter(
            token_hash__isnull=False
        ).count()

        if force:
            target = voters.filter(
                voted_at__isnull=True
            )
        else:
            target = voters.filter(
                voted_at__isnull=True,
                token_hash__isnull=True,
            )

        target_count = target.count()

        self.stdout.write(
            f"Election       : {election}"
        )
        self.stdout.write(
            f"Total voters   : {total}"
        )
        self.stdout.write(
            f"Already voted  : {voted_count}"
        )
        self.stdout.write(
            f"Token issued   : {already_issued}"
        )
        self.stdout.write(
            f"Generate       : {target_count}"
        )

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    "*** DRY RUN ***"
                )
            )
            return

        if target_count == 0:
            self.stdout.write(
                self.style.WARNING(
                    "生成対象者はいません。"
                )
            )
            return

        if output.exists():
            raise CommandError(
                f"出力ファイルが既に存在します: {output}"
            )

        output.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        rows = []

        with transaction.atomic():

            for voter in target.select_for_update():

                #
                # 暗号学的に安全な256bit程度の乱数を生成
                #
                token = secrets.token_urlsafe(32)
                token_hash = hash_token(token)

                #
                # 理論上ほぼ起こらないが、
                # 念のため既存tokenとの衝突を確認
                #
                while (
                    VoterParticipation.objects
                    .filter(token_hash=token_hash)
                    .exists()
                ):
                    token = secrets.token_urlsafe(32)
                    token_hash = hash_token(token)

                #
                # DBにはtokenそのものではなくSHA-256だけ保存
                #
                voter.token_hash = token_hash
                voter.save(
                    update_fields=["token_hash"]
                )

                rows.append(
                    {
                        "member_no":
                            voter.member.member_no,
                        "last_name":
                            voter.member.last_name,
                        "first_name":
                            voter.member.first_name,
                        "email":
                            voter.member.email,
                        "url":
                            f"{base_url}{token}",
                    }
                )

            #
            # CSV生成もtransaction内で実行する。
            # CSV生成に失敗した場合はDB側もrollbackされる。
            #
            with output.open(
                "x",
                newline="",
                encoding="utf-8",
            ) as f:

                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "member_no",
                        "last_name",
                        "first_name",
                        "email",
                        "url",
                    ],
                )

                writer.writeheader()
                writer.writerows(rows)

            #
            # 有効な投票URLが含まれるため600
            #
            output.chmod(0o600)

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                "Token generation completed."
            )
        )

        self.stdout.write(
            f"Generated : {len(rows)}"
        )

        self.stdout.write(
            f"Output    : {output}"
        )

        self.stdout.write(
            self.style.WARNING(
                "このCSVには有効な投票URLが含まれています。"
                "メール送信後は安全に削除してください。"
            )
        )
