"""選挙管理委員向けの年度・候補者・投票・開票管理画面。"""
from io import StringIO

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.db.models import Count, Q
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from .forms import ElectionCycleManagementForm, PaperBallotForm
from .models import (
    Candidate,
    CandidateStatusChange,
    Election,
    ElectionCycle,
    MemberSnapshot,
    VoterParticipation,
)
from .permissions import accessible_cycles, require_cycle_access
from .responses import result_csv_response
from .services.counting import commit_election_count, preview_election_count
from .services.cycle_setup import setup_cycle
from .services.paper_voting import accept_paper_votes, create_paper_ballot
from .services.result_export import build_result_export, result_csv_available


@staff_member_required(login_url="election:login")
def management_cycle_list(request):
    cycles = list(accessible_cycles(request.user))
    # Keep independent one-to-many relations out of the same aggregate JOIN.
    for cycle in cycles:
        cycle.election_count = cycle.elections.count()
        cycle.member_count = cycle.members.count()
        cycle.voted_count = VoterParticipation.objects.filter(
            election__cycle=cycle, voted_at__isnull=False,
        ).count()
    return render(request, "election/management/cycle_list.html", {"cycles": cycles})


@staff_member_required(login_url="election:login")
def management_cycle_detail(request, cycle_year):
    cycle = get_object_or_404(accessible_cycles(request.user), year=cycle_year)
    elections = list(cycle.elections.all())
    for election in elections:
        participation_stats = election.voter_participations.aggregate(
            voter_count=Count("pk"),
            email_sent_count=Count("pk", filter=Q(email_sent_at__isnull=False)),
            voted_count=Count("pk", filter=Q(voted_at__isnull=False)),
        )
        election.voter_count = participation_stats["voter_count"]
        election.email_sent_count = participation_stats["email_sent_count"]
        election.voted_count = participation_stats["voted_count"]
        election.ballot_count = election.ballots.count()
        candidate_stats = election.candidates.aggregate(
            total=Count("pk"),
            non_declined=Count("pk", filter=~Q(status=Candidate.Status.DECLINED)),
        )
        election.candidate_count = candidate_stats["total"]
        election.non_declined_candidate_count = candidate_stats["non_declined"]
    office_order = {
        (Election.Office.PRESIDENT, ""): 0,
        (
            Election.Office.REPRESENTATIVE,
            Election.RepresentativeCategory.GENERAL,
        ): 1,
        (
            Election.Office.REPRESENTATIVE,
            Election.RepresentativeCategory.CORPORATE,
        ): 2,
    }
    elections.sort(key=lambda election: (
        election.phase != Election.Phase.PRELIMINARY,
        office_order[(election.office, election.representative_category)],
    ))
    for election in elections:
        election.turnout = (
            election.voted_count / election.voter_count * 100
            if election.voter_count else 0
        )
    cycle_form = None
    if request.user.is_superuser:
        cycle_form = ElectionCycleManagementForm(instance=cycle)
    return render(request, "election/management/cycle_detail.html", {
        "cycle": cycle, "elections": elections,
        "member_count": cycle.members.count(),
        "status_choices": Election.Status.choices,
        "cycle_form": cycle_form,
    })


@staff_member_required(login_url="election:login")
def management_cycle_form(request, cycle_year=None):

    if not request.user.is_superuser:
        raise PermissionDenied("選挙年度を設定できるのはスーパーユーザーだけです。")
    cycle = get_object_or_404(ElectionCycle, year=cycle_year) if cycle_year else None
    form = ElectionCycleManagementForm(request.POST or None, instance=cycle)
    if request.method == "POST" and form.is_valid():
        cycle = form.save()
        result = setup_cycle(cycle)
        messages.success(
            request,
            f"{cycle.year}年度を保存しました。選挙{result.created_elections}件を追加し、"
            f"既存{result.existing_elections}件を同期しました。",
        )
        return redirect("election:management_cycle_detail", cycle_year=cycle.year)
    return render(request, "election/management/cycle_form.html", {
        "form": form, "cycle": cycle,
    })


