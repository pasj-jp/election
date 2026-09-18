import hashlib
import hmac
import random

from django.conf import settings
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import (
    Ballot,
    BallotChoice,
    Candidate,
    Election,
    VoterParticipation,
)


@require_GET
def home(request):
    return render(request, "election/home.html")


def hash_token(token):
    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


def election_is_open(election):
    return election.is_voting_open


def get_valid_candidate_statuses(election):
    """
    選挙フェーズごとに、投票対象として有効な候補者statusを返す。
    """

    if election.phase == Election.Phase.FINAL:
        statuses = [Candidate.Status.QUALIFIED]
        if election.office == Election.Office.REPRESENTATIVE:
            statuses.append(Candidate.Status.ACCEPTED)
        return statuses

    return [
        Candidate.Status.ELIGIBLE,
    ]


def should_show_candidate_route_labels(election):
    return (
        election.phase == Election.Phase.FINAL
        and election.office == Election.Office.REPRESENTATIVE
        and Candidate.objects.filter(
            election=election,
            status=Candidate.Status.ACCEPTED,
        ).exists()
    )


def deterministic_shuffle(candidates, election, voter):
    """
    有権者ごとに異なる候補者順を生成する。
    同じ有権者が再読み込みしても順番は同じ。
    """

    message = (
        f"{election.pk}:"
        f"{voter.member.member_no}"
    ).encode("utf-8")

    digest = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        message,
        hashlib.sha256,
    ).digest()

    seed = int.from_bytes(
        digest,
        byteorder="big",
    )

    rng = random.Random(seed)

    candidates = list(candidates)
    rng.shuffle(candidates)

    return candidates


def get_voter_from_session(request):
    """
    sessionから現在投票中のVoterParticipationを取得。
    """

    session_data = request.session.get(
        "election_vote"
    )

    if not session_data:
        return None

    try:
        return (
            VoterParticipation.objects
            .select_related(
                "election",
                "election__cycle",
                "member",
            )
            .get(
                pk=session_data[
                    "voter_participation_id"
                ],
                election_id=session_data[
                    "election_id"
                ],
            )
        )

    except VoterParticipation.DoesNotExist:
        return None


def validate_vote(election, candidate_ids):
    """
    投票内容をサーバー側で検証する。
    """

    #
    # 重複候補者を拒否
    #
    if len(candidate_ids) != len(set(candidate_ids)):
        return (
            "同じ候補者を重複して"
            "選択することはできません。"
        )

    #
    # 代議員
    #
    if election.office == Election.Office.REPRESENTATIVE:
        if len(candidate_ids) == 0:
            return "少なくとも1名を選択してください。"
        if len(candidate_ids) > election.vote_limit:
            return (
                f"代議員は最大{election.vote_limit}名まで"
                "選択できます。"
            )

        return None

    #
    # 会長本選挙
    #
    if election.office == Election.Office.PRESIDENT:

        if len(candidate_ids) != 1:
            return "会長候補者を1名選択してください。"

        return None

    return (
        "この選挙形式の投票処理は"
        "まだ実装されていません。"
    )


@require_GET
def vote_entry(request, token):
    """
    メール内の固有URLを検証する。
    """

    token_hash = hash_token(token)

    try:
        voter = (
            VoterParticipation.objects
            .select_related(
                "election",
                "election__cycle",
                "member",
            )
            .get(
                token_hash=token_hash
            )
        )

    except VoterParticipation.DoesNotExist:
        raise Http404(
            "この投票URLは無効です。"
        )

    election = voter.election

    #
    # 投票済み
    #
    if voter.voted_at is not None:
        return render(
            request,
            "election/already_voted.html",
            {
                "election": election,
            },
            status=403,
        )

    #
    # 選挙期間チェック
    #
    if not election_is_open(election):
        return render(
            request,
            "election/election_closed.html",
            {
                "election": election,
            },
            status=403,
        )

    #
    # Session fixation対策
    #
    request.session.cycle_key()

    request.session["election_vote"] = {
        "election_id": election.pk,
        "voter_participation_id": voter.pk,
    }

    #
    # 過去の候補選択情報が残っていたら削除
    #
    request.session.pop(
        "selected_candidate_ids",
        None,
    )

    #
    # 投票セッションは30分
    #
    request.session.set_expiry(
        30 * 60
    )

    #
    # tokenをURLから消す
    #
    response = redirect(
        "election:ballot"
    )

    response.status_code = 303

    return response


