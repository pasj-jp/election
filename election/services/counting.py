from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q

from election.models import (
    Ballot,
    Candidate,
    Election,
    LotteryCandidate,
    LotteryDraw,
    MemberSnapshot,
)


GENERAL_SEATS = 25
CORPORATE_SEATS = 5


def get_representative_final_candidates(
    election,
    category,
):
    """
    代議員本選挙の候補者を得票数付きで取得する。
    """

    return list(
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


def calculate_category_result(
    election,
    category,
    seats,
):
    """
    1つの枠について、

    - 確定当選者
    - 抽選対象者
    - 落選者
    - 境界得票
    - 残議席

    を計算する。
    """

    candidates = get_representative_final_candidates(
        election,
        category,
    )

    if not candidates:
        return {
            "category": category,
            "seats": seats,
            "candidates": [],
            "winners": [],
            "tied": [],
            "losers": [],
            "lottery_required": False,
            "boundary_vote": None,
            "remaining_seats": seats,
        }

    #
    # 候補者数 <= 定数
    #
    if len(candidates) <= seats:
        return {
            "category": category,
            "seats": seats,
            "candidates": candidates,
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
        candidate
        for candidate in candidates
        if candidate.vote_count > boundary_vote
    ]

    tied = [
        candidate
        for candidate in candidates
        if candidate.vote_count == boundary_vote
    ]

    losers = [
        candidate
        for candidate in candidates
        if candidate.vote_count < boundary_vote
    ]

    remaining_seats = (
        seats - len(winners)
    )

    lottery_required = (
        len(tied) > remaining_seats
    )

    #
    # 同票者全員が残議席内に入るなら
    # 抽選は不要
    #
    if not lottery_required:
        winners.extend(tied)
        tied = []

    return {
        "category": category,
        "seats": seats,
        "candidates": candidates,
        "winners": winners,
        "tied": tied,
        "losers": losers,
        "lottery_required": lottery_required,
        "boundary_vote": boundary_vote,
        "remaining_seats": remaining_seats,
    }


def preview_representative_final_count(
    election,
):
    """
    代議員本選挙の開票プレビュー。
    DBは変更しない。
    """

    if (
        election.office
        != Election.Office.REPRESENTATIVE
        or election.phase
        != Election.Phase.FINAL
    ):
        raise ValidationError(
            "代議員本選挙ではありません。"
        )

    if election.status not in [
        Election.Status.CLOSED,
        Election.Status.COUNTED,
    ]:
        raise ValidationError(
            "投票終了(CLOSED)後に開票してください。"
        )

    #
    # 既に抽選実行済みなら再開票禁止
    #
    if LotteryDraw.objects.filter(
        election=election,
        executed_at__isnull=False,
    ).exists():
        raise ValidationError(
            "抽選実行済みのため、再開票できません。"
        )

    general = calculate_category_result(
        election,
        MemberSnapshot.RepresentativeCategory.GENERAL,
        GENERAL_SEATS,
    )

    corporate = calculate_category_result(
        election,
        MemberSnapshot.RepresentativeCategory.CORPORATE,
        CORPORATE_SEATS,
    )

    return {
        "election": election,
        "ballot_count": (
            Ballot.objects
            .filter(election=election)
            .count()
        ),
        "general": general,
        "corporate": corporate,
    }


@transaction.atomic
def commit_representative_final_count(
    election,
):
    """
    代議員本選挙の開票結果を確定する。
    """

    #
    # Election自体をロック
    #
    election = (
        Election.objects
        .select_for_update()
        .get(pk=election.pk)
    )

    preview = preview_representative_final_count(
        election
    )

    #
    # 未実行の旧LotteryDrawがあれば消す
    #
    LotteryDraw.objects.filter(
        election=election,
        executed_at__isnull=True,
    ).delete()

    for result in [
        preview["general"],
        preview["corporate"],
    ]:

        for candidate in result["winners"]:
            candidate.status = (
                Candidate.Status.ELECTED
            )

            candidate.save(
                update_fields=["status"]
            )

        for candidate in result["losers"]:
            candidate.status = (
                Candidate.Status.NOT_ELECTED
            )

            candidate.save(
                update_fields=["status"]
            )

        for candidate in result["tied"]:
            candidate.status = (
                Candidate.Status.LOTTERY
            )

            candidate.save(
                update_fields=["status"]
            )

        #
        # 抽選が必要ならLotteryDraw生成
        #
        if result["lottery_required"]:

            lottery = LotteryDraw.objects.create(
                election=election,
                category=result["category"],
                vote_count=result["boundary_vote"],
                seats_remaining=result[
                    "remaining_seats"
                ],
            )

            LotteryCandidate.objects.bulk_create(
                [
                    LotteryCandidate(
                        lottery=lottery,
                        candidate=candidate,
                    )
                    for candidate
                    in result["tied"]
                ]
            )

    election.status = Election.Status.COUNTED

    election.save(
        update_fields=["status"]
    )

    return preview