@staff_member_required(login_url="election:login")
@require_POST
def management_election_status(request, cycle_year, election_id):
    election = get_management_election(request, cycle_year, election_id)
    status = request.POST.get("status")
    if status not in {value for value, _label in Election.Status.choices}:
        messages.error(request, "選挙状態の指定が正しくありません。")
    else:
        election.status = status
        election.save(update_fields=["status"])
        messages.success(request, f"{election}の状態を更新しました。")
    return redirect("election:management_cycle_detail", cycle_year=cycle_year)


@staff_member_required(login_url="election:login")
def management_voters(request, cycle_year, election_id):
    if not request.user.is_superuser:
        raise PermissionDenied("有権者管理はスーパーユーザーのみ利用できます。")


    election = get_management_election(request, cycle_year, election_id)
    cycle = election.cycle
    if request.method == "POST":
        if election.status != Election.Status.OPEN:
            messages.error(request, "書面投票受付を設定できるのは「投票受付中」の選挙だけです。")
        else:
            voter_ids = request.POST.getlist("voters")
            scoped_ids = list(election.voter_participations.filter(
                pk__in=voter_ids
            ).values_list("pk", flat=True))
            if len(scoped_ids) != len(set(voter_ids)):
                raise PermissionDenied("対象外の有権者が含まれています。")
            result = accept_paper_votes(scoped_ids)
            if result["accepted"]:
                messages.success(request, f'{result["accepted"]}名を書面投票受付済みにしました。')
            if result["already_voted"]:
                messages.warning(request, f'{result["already_voted"]}名は投票済みのため変更していません。')
        query = request.POST.get("q", "").strip()
        url = reverse("election:management_voters", args=[cycle.year, election.pk])
        return redirect(f"{url}?q={query}" if query else url)

    query = request.GET.get("q", "").strip()
    voters = election.voter_participations.select_related("member")
    if query:
        voters = voters.filter(
            Q(member__member_no__icontains=query)
            | Q(member__last_name__icontains=query)
            | Q(member__first_name__icontains=query)
            | Q(member__email__icontains=query)
        )
    voters = voters.order_by("member__member_no")[:200]
    return render(request, "election/management/voters.html", {
        "cycle": cycle,
        "election": election,
        "voters": voters,
        "query": query,
    })


PRELIMINARY_MANUAL_CANDIDATE_STATUSES = (
    Candidate.Status.ELIGIBLE,
    Candidate.Status.DISQUALIFIED,
)
FINAL_MANUAL_CANDIDATE_STATUSES = (
    Candidate.Status.ACCEPTED,
    Candidate.Status.DELEGATE_RECOMMENDED,
    Candidate.Status.DECLINED,
    Candidate.Status.DISQUALIFIED,
)
FINAL_CANDIDATE_STATUSES = (
    Candidate.Status.ELECTED,
    Candidate.Status.LOTTERY,
    Candidate.Status.NOT_ELECTED,
)


def manual_candidate_status_choices(election):
    statuses = (
        PRELIMINARY_MANUAL_CANDIDATE_STATUSES
        if election.phase == Election.Phase.PRELIMINARY
        else FINAL_MANUAL_CANDIDATE_STATUSES
    )
    return tuple(
        (value, label)
        for value, label in Candidate.Status.choices if value in statuses
        and not (
            election.office == Election.Office.PRESIDENT
            and value == Candidate.Status.ACCEPTED
        )
    )


def ensure_candidate_roster_editable(election):
    if election.status != Election.Status.DRAFT:
        raise ValidationError("候補者名簿を変更できるのは選挙が「準備中」の間だけです。")


def ensure_final_election(election):
    ensure_candidate_roster_editable(election)
    if election.phase != Election.Phase.FINAL:
        raise ValidationError("本選挙候補者を追加・除外できるのは本選挙だけです。")


def get_management_election(request, cycle_year, election_id):
    election = get_object_or_404(
        Election.objects.select_related("cycle"),
        pk=election_id,
        cycle__year=cycle_year,
    )
    require_cycle_access(request.user, election.cycle)
    return election