@require_GET
def ballot(request):
    """
    候補者選択画面。
    """

    voter = get_voter_from_session(
        request
    )

    if voter is None:
        request.session.flush()

        return render(
            request,
            "election/invalid_session.html",
            status=403,
        )

    election = voter.election

    #
    # 選挙期間チェック
    #
    if not election_is_open(election):
        request.session.flush()

        return render(
            request,
            "election/election_closed.html",
            {
                "election": election,
            },
            status=403,
        )

    #
    # 別画面ですでに投票済みになった場合
    #
    if voter.voted_at is not None:
        request.session.flush()

        return render(
            request,
            "election/already_voted.html",
            {
                "election": election,
            },
            status=403,
        )

    #
    # 選挙フェーズに応じた候補者を取得
    #
    candidates = (
        Candidate.objects
        .filter(
            election=election,
            status__in=get_valid_candidate_statuses(
                election
            ),
        )
        .select_related("member")
    )

    candidates = deterministic_shuffle(
        candidates,
        election,
        voter,
    )

    selected_candidate_ids = (
        request.session.get(
            "selected_candidate_ids",
            [],
        )
    )

    return render(
        request,
        "election/ballot.html",
        {
            "election": election,
            "candidates": candidates,
            "vote_limit": election.vote_limit,
            "show_candidate_route_labels": (
                should_show_candidate_route_labels(election)
            ),
            "selected_candidate_ids":
                selected_candidate_ids,
        },
    )


@require_POST
def ballot_confirm(request):
    """
    候補者選択内容を検証し、
    確認画面を表示する。
    """

    voter = get_voter_from_session(
        request
    )

    if voter is None:
        request.session.flush()

        return render(
            request,
            "election/invalid_session.html",
            status=403,
        )

    election = voter.election

    #
    # 選挙期間チェック
    #
    if not election_is_open(election):
        request.session.flush()

        return render(
            request,
            "election/election_closed.html",
            {
                "election": election,
            },
            status=403,
        )

    #
    # 投票済みチェック
    #
    if voter.voted_at is not None:
        request.session.flush()

        return render(
            request,
            "election/already_voted.html",
            {
                "election": election,
            },
            status=403,
        )

    raw_candidate_ids = request.POST.getlist(
        "candidate"
    )

    #
    # 整数以外を拒否
    #
    try:
        candidate_ids = [
            int(candidate_id)
            for candidate_id
            in raw_candidate_ids
        ]

    except ValueError:
        return render(
            request,
            "election/vote_error.html",
            {
                "election": election,
                "message":
                    "不正な候補者が指定されています。",
            },
            status=400,
        )

    #
    # 人数等を検証
    #
    error = validate_vote(
        election,
        candidate_ids,
    )

    if error:
        return render(
            request,
            "election/vote_error.html",
            {
                "election": election,
                "message": error,
            },
            status=400,
        )

    #
    # candidate_idが本当にこのElectionの
    # 有効候補者か確認
    #
    candidates = list(
        Candidate.objects
        .filter(
            election=election,
            status__in=get_valid_candidate_statuses(
                election
            ),
            pk__in=candidate_ids,
        )
        .select_related("member")
    )

    if len(candidates) != len(
        candidate_ids
    ):
        return render(
            request,
            "election/vote_error.html",
            {
                "election": election,
                "message":
                    "無効な候補者が含まれています。",
            },
            status=400,
        )

    #
    # 確認画面からの最終POST用に
    # sessionへ保存
    #
    request.session[
        "selected_candidate_ids"
    ] = candidate_ids

    #
    # 確認画面でも候補者順を固定
    #
    candidates = deterministic_shuffle(
        candidates,
        election,
        voter,
    )

    return render(
        request,
        "election/ballot_confirm.html",
        {
            "election": election,
            "candidates": candidates,
            "show_candidate_route_labels": (
                should_show_candidate_route_labels(election)
            ),
        },
    )


