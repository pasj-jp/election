import hashlib
import secrets

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from election.models import (
    Candidate,
    Election,
    LotteryDraw,
)


class Command(BaseCommand):
    help = "代議員本選挙の境界同票抽選を実行します"

    def add_arguments(self, parser):

        parser.add_argument(
            "--cycle",
            type=int,
            required=True,
        )

        parser.add_argument(
            "--category",
            choices=[
                "general",
                "corporate",
            ],
            required=True,
        )

        parser.add_argument(
            "--dry-run",
            action="store_true",
        )

    def handle(self, *args, **options):

        year = options["cycle"]
        category = options["category"]
        dry_run = options["dry_run"]

        try:
            lottery = (
                LotteryDraw.objects
                .select_related("election")
                .get(
                    election__cycle__year=year,
                    election__office=(
                        Election.Office.REPRESENTATIVE
                    ),
                    election__phase=(
                        Election.Phase.FINAL
                    ),
                    category=category,
                )
            )
        except LotteryDraw.DoesNotExist:
            raise CommandError(
                "指定された抽選が存在しません。"
            )

        if lottery.executed_at:
            raise CommandError(
                "この抽選はすでに実行済みです。"
            )

        entries = list(
            lottery.candidates
            .select_related(
                "candidate",
                "candidate__member",
            )
            .order_by(
                "candidate__member__member_no"
            )
        )

        if len(entries) <= lottery.seats_remaining:
            raise CommandError(
                "抽選対象人数が残議席以下です。"
            )

        self.stdout.write(
            f"Election      : {lottery.election}"
        )
        self.stdout.write(
            f"Category      : "
            f"{lottery.get_category_display()}"
        )
        self.stdout.write(
            f"Vote count    : {lottery.vote_count}"
        )
        self.stdout.write(
            f"Seats         : {lottery.seats_remaining}"
        )
        self.stdout.write(
            f"Candidates    : {len(entries)}"
        )

        if dry_run:
            for entry in entries:
                self.stdout.write(
                    f"{entry.candidate.member.member_no} "
                    f"{entry.candidate.member.last_name} "
                    f"{entry.candidate.member.first_name}"
                )

            self.stdout.write(
                self.style.WARNING(
                    "*** DRY RUN: 抽選は実行していません ***"
                )
            )

            return

        #
        # 256bit乱数seed
        #
        seed = secrets.token_hex(32)

        scored = []

        for entry in entries:

            candidate = entry.candidate

            material = (
                f"PASJ-ELECTION-LOTTERY-v1|"
                f"{lottery.election_id}|"
                f"{lottery.category}|"
                f"{lottery.vote_count}|"
                f"{candidate.member.member_no}|"
                f"{seed}"
            )

            score = hashlib.sha256(
                material.encode("utf-8")
            ).hexdigest()

            scored.append(
                (
                    score,
                    entry,
                )
            )

        #
        # SHA256値の昇順
        #
        scored.sort(
            key=lambda item: item[0]
        )

        selected = scored[
            :lottery.seats_remaining
        ]

        selected_ids = {
            entry.candidate_id
            for score, entry in selected
        }

        #
        # 結果全体のハッシュ
        #
        result_material = "|".join(
            f"{entry.candidate.member.member_no}:{score}"
            for score, entry in scored
        )

        result_hash = hashlib.sha256(
            result_material.encode("utf-8")
        ).hexdigest()

        with transaction.atomic():

            #
            # 再実行競合を防止
            #
            lottery = (
                LotteryDraw.objects
                .select_for_update()
                .get(pk=lottery.pk)
            )

            if lottery.executed_at:
                raise CommandError(
                    "この抽選は既に実行済みです。"
                )

            for score, entry in scored:

                entry.score = score

                if (
                    entry.candidate_id
                    in selected_ids
                ):
                    entry.selected = True

                    entry.candidate.status = (
                        Candidate.Status.ELECTED
                    )

                else:
                    entry.selected = False

                    entry.candidate.status = (
                        Candidate.Status.NOT_ELECTED
                    )

                entry.save(
                    update_fields=[
                        "score",
                        "selected",
                    ]
                )

                entry.candidate.save(
                    update_fields=[
                        "status",
                    ]
                )

            lottery.seed = seed
            lottery.result_hash = result_hash
            lottery.executed_at = timezone.now()

            lottery.save(
                update_fields=[
                    "seed",
                    "result_hash",
                    "executed_at",
                ]
            )

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                "抽選を実行しました。"
            )
        )

        self.stdout.write(
            f"Seed       : {seed}"
        )

        self.stdout.write(
            f"Result hash: {result_hash}"
        )

        self.stdout.write("")
        self.stdout.write(
            "当選:"
        )

        for score, entry in selected:

            member = entry.candidate.member

            self.stdout.write(
                f"  {member.member_no} "
                f"{member.last_name} "
                f"{member.first_name}"
            )