@staff_member_required(login_url="election:login")
def management_candidates(request, cycle_year, election_id):
    election = get_management_election(request, cycle_year, election_id)
    candidates = election.candidates.select_related("member").order_by(
        "member__member_no"
    )
    history = CandidateStatusChange.objects.filter(
        election=election
    ).select_related("member", "changed_by")[:30]
    member_query = request.GET.get("q", "").strip()
    member_results = MemberSnapshot.objects.none()
    if election.phase == Election.Phase.FINAL and member_query:
        member_results = MemberSnapshot.objects.filter(
            cycle=election.cycle,
            is_eligible_voter=True,
        ).filter(
            Q(member_no__icontains=member_query)
            | Q(last_name__icontains=member_query)
            | Q(first_name__icontains=member_query)
            | Q(email__icontains=member_query)
            | Q(affiliation__icontains=member_query)
        )
        if election.office == Election.Office.REPRESENTATIVE:
            member_results = member_results.filter(
                representative_category=election.representative_category
            )
        member_results = member_results.order_by("member_no")[:50]
    return render(request, "election/management/candidates.html", {
        "cycle": election.cycle,
        "election": election,
        "candidates": candidates,
        "status_choices": manual_candidate_status_choices(election),
        "final_statuses": FINAL_CANDIDATE_STATUSES,
        "history": history,
        "member_query": member_query,
        "member_results": member_results,
        "roster_editable": election.status == Election.Status.DRAFT,
        "can_add_candidates": election.phase == Election.Phase.FINAL,
        "can_accept_candidacy": (
            election.phase == Election.Phase.FINAL
            and election.office == Election.Office.REPRESENTATIVE
        ),
    })


def delete_managed_candidate(request, candidate):
    try:
        with transaction.atomic():
            CandidateStatusChange.objects.create(
                candidate=candidate,
                previous_status=candidate.status,
                new_status=Candidate.Status.DISQUALIFIED,
                changed_by=request.user,
            )
            candidate.delete()
    except ProtectedError:
        messages.error(request, "投票・抽選データから参照されている候補者は削除できません。")
    else:
        messages.success(request, f"{candidate.member}を候補者から削除しました。")


@staff_member_required(login_url="election:login")
@require_POST
def management_candidate_status(request, cycle_year, election_id, candidate_id):
    election = get_management_election(request, cycle_year, election_id)
    candidate = get_object_or_404(
        Candidate.objects.select_related("member", "election"),
        pk=candidate_id,
        election=election,
    )
    new_status = request.POST.get("status")
    allowed_statuses = {value for value, _ in manual_candidate_status_choices(election)}
    try:
        ensure_candidate_roster_editable(election)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        if candidate.status in FINAL_CANDIDATE_STATUSES:
            messages.error(request, "開票処理で確定した候補者状態は手動変更できません。")
        elif new_status == Candidate.Status.DISQUALIFIED:
            delete_managed_candidate(request, candidate)
        elif new_status == candidate.status:
            messages.info(request, "候補者状態に変更はありません。")
        elif new_status not in allowed_statuses:
            messages.error(request, "この候補者状態は手動で設定できません。")
        else:
            previous_status = candidate.status
            candidate.status = new_status
            try:
                candidate.full_clean()
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
            else:
                with transaction.atomic():
                    candidate.save(update_fields=["status"])
                    CandidateStatusChange.objects.create(
                        candidate=candidate,
                        previous_status=previous_status,
                        new_status=new_status,
                        changed_by=request.user,
                    )
                messages.success(
                    request,
                    f"{candidate.member.last_name} {candidate.member.first_name}さんの状態を更新しました。",
                )
    return redirect("election:management_candidates", cycle_year=cycle_year, election_id=election_id)


