import hashlib
import secrets

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from election.models import (
    Candidate,
    LotteryCandidate,
    LotteryDraw,
)


ALGORITHM = "sha256-v1"
DOMAIN = "PASJ-ELECTION-LOTTERY-v1"


def calculate_score(
    lottery,
    candidate,
    seed,
):
    payload = (
        f"{DOMAIN}|"
        f"{lottery.election_id}|"
        f"{lottery.category}|"
        f"{lottery.vote_count}|"
        f"{candidate.member.member_no}|"
        f"{seed}"
    )

    return hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()


def preview_lottery(
    lottery,
):
    """
    抽選対象と残議席を確認する。
    DBは変更しない。
    """

    if lottery.executed_at is not None:
        raise ValidationError(
            "この抽選はすでに実行済みです。"
        )

    candidates = list(
        LotteryCandidate.objects
        .filter(lottery=lottery)
        .select_related(
            "candidate",
            "candidate__member",
        )
        .order_by(
            "candidate__member__member_no"
        )
    )

    if not candidates:
        raise ValidationError(
            "抽選対象者が存在しません。"
        )

    if lottery.seats_remaining <= 0:
        raise ValidationError(
            "残議席数が不正です。"
        )

    if lottery.seats_remaining >= len(candidates):
        raise ValidationError(
            "抽選対象者数以下の残議席ではありません。"
        )

    return {
        "lottery": lottery,
        "candidates": candidates,
        "candidate_count": len(candidates),
        "seats_remaining": lottery.seats_remaining,
    }


@transaction.atomic
def execute_lottery(
    lottery,
):
    """
    抽選を1回だけ実行する。
    """

    lottery = (
        LotteryDraw.objects
        .select_for_update()
        .select_related("election")
        .get(pk=lottery.pk)
    )

    if lottery.executed_at is not None:
        raise ValidationError(
            "この抽選はすでに実行済みです。"
        )

    rows = list(
        LotteryCandidate.objects
        .select_for_update()
        .filter(lottery=lottery)
        .select_related(
            "candidate",
            "candidate__member",
        )
    )

    if not rows:
        raise ValidationError(
            "抽選対象者が存在しません。"
        )

    if lottery.seats_remaining <= 0:
        raise ValidationError(
            "残議席数が不正です。"
        )

    if lottery.seats_remaining >= len(rows):
        raise ValidationError(
            "抽選を行う必要がありません。"
        )

    seed = secrets.token_hex(32)

    scored = []

    for row in rows:
        score = calculate_score(
            lottery,
            row.candidate,
            seed,
        )

        scored.append(
            (
                score,
                row,
            )
        )

    scored.sort(
        key=lambda item: item[0]
    )

    selected_ids = {
        row.pk
        for _, row
        in scored[:lottery.seats_remaining]
    }

    result_lines = []

    for score, row in scored:
        selected = (
            row.pk in selected_ids
        )

        row.score = score
        row.selected = selected

        row.save(
            update_fields=[
                "score",
                "selected",
            ]
        )

        candidate = row.candidate

        candidate.status = (
            Candidate.Status.ELECTED
            if selected
            else Candidate.Status.NOT_ELECTED
        )

        candidate.save(
            update_fields=["status"]
        )

        result_lines.append(
            (
                f"{candidate.member.member_no}:"
                f"{score}:"
                f"{int(selected)}"
            )
        )

    result_hash = hashlib.sha256(
        "\n".join(result_lines)
        .encode("utf-8")
    ).hexdigest()

    lottery.seed = seed
    lottery.algorithm = ALGORITHM
    lottery.result_hash = result_hash
    lottery.executed_at = timezone.now()

    lottery.save(
        update_fields=[
            "seed",
            "algorithm",
            "result_hash",
            "executed_at",
        ]
    )

    return lottery
