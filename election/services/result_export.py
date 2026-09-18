from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db.models import Count, Q

from ..models import Candidate, Election, LotteryDraw


@dataclass(frozen=True)
class ResultExport:
    filename: str
    fieldnames: tuple[str, ...]
    rows: tuple[dict, ...]


def result_label(candidate):
    labels = {
        Candidate.Status.ELECTED: "当選",
        Candidate.Status.NOT_ELECTED: "落選",
        Candidate.Status.LOTTERY: "抽選",
    }
    return labels.get(candidate.status, candidate.get_status_display())


def build_result_export(election):
    if (
        election.phase != Election.Phase.FINAL
        or election.status != Election.Status.COUNTED
    ):
        raise ValidationError(
            "CSVを出力できるのは開票済みの本選挙だけです。"
        )
    if LotteryDraw.objects.filter(
        election=election,
        executed_at__isnull=True,
    ).exists():
        raise ValidationError(
            "未実行の抽選があります。抽選完了後に出力してください。"
        )

    candidates = list(
        Candidate.objects
        .filter(election=election)
        .select_related("member")
        .annotate(
            vote_count=Count(
                "ballot_choices__ballot",
                filter=Q(
                    ballot_choices__ballot__election=election,
                ),
                distinct=True,
            )
        )
        .order_by("-vote_count", "member__member_no")
    )
    if not candidates:
        raise ValidationError("候補者が存在しません。")

    common_fields = (
        "result",
        "vote_count",
        "member_no",
        "last_name",
        "first_name",
        "affiliation",
    )
    rows = []
    for candidate in candidates:
        member = candidate.member
        row = {
            "result": result_label(candidate),
            "vote_count": candidate.vote_count,
            "member_no": member.member_no,
            "last_name": member.last_name,
            "first_name": member.first_name,
            "affiliation": member.affiliation,
        }
        if election.office == Election.Office.REPRESENTATIVE:
            row.update({
                "category": election.get_representative_category_display(),
                "business_category": member.business_category,
            })
        rows.append(row)

    year = election.cycle.year
    if election.office == Election.Office.PRESIDENT:
        filename = f"president-{year}.csv"
        fieldnames = common_fields
    else:
        category = election.representative_category
        filename = f"representative-{category}-{year}.csv"
        fieldnames = ("category",) + common_fields + ("business_category",)

    return ResultExport(
        filename=filename,
        fieldnames=fieldnames,
        rows=tuple(rows),
    )