@staff_member_required(login_url="election:login")
@require_POST
def management_candidate_add(request, cycle_year, election_id, member_id):
    election = get_management_election(request, cycle_year, election_id)
    member = get_object_or_404(MemberSnapshot, pk=member_id, cycle=election.cycle)
    route = request.POST.get("route")
    try:
        ensure_final_election(election)
        if not member.is_eligible_voter:
            raise ValidationError("有権者資格のない会員は候補者に追加できません。")
        if (
            election.office == Election.Office.REPRESENTATIVE
            and member.representative_category != election.representative_category
        ):
            raise ValidationError("会員の所属枠と代議員選挙の枠が一致しません。")
        if route == "accepted":
            if election.office != Election.Office.REPRESENTATIVE:
                raise ValidationError("本人立候補を追加できるのは代議員本選挙だけです。")
            status = Candidate.Status.ACCEPTED
            route_label = "立候補者"
        elif route == "delegate_recommended":
            status = Candidate.Status.DELEGATE_RECOMMENDED
            route_label = "代議員推薦者"
        else:
            raise ValidationError("追加方法の指定が正しくありません。")
        if Candidate.objects.filter(election=election, member=member).exists():
            raise ValidationError("この会員はすでに本選挙候補者へ登録されています。")
        candidate = Candidate(election=election, member=member, status=status)
        candidate.full_clean()
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        with transaction.atomic():
            candidate.save()
            CandidateStatusChange.objects.create(
                candidate=candidate,
                previous_status=status,
                new_status=status,
                changed_by=request.user,
            )
        messages.success(
            request,
            f"{member.last_name} {member.first_name}さんを{route_label}として追加しました。",
        )
    query = request.POST.get("q", "").strip()
    url = reverse("election:management_candidates", args=[cycle_year, election_id])
    return redirect(f"{url}?q={query}" if query else url)


@staff_member_required(login_url="election:login")
@require_POST
def management_candidate_remove(request, cycle_year, election_id, candidate_id):
    election = get_management_election(request, cycle_year, election_id)
    candidate = get_object_or_404(
        Candidate.objects.select_related("member"), pk=candidate_id, election=election
    )
    try:
        ensure_final_election(election)
        if candidate.status in FINAL_CANDIDATE_STATUSES:
            raise ValidationError("開票処理で確定した候補者は除外できません。")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        delete_managed_candidate(request, candidate)
    return redirect("election:management_candidates", cycle_year=cycle_year, election_id=election_id)


@staff_member_required(login_url="election:login")
def management_email_preview(request, cycle_year, election_id):
    election = get_management_election(request, cycle_year, election_id)
    if not election.is_voting_open:
        messages.error(request, "投票期間中かつ「投票受付中」の選挙に限りメールを送信できます。")
        return redirect("election:management_cycle_detail", cycle_year=cycle_year)
    voters = election.voter_participations.filter(
        voted_at__isnull=True, email_sent_at__isnull=True,
    )
    sent_count = election.voter_participations.filter(
        email_sent_at__isnull=False
    ).count()
    send_disabled = election.voter_participations.filter(
        email_send_attempts__gt=0
    ).exists()
    return render(request, "election/management/email_preview.html", {
        "cycle": election.cycle, "election": election,
        "target_count": voters.count(),
        "existing_token_count": voters.filter(token_hash__isnull=False).count(),
        "sent_count": sent_count,
        "send_disabled": send_disabled,
    })


