from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count, Q

from election.models import (
    Ballot,
    Candidate,
    Election,
    ElectionCycle,
    LotteryCandidate,
    LotteryDraw,
    MemberSnapshot,
)


GENERAL_SEATS = 25
CORPORATE_SEATS = 5


class Command(BaseCommand):
    help = (
        "代議員本選挙を開票し、"
        "当選・抽選対象・落選をDBへ保存します"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--cycle",
            type=int,
            required=True,
        )

        parser.add_argument(
            "--dry-run",
            action="store_true",
        )

    def handle(self, *args, **options):
        year = options["cycle"]
        dry_run = options["dry_run"]

        try:
            cycle = ElectionCycle.objects.get(
                year=year
            )
        except ElectionCycle.DoesNotExist:
            raise CommandError(
                f"{year}年度ElectionCycleがありません。"
            )

        try:
            election = Election.objects.get(
                cycle=cycle,
                office=Election.Office.REPRESENTATIVE,
                phase=Election.Phase.FINAL,
            )
        except Election.DoesNotExist:
            raise CommandError(
                "代議員本選挙が存在しません。"
            )

        if election.status not in [
            Election.Status.CLOSED,
            Election.Status.COUNTED,
        ]:
            raise CommandError(
                "本選挙をCLOSEDにしてから開票してください。"
            )

        if LotteryDraw.objects.filter(
            election=election,
            executed_at__isnull=False,
        ).exists():
            raise CommandError(
                "すでに抽選済みです。"
                "開票結果を再生成できません。"
            )

        self.stdout.write(
            f"Election: {election}"
        )

        self.stdout.write(
            f"Ballots : "
            f"{Ballot.objects.filter(election=election).count()}"
        )

        results = []

        results.append(
            self.calculate_category(
                election,
                MemberSnapshot.RepresentativeCategory.GENERAL,
                "一般枠",
                GENERAL_SEATS,
            )
        )

        results.append(
            self.calculate_category(
                election,
                MemberSnapshot.RepresentativeCategory.CORPORATE,
                "企業枠",
                CORPORATE_SEATS,
            )
        )

        if dry_run:
            self.stdout.write(
                self.style.WARNING("*** DRY RUN ***")
            )
            return

        with transaction.atomic():

            LotteryDraw.objects.filter(
                election=election,
                executed_at__isnull=True,
            ).delete()

            for result in results:

                for candidate in result["winners"]:
                    candidate.status = Candidate.Status.ELECTED
                    candidate.save(
                        update_fields=["status"]
                    )

                for candidate in result["losers"]:
                    candidate.status = Candidate.Status.NOT_ELECTED
                    candidate.save(
                        update_fields=["status"]
                    )

                for candidate in result["tied"]:
                    candidate.status = Candidate.Status.LOTTERY
                    candidate.save(
                        update_fields=["status"]
                    )

                if result["lottery_required"]:

                    lottery = LotteryDraw.objects.create(
                        election=election,
                        category=result["category"],
                        vote_count=result["boundary_vote"],
                        seats_remaining=result["remaining_seats"],
                    )

                    LotteryCandidate.objects.bulk_create(
                        [
                            LotteryCandidate(
                                lottery=lottery,
                                candidate=candidate,
                            )
                            for candidate in result["tied"]
                        ]
                    )

            election.status = Election.Status.COUNTED

            election.save(
                update_fields=["status"]
            )

        self.stdout.write(
            self.style.SUCCESS(
                "開票結果をDBへ保存しました。"
            )
        )

    def calculate_category(
        self,
        election,
        category,
        category_name,
        seats,
    ):

        candidates = list(
            Candidate.objects
            .filter(
                election=election,
                member__representative_category=category,
                status__in=[
                    Candidate.Status.QUALIFIED,
                    Candidate.Status.ACCEPTED,
                    Candidate.Status.ELECTED,
                    Candidate.Status.LOTTERY,
                    Candidate.Status.NOT_ELECTED,
                ],
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

        self.stdout.write("")
        self.stdout.write(
            f"{category_name} 定数={seats}"
        )

        if len(candidates) <= seats:

            for candidate in candidates:
                self.stdout.write(
                    f"[当選] "
                    f"{candidate.vote_count:4} "
                    f"{candidate.member.member_no} "
                    f"{candidate.member.last_name} "
                    f"{candidate.member.first_name}"
                )

            return {
                "category": category,
                "winners": candidates,
                "tied": [],
                "losers": [],
                "lottery_required": False,
                "boundary_vote": None,
                "remaining_seats": 0,
            }

        boundary_vote = (
            candidates[seats - 1].vote_count
        )

        winners = [
            c for c in candidates
            if c.vote_count > boundary_vote
        ]

        tied = [
            c for c in candidates
            if c.vote_count == boundary_vote
        ]

        losers = [
            c for c in candidates
            if c.vote_count < boundary_vote
        ]

        remaining_seats = (
            seats - len(winners)
        )

        lottery_required = (
            len(tied) > remaining_seats
        )

        if not lottery_required:
            winners.extend(tied)
            tied = []

        for candidate in winners:
            self.stdout.write(
                f"[当選] "
                f"{candidate.vote_count:4} "
                f"{candidate.member.member_no} "
                f"{candidate.member.last_name} "
                f"{candidate.member.first_name}"
            )

        for candidate in tied:
            self.stdout.write(
                f"[抽選] "
                f"{candidate.vote_count:4} "
                f"{candidate.member.member_no} "
                f"{candidate.member.last_name} "
                f"{candidate.member.first_name}"
            )

        for candidate in losers:
            self.stdout.write(
                f"[落選] "
                f"{candidate.vote_count:4} "
                f"{candidate.member.member_no} "
                f"{candidate.member.last_name} "
                f"{candidate.member.first_name}"
            )

        if lottery_required:
            self.stdout.write(
                self.style.WARNING(
                    f"抽選対象={len(tied)}名 "
                    f"残議席={remaining_seats}"
                )
            )

        return {
            "category": category,
            "winners": winners,
            "tied": tied,
            "losers": losers,
            "lottery_required": lottery_required,
            "boundary_vote": boundary_vote,
            "remaining_seats": remaining_seats,
        }
