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
NOMINATION_THRESHOLDS = {
    Election.Office.REPRESENTATIVE: 3,
    Election.Office.PRESIDENT: 10,
}


def preview_preliminary_count(election):
    if election.phase != Election.Phase.PRELIMINARY:
        raise ValidationError("予備選挙ではありません。")
    if election.status not in [
        Election.Status.CLOSED,
        Election.Status.COUNTED,
    ]:
        raise ValidationError("投票終了後に開票してください。")

    try:
        final = Election.objects.get(
            cycle=election.cycle,
            office=election.office,
            phase=Election.Phase.FINAL,
        )
    except Election.DoesNotExist as exc:
        raise ValidationError("対応する本選挙が存在しません。") from exc

    if (
        election.status == Election.Status.CLOSED
        and final.status != Election.Status.DRAFT
    ):
        raise ValidationError("本選挙が準備中ではありません。")
    if (
        election.status == Election.Status.CLOSED
        and Ballot.objects.filter(election=final).exists()
    ):
        raise ValidationError("本選挙の投票データが既に存在します。")

    threshold = NOMINATION_THRESHOLDS[election.office]
    candidates = list(
        Candidate.objects.filter(
            election=election,
            status=Candidate.Status.ELIGIBLE,
        )
        .select_related("member")
        .annotate(
            vote_count=Count(
                "ballot_choices__ballot",
                filter=Q(ballot_choices__ballot__election=election),
                distinct=True,
            )
        )
        .order_by("-vote_count", "member__member_no")
    )
    qualified = [c for c in candidates if c.vote_count >= threshold]
    for candidate in candidates:
        candidate.will_qualify = candidate.vote_count >= threshold
    return {
        "kind": "preliminary",
        "election": election,
        "final": final,
        "ballot_count": Ballot.objects.filter(election=election).count(),
        "threshold": threshold,
        "candidates": candidates,
        "qualified": qualified,
        "can_confirm": election.status == Election.Status.CLOSED,
    }


@transaction.atomic
def commit_preliminary_count(election):
    election = Election.objects.select_for_update().get(pk=election.pk)
    preview = preview_preliminary_count(election)
    for candidate in preview["qualified"]:
        Candidate.objects.get_or_create(
            election=preview["final"],
            member=candidate.member,
            defaults={"status": Candidate.Status.QUALIFIED},
        )
    election.status = Election.Status.COUNTED
    election.save(update_fields=["status"])
    return preview


def preview_president_final_count(election):
    if not (
        election.office == Election.Office.PRESIDENT
        and election.phase == Election.Phase.FINAL
    ):
        raise ValidationError("会長本選挙ではありません。")
    if election.status not in [
        Election.Status.CLOSED,
        Election.Status.COUNTED,
    ]:
        raise ValidationError("投票終了後に開票してください。")

    candidates = list(
        Candidate.objects.filter(
            election=election,
            status__in=[
                Candidate.Status.QUALIFIED,
                Candidate.Status.ACCEPTED,
                Candidate.Status.ELECTED,
                Candidate.Status.NOT_ELECTED,
                Candidate.Status.LOTTERY,
            ],
        )
        .select_related("member")
        .annotate(
            vote_count=Count(
                "ballot_choices__ballot",
                filter=Q(ballot_choices__ballot__election=election),
                distinct=True,
            )
        )
        .order_by("-vote_count", "member__member_no")
    )
    if not candidates:
        raise ValidationError("会長候補者が存在しません。")
    top_vote = candidates[0].vote_count
    top_candidates = [c for c in candidates if c.vote_count == top_vote]
    lottery = (
        LotteryDraw.objects.filter(
            election=election,
            category=LotteryDraw.Category.PRESIDENT,
        )
        .order_by("-created_at")
        .first()
    )
    return {
        "kind": "president_final",
        "election": election,
        "ballot_count": Ballot.objects.filter(election=election).count(),
        "candidates": candidates,
        "top_candidates": top_candidates,
        "winner": top_candidates[0] if len(top_candidates) == 1 else None,
        "lottery_required": len(top_candidates) > 1,
        "lottery_executed": bool(lottery and lottery.executed_at),
        "can_confirm": election.status == Election.Status.CLOSED,
    }


@transaction.atomic
def commit_president_final_count(election):
    election = Election.objects.select_for_update().get(pk=election.pk)
    preview = preview_president_final_count(election)
    if not preview["can_confirm"]:
        raise ValidationError("投票終了後の選挙だけ開票を確定できます。")
    if preview["lottery_required"]:
        LotteryDraw.objects.filter(
            election=election,
            category=LotteryDraw.Category.PRESIDENT,
            executed_at__isnull=True,
        ).delete()
        lottery = LotteryDraw.objects.create(
            election=election,
            category=LotteryDraw.Category.PRESIDENT,
            vote_count=preview["top_candidates"][0].vote_count,
            seats_remaining=1,
        )
        LotteryCandidate.objects.bulk_create([
            LotteryCandidate(lottery=lottery, candidate=candidate)
            for candidate in preview["top_candidates"]
        ])
        top_candidate_ids = {
            candidate.pk for candidate in preview["top_candidates"]
        }
        for candidate in preview["candidates"]:
            candidate.status = (
                Candidate.Status.LOTTERY
                if candidate.pk in top_candidate_ids
                else Candidate.Status.NOT_ELECTED
            )
            candidate.save(update_fields=["status"])
    else:
        winner = preview["winner"]
        for candidate in preview["candidates"]:
            candidate.status = (
                Candidate.Status.ELECTED
                if candidate.pk == winner.pk
                else Candidate.Status.NOT_ELECTED
            )
            candidate.save(update_fields=["status"])
    election.status = Election.Status.COUNTED
    election.save(update_fields=["status"])
    return preview


def preview_election_count(election):
    if election.phase == Election.Phase.PRELIMINARY:
        return preview_preliminary_count(election)
    if election.office == Election.Office.PRESIDENT:
        return preview_president_final_count(election)
    preview = preview_representative_final_count(election)
    preview["kind"] = "representative_final"
    return preview


def commit_election_count(election):
    if election.status != Election.Status.CLOSED:
        raise ValidationError("投票終了後の選挙だけ開票を確定できます。")
    if election.phase == Election.Phase.PRELIMINARY:
        return commit_preliminary_count(election)
    if election.office == Election.Office.PRESIDENT:
        return commit_president_final_count(election)
    preview = commit_representative_final_count(election)
    preview["kind"] = "representative_final"
    return preview


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
    if (
        election.status == Election.Status.CLOSED
        and LotteryDraw.objects.filter(
            election=election,
            executed_at__isnull=False,
        ).exists()
    ):
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
        "can_confirm": election.status == Election.Status.CLOSED,
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
