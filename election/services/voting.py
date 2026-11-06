"""電子投票と書面投票で共有する候補者・選択人数のルール。"""
import hashlib
import hmac
import random

from django.conf import settings

from ..models import Candidate, Election


def get_valid_candidate_statuses(election):
    """選挙フェーズごとに、投票対象として有効な候補者状態を返す。"""
    if election.phase == Election.Phase.FINAL:
        statuses = [
            Candidate.Status.QUALIFIED,
            Candidate.Status.DELEGATE_RECOMMENDED,
        ]
        if election.office == Election.Office.REPRESENTATIVE:
            statuses.append(Candidate.Status.ACCEPTED)
        return statuses
    return [Candidate.Status.ELIGIBLE]


def validate_vote(election, candidate_ids):
    """選択人数と重複を検証し、不正な場合にエラーメッセージを返す。"""
    if len(candidate_ids) != len(set(candidate_ids)):
        return "同じ候補者を重複して選択することはできません。"

    if election.office == Election.Office.REPRESENTATIVE:
        if not candidate_ids:
            return "少なくとも1名を選択してください。"
        if len(candidate_ids) > election.vote_limit:
            return f"代議員は最大{election.vote_limit}名まで選択できます。"
        return None

    if election.office == Election.Office.PRESIDENT:
        if len(candidate_ids) != 1:
            return "会長候補者を1名選択してください。"
        return None

    return "この選挙形式の投票処理はまだ実装されていません。"


def should_show_candidate_route_labels(election):
    return (
        election.phase == Election.Phase.FINAL
        and Candidate.objects.filter(
            election=election,
            status__in=[
                Candidate.Status.ACCEPTED,
                Candidate.Status.DELEGATE_RECOMMENDED,
            ],
        ).exists()
    )


def deterministic_shuffle(candidates, election, voter):
    """有権者ごとに異なり、再読み込みでは変化しない候補者順を生成する。"""
    message = f"{election.pk}:{voter.member.member_no}".encode("utf-8")
    digest = hmac.new(
        settings.SECRET_KEY.encode("utf-8"), message, hashlib.sha256,
    ).digest()
    rng = random.Random(int.from_bytes(digest, byteorder="big"))
    candidates = list(candidates)
    rng.shuffle(candidates)
    return candidates