@require_POST
def ballot_submit(request):
    """
    最終確定処理。

    VoterParticipationをSELECT FOR UPDATEでロックし、
    同時送信による二重投票を防ぐ。
    """

    session_data = request.session.get(
        "election_vote"
    )

    candidate_ids = request.session.get(
        "selected_candidate_ids"
    )

    if (
        not session_data
        or candidate_ids is None
    ):
        request.session.flush()

        return render(
            request,
            "election/invalid_session.html",
            status=403,
        )

    try:
        with transaction.atomic():

            #
            # 二重投票防止の核心
            #
            voter = (
                VoterParticipation.objects
                .select_for_update()
                .select_related(
                    "election",
                    "member",
                )
                .get(
                    pk=session_data[
                        "voter_participation_id"
                    ],
                    election_id=session_data[
                        "election_id"
                    ],
                )
            )

            election = voter.election

            #
            # LOCK取得後に投票済みを再確認
            #
            if voter.voted_at is not None:
                return render(
                    request,
                    "election/already_voted.html",
                    {
                        "election":
                            election,
                    },
                    status=403,
                )

            #
            # LOCK取得後に受付期間も再確認
            #
            if not election_is_open(
                election
            ):
                return render(
                    request,
                    "election/election_closed.html",
                    {
                        "election":
                            election,
                    },
                    status=403,
                )

            #
            # 人数制限等を再検証
            #
            error = validate_vote(
                election,
                candidate_ids,
            )

            if error:
                return render(
                    request,
                    "election/vote_error.html",
                    {
                        "election":
                            election,
                        "message":
                            error,
                    },
                    status=400,
                )

            #
            # 最終確定時にも候補者を再検証
            #
            candidates = list(
                Candidate.objects
                .filter(
                    election=election,
                    status__in=
                        get_valid_candidate_statuses(
                            election
                        ),
                    pk__in=candidate_ids,
                )
            )

            if len(candidates) != len(
                candidate_ids
            ):
                return render(
                    request,
                    "election/vote_error.html",
                    {
                        "election":
                            election,
                        "message":
                            "候補者情報が変更されたため"
                            "投票を受け付けられません。",
                    },
                    status=400,
                )

            #
            # 匿名Ballotを作成
            #
            ballot = Ballot.objects.create(
                election=election,
                voting_method=Ballot.VotingMethod.ELECTRONIC,
            )

            #
            # 選択内容を保存
            #
            BallotChoice.objects.bulk_create(
                [
                    BallotChoice(
                        ballot=ballot,
                        candidate=candidate,
                    )
                    for candidate
                    in candidates
                ]
            )

            #
            # 有権者側には投票済み時刻だけ記録
            #
            voter.voted_at = timezone.now()

            voter.voting_method = (
                VoterParticipation.VotingMethod.ELECTRONIC
            )

            voter.save(
                update_fields=[
                    "voted_at",
                    "voting_method",
                ]
            )

        #
        # atomic終了時点でCOMMIT
        #

    except VoterParticipation.DoesNotExist:

        request.session.flush()

        return render(
            request,
            "election/invalid_session.html",
            status=403,
        )

    #
    # 投票完了後は投票用sessionを完全破棄
    #
    request.session.flush()

    return render(
        request,
        "election/vote_completed.html",
        {
            "election": election,
            "submitted_at":
                ballot.submitted_at,
        },
    )