@staff_member_required(login_url="election:login")
@require_POST
def management_email_send(request, cycle_year, election_id):
    election = get_management_election(request, cycle_year, election_id)
    if not election.is_voting_open:
        messages.error(request, "投票期間中かつ「投票受付中」の選挙に限りメールを送信できます。")
        return redirect("election:management_cycle_detail", cycle_year=cycle_year)
    if election.voter_participations.filter(email_send_attempts__gt=0).exists():
        messages.error(
            request,
            "この選挙ではメールを送信済みです。一括送信は再実行できません。"
        )
        return redirect(
            "election:management_email_preview",
            cycle_year=cycle_year,
            election_id=election_id,
        )
    voters = election.voter_participations.filter(
        voted_at__isnull=True, email_sent_at__isnull=True,
    )
    if voters.filter(token_hash__isnull=False).exists():
        messages.error(request, "既存トークンがある未送信者がいるため送信できません。")
        return redirect("election:management_email_preview", cycle_year=cycle_year, election_id=election_id)
    marker = "EMAIL_TOKEN_MARKER"
    voting_url = request.build_absolute_uri(reverse("election:vote_entry", args=[marker]))
    before = election.voter_participations.filter(email_sent_at__isnull=False).count()
    target_count = voters.count()
    try:
        call_command(
            "send_voting_emails", cycle=election.cycle.year,
            office=election.office, phase=election.phase,
            category=election.representative_category or None,
            base_url=voting_url.replace(marker + "/", ""),
            stdout=StringIO(), stderr=StringIO(),
        )
    except CommandError as exc:
        messages.error(request, str(exc))
    else:
        after = election.voter_participations.filter(email_sent_at__isnull=False).count()
        sent = after - before
        level = messages.SUCCESS if sent == target_count else messages.WARNING
        messages.add_message(request, level, f"投票メールを{sent}件送信しました。未送信は{target_count - sent}件です。")
    return redirect("election:management_cycle_detail", cycle_year=cycle_year)


@staff_member_required(login_url="election:login")
def management_count_preview(request, cycle_year, election_id):
    election = get_management_election(request, cycle_year, election_id)
    try:
        preview = preview_election_count(election)
    except ValidationError as exc:
        messages.error(request, str(exc))
        return redirect("election:management_cycle_detail", cycle_year=cycle_year)
    return render(request, "election/management/count_preview.html", {
        "cycle": election.cycle, "election": election, "preview": preview,
        "result_csv_available": result_csv_available(election),
    })


@staff_member_required(login_url="election:login")
@require_GET
def management_result_csv(request, cycle_year, election_id):
    election = get_management_election(request, cycle_year, election_id)
    try:
        export = build_result_export(election)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("election:management_cycle_detail", cycle_year=cycle_year)
    return result_csv_response(export)


@staff_member_required(login_url="election:login")
@require_POST
def management_count_confirm(request, cycle_year, election_id):
    election = get_management_election(request, cycle_year, election_id)
    try:
        preview = commit_election_count(election)
    except ValidationError as exc:
        messages.error(request, str(exc))
        return redirect("election:management_cycle_detail", cycle_year=cycle_year)
    needs_lottery = (
        preview.get("lottery_required", False)
        if preview["kind"] != "representative_final"
        else preview["result"]["lottery_required"]
    )
    messages.add_message(
        request, messages.WARNING if needs_lottery else messages.SUCCESS,
        "開票を確定しました。" + ("抽選が必要です。" if needs_lottery else ""),
    )
    return redirect("election:management_cycle_detail", cycle_year=cycle_year)


@staff_member_required(login_url="election:login")
def management_paper_ballot(request, cycle_year, election_id):

    election = get_management_election(request, cycle_year, election_id)
    if election.status != Election.Status.OPEN:
        messages.error(request, "書面票を入力できるのは「投票受付中」の選挙だけです。")
        return redirect("election:management_cycle_detail", cycle_year=cycle_year)
    form = PaperBallotForm(request.POST or None, election=election)
    if request.method == "POST" and form.is_valid():
        try:
            create_paper_ballot(election, form.cleaned_data["candidates"])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "書面票を匿名票として1票登録しました。")
            return redirect("election:management_paper_ballot", cycle_year=cycle_year, election_id=election_id)
    paper_ballot_count = election.ballots.filter(voting_method="paper").count()
    paper_voter_count = election.voter_participations.filter(
        voting_method="paper", voted_at__isnull=False,
    ).count()
    return render(request, "election/management/paper_ballot.html", {
        "cycle": election.cycle, "election": election, "form": form,
        "paper_ballot_count": paper_ballot_count,
        "paper_voter_count": paper_voter_count,
        "can_register_paper_ballot": paper_ballot_count < paper_voter_count,
    })
