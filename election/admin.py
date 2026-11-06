from io import StringIO

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse

from .forms import (
    CycleMemberCsvImportForm,
    ElectionCycleAdminForm,
    MemberCsvImportForm,
    PaperBallotForm,
)
from .models import (
    Ballot,
    Candidate,
    CandidateStatusChange,
    Election,
    ElectionCycle,
    LotteryCandidate,
    LotteryDraw,
    MemberSnapshot,
    VoterParticipation,
)

from .permissions import accessible_cycles, can_access_cycle
from .responses import result_csv_response

from .services.counting import (
    commit_election_count,
    preview_election_count,
)
from .services.cycle_setup import setup_cycle

from .services.lottery import (
    execute_lottery,
    preview_lottery,
)
from .services.member_import import MemberImportError, import_members
from .services.paper_voting import accept_paper_votes, create_paper_ballot
from .services.result_export import build_result_export, result_csv_available


admin.site.site_header = "加速器学会選挙システム"
admin.site.site_title = "加速器学会選挙システム"
admin.site.index_title = "選挙管理"


_default_get_app_list = admin.site.get_app_list


def order_admin_models(app_list):
    model_order = {
        "MemberSnapshot": 0,
        "VoterParticipation": 1,
        "Candidate": 2,
    }

    for app in app_list:
        if app["app_label"] == "election":
            app["models"].sort(
                key=lambda model: model_order.get(
                    model["object_name"],
                    len(model_order),
                )
            )

    return app_list


def get_ordered_app_list(request, app_label=None):
    return order_admin_models(
        _default_get_app_list(request, app_label)
    )


admin.site.get_app_list = get_ordered_app_list


class CycleScopedAdminMixin:
    """担当年度に属するオブジェクトだけを管理サイトへ公開する。"""

    cycle_lookup = "cycle"

    def get_object_cycle(self, obj):
        cycle = obj
        for part in self.cycle_lookup.split("__"):
            cycle = getattr(cycle, part)
        return cycle

    def get_queryset(self, request):
        return super().get_queryset(request).filter(**{
            f"{self.cycle_lookup}__in": accessible_cycles(request.user),
        })

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(ElectionCycle)
class ElectionCycleAdmin(admin.ModelAdmin):
    form = ElectionCycleAdminForm
    change_form_template = (
        "admin/election/electioncycle/change_form.html"
    )

    list_display = (
        "year",
        "name",
        "preliminary_start_at",
        "preliminary_end_at",
        "final_start_at",
        "final_end_at",
        "created_at",
    )

    ordering = (
        "-year",
    )

    def get_queryset(self, request):
        return super().get_queryset(request).filter(
            pk__in=accessible_cycles(request.user).values("pk")
        )

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            readonly.append("manager_groups")
        return readonly

    def get_urls(self):
        custom_urls = [
            path(
                "<path:object_id>/import-members/",
                self.admin_site.admin_view(
                    self.import_members_view
                ),
                name="election_electioncycle_import_members",
            ),
        ]
        return custom_urls + super().get_urls()

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        request._cycle_setup_result = setup_cycle(obj)

    def _show_setup_message(self, request):
        result = getattr(request, "_cycle_setup_result", None)
        if not result:
            return
        self.message_user(
            request,
            (
                f"選挙を{result.created_elections}件追加し、"
                f"既存{result.existing_elections}件の期間を同期しました。"
            ),
            messages.SUCCESS,
        )

    def response_add(self, request, obj, post_url_continue=None):
        self._show_setup_message(request)
        return super().response_add(request, obj, post_url_continue)

    def response_change(self, request, obj):
        self._show_setup_message(request)
        return super().response_change(request, obj)

    def import_members_view(self, request, object_id):
        cycle = get_object_or_404(ElectionCycle, pk=object_id)
        if not self.has_change_permission(request, cycle):
            raise PermissionDenied

        form = CycleMemberCsvImportForm(
            request.POST or None,
            request.FILES or None,
        )
        if request.method == "POST" and form.is_valid():
            uploaded_file = form.cleaned_data["csv_file"]
            try:
                result = import_members(uploaded_file.read(), cycle)
                setup_result = setup_cycle(cycle)
            except (MemberImportError, ValueError) as exc:
                form.add_error("csv_file", str(exc))
            else:
                self.message_user(
                    request,
                    (
                        "会員リストを取り込みました。"
                        f" 新規: {result.created_count}件、"
                        f"更新: {result.updated_count}件、"
                        f"変更なし: {result.unchanged_count}件。"
                        f" 有権者: {setup_result.created_voters}件追加、"
                        f"候補者: {setup_result.created_candidates}件追加。"
                    ),
                    messages.SUCCESS,
                )
                for warning in result.warnings:
                    self.message_user(request, warning, messages.WARNING)
                return redirect(
                    "admin:election_electioncycle_change",
                    object_id=cycle.pk,
                )

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "cycle": cycle,
            "form": form,
            "title": "会員リスト取り込み",
        }
        return render(
            request,
            "admin/election/electioncycle/import_members.html",
            context,
        )


@admin.register(Election)
class ElectionAdmin(admin.ModelAdmin):

    fields = (
        "cycle",
        "office",
        "representative_category",
        "phase",
        "start_at",
        "end_at",
        "status",
        "created_at",
    )
    readonly_fields = (
        "cycle",
        "office",
        "representative_category",
        "phase",
        "start_at",
        "end_at",
        "created_at",
    )
    list_editable = ("status",)

    change_form_template = (
        "admin/election/election/change_form.html"
    )

    list_display = (
        "cycle",
        "office_display",
        "phase_display",
        "status",
        "start_at",
        "end_at",
        "voter_count_display",
        "email_sent_display",
        "voted_display",
        "turnout_display",
        "ballot_count_display",
        "integrity_display",
    )

    list_filter = (
        "cycle",
        "office",
        "representative_category",
        "phase",
        "status",
    )

    ordering = (
        "-cycle__year",
        "phase",
        "office",
        "representative_category",
    )

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def response_add(self, request, obj, post_url_continue=None):
        voter_count = obj.voter_participations.count()
        if voter_count:
            self.message_user(
                request,
                f"有権者を{voter_count}名、自動生成しました。",
                messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                "有権者は生成されませんでした。"
                "先にこの年度の会員名簿を取り込んでください。",
                messages.WARNING,
            )

        if obj.phase == Election.Phase.PRELIMINARY:
            candidate_count = obj.candidates.count()
            if candidate_count:
                self.message_user(
                    request,
                    f"候補者を{candidate_count}名、自動生成しました。",
                    messages.SUCCESS,
                )
            else:
                self.message_user(
                    request,
                    "候補者は生成されませんでした。"
                    "先にこの年度の会員名簿を取り込んでください。",
                    messages.WARNING,
                )
        return super().response_add(request, obj, post_url_continue)

    def get_queryset(self, request):
        qs = super().get_queryset(request).filter(
            cycle__in=accessible_cycles(request.user)
        )

        return qs.annotate(
            _voter_count=Count(
                "voter_participations",
                distinct=True,
            ),
            _email_sent_count=Count(
                "voter_participations",
                filter=Q(
                    voter_participations__email_sent_at__isnull=False
                ),
                distinct=True,
            ),
            _voted_count=Count(
                "voter_participations",
                filter=Q(
                    voter_participations__voted_at__isnull=False
                ),
                distinct=True,
            ),
            _ballot_count=Count(
                "ballots",
                distinct=True,
            ),
        )

    def get_urls(self):
        urls = super().get_urls()

        custom_urls = [
            path(
                "<path:object_id>/count-preview/",
                self.admin_site.admin_view(
                    self.count_preview_view
                ),
                name="election_election_count_preview",
            ),
            path(
                "<path:object_id>/count-confirm/",
                self.admin_site.admin_view(
                    self.count_confirm_view
                ),
                name="election_election_count_confirm",
            ),
            path(
                "<path:object_id>/email-preview/",
                self.admin_site.admin_view(
                    self.email_preview_view
                ),
                name="election_election_email_preview",
            ),
            path(
                "<path:object_id>/email-send/",
                self.admin_site.admin_view(
                    self.email_send_view
                ),
                name="election_election_email_send",
            ),
            path(
                "<path:object_id>/paper-ballot/",
                self.admin_site.admin_view(
                    self.paper_ballot_view
                ),
                name="election_election_paper_ballot",
            ),
            path(
                "<path:object_id>/result-csv/",
                self.admin_site.admin_view(
                    self.result_csv_view
                ),
                name="election_election_result_csv",
            ),
        ]

        return custom_urls + urls

    def result_csv_view(self, request, object_id):
        election = get_object_or_404(
            Election.objects.select_related("cycle"),
            pk=object_id,
        )
        if not self.has_view_or_change_permission(request, election):
            raise PermissionDenied
        try:
            export = build_result_export(election)
        except ValidationError as exc:
            self.message_user(request, str(exc), messages.ERROR)
            return redirect(
                "admin:election_election_change",
                object_id=election.pk,
            )

        return result_csv_response(export)

    def paper_ballot_view(self, request, object_id):
        election = get_object_or_404(
            Election.objects.select_related("cycle"),
            pk=object_id,
        )
        if not self.has_change_permission(request, election):
            raise PermissionDenied

        change_url = reverse(
            "admin:election_election_change",
            args=[election.pk],
        )
        if election.status == Election.Status.COUNTED:
            self.message_user(
                request,
                "開票済みの選挙には書面票を登録できません。",
                messages.ERROR,
            )
            return redirect(change_url)

        form = PaperBallotForm(
            request.POST or None,
            election=election,
        )
        if request.method == "POST" and form.is_valid():
            try:
                create_paper_ballot(
                    election,
                    form.cleaned_data["candidates"],
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                self.message_user(
                    request,
                    "書面票を匿名票として1票登録しました。",
                    messages.SUCCESS,
                )
                return redirect(
                    "admin:election_election_paper_ballot",
                    object_id=election.pk,
                )

        context = {
            **self.admin_site.each_context(request),
            "title": "書面票入力",
            "election": election,
            "form": form,
            "opts": self.model._meta,
            "paper_ballot_count": election.ballots.filter(
                voting_method=Ballot.VotingMethod.PAPER,
            ).count(),
            "paper_voter_count": election.voter_participations.filter(
                voting_method=VoterParticipation.VotingMethod.PAPER,
            ).count(),
        }
        return render(
            request,
            "admin/election/election/paper_ballot.html",
            context,
        )

    def get_email_election(self, request, object_id):
        election = get_object_or_404(
            Election.objects.select_related("cycle"),
            pk=object_id,
        )
        if not self.has_change_permission(request, election):
            raise PermissionDenied
        return election

    def email_preview_view(self, request, object_id):
        election = self.get_email_election(request, object_id)
        if not election.is_voting_open:
            self.message_user(
                request,
                "投票期間中かつ「投票受付中」の選挙に限り、"
                "メールを送信できます。",
                level=messages.ERROR,
            )
            return redirect(
                reverse(
                    "admin:election_election_change",
                    args=[election.pk],
                )
            )
        voters = election.voter_participations.filter(
            voted_at__isnull=True,
            email_sent_at__isnull=True,
        )
        context = {
            **self.admin_site.each_context(request),
            "title": "投票メール送信",
            "election": election,
            "target_count": voters.count(),
            "existing_token_count": voters.filter(
                token_hash__isnull=False,
            ).count(),
            "sent_count": election.voter_participations.filter(
                email_sent_at__isnull=False,
            ).count(),
            "opts": self.model._meta,
        }
        return render(
            request,
            "admin/election/election/email_preview.html",
            context,
        )

    def email_send_view(self, request, object_id):
        election = self.get_email_election(request, object_id)
        change_url = reverse(
            "admin:election_election_change",
            args=[election.pk],
        )
        if request.method != "POST":
            return redirect(
                reverse(
                    "admin:election_election_email_preview",
                    args=[election.pk],
                )
            )

        if not election.is_voting_open:
            self.message_user(
                request,
                "投票期間中かつ「投票受付中」の選挙に限り、"
                "メールを送信できます。",
                level=messages.ERROR,
            )
            return redirect(change_url)

        token_marker = "EMAIL_TOKEN_MARKER"
        voting_url = request.build_absolute_uri(
            reverse("election:vote_entry", args=[token_marker])
        )
        base_url = voting_url.replace(token_marker + "/", "")
        before = election.voter_participations.filter(
            email_sent_at__isnull=False,
        ).count()
        target_count = election.voter_participations.filter(
            voted_at__isnull=True,
            email_sent_at__isnull=True,
        ).count()

        try:
            call_command(
                "send_voting_emails",
                cycle=election.cycle.year,
                office=election.office,
                phase=election.phase,
                category=election.representative_category or None,
                base_url=base_url,
                stdout=StringIO(),
                stderr=StringIO(),
            )
        except CommandError as exc:
            self.message_user(
                request,
                str(exc),
                level=messages.ERROR,
            )
            return redirect(change_url)

        after = election.voter_participations.filter(
            email_sent_at__isnull=False,
        ).count()
        sent_count = after - before
        failed_count = target_count - sent_count
        level = (
            messages.SUCCESS
            if failed_count == 0
            else messages.WARNING
        )
        self.message_user(
            request,
            f"投票メールを{sent_count}件送信しました。"
            f" 未送信は{failed_count}件です。",
            level=level,
        )
        return redirect(change_url)

    def count_preview_view(
        self,
        request,
        object_id,
    ):
        election = get_object_or_404(
            self.get_queryset(request),
            pk=object_id,
        )
        if not self.has_change_permission(request, election):
            raise PermissionDenied

        try:
            preview = preview_election_count(election)

        except ValidationError as exc:
            self.message_user(
                request,
                str(exc),
                level=messages.ERROR,
            )

            return redirect(
                reverse(
                    "admin:election_election_change",
                    args=[election.pk],
                )
            )

        context = {
            **self.admin_site.each_context(
                request
            ),
            "title": "開票プレビュー",
            "election": election,
            "preview": preview,
            "opts": self.model._meta,
        }

        template = (
            "admin/election/election/count_preview.html"
            if preview["kind"] == "representative_final"
            else "admin/election/election/count_preview_simple.html"
        )
        return render(request, template, context)

    def count_confirm_view(
        self,
        request,
        object_id,
    ):
        election = get_object_or_404(
            self.get_queryset(request),
            pk=object_id,
        )
        if not self.has_change_permission(request, election):
            raise PermissionDenied

        if request.method != "POST":
            return redirect(
                reverse(
                    "admin:election_election_count_preview",
                    args=[election.pk],
                )
            )

        try:
            preview = commit_election_count(election)

        except ValidationError as exc:
            self.message_user(
                request,
                str(exc),
                level=messages.ERROR,
            )

            return redirect(
                reverse(
                    "admin:election_election_change",
                    args=[election.pk],
                )
            )

        lottery_count = 0
        if preview["kind"] == "representative_final":
            lottery_count = int(preview["result"]["lottery_required"])

        if (
            preview["kind"] == "president_final"
            and preview["lottery_required"]
        ):
            self.message_user(
                request,
                "開票を確定しました。"
                " 最多得票が同票のため抽選が必要です。",
                level=messages.WARNING,
            )
        elif preview["kind"] != "representative_final":
            self.message_user(
                request,
                "開票を確定しました。",
                level=messages.SUCCESS,
            )
        elif lottery_count:
            self.message_user(
                request,
                (
                    "開票を確定しました。"
                    f" 抽選が必要な枠が"
                    f" {lottery_count} 件あります。"
                ),
                level=messages.WARNING,
            )
        else:
            self.message_user(
                request,
                "開票を確定しました。"
                " 抽選はありません。",
                level=messages.SUCCESS,
            )

        return redirect(
            reverse(
                "admin:election_election_change",
                args=[election.pk],
            )
        )

    def changeform_view(
        self,
        request,
        object_id=None,
        form_url="",
        extra_context=None,
    ):
        extra_context = extra_context or {}

        if object_id:
            election = self.get_object(
                request,
                object_id,
            )

            if election:
                voters = (
                    VoterParticipation.objects
                    .filter(election=election)
                )

                total_voters = voters.count()

                email_sent = voters.filter(
                    email_sent_at__isnull=False
                ).count()

                voted = voters.filter(
                    voted_at__isnull=False
                ).count()

                ballot_count = (
                    Ballot.objects
                    .filter(election=election)
                    .count()
                )

                turnout = (
                    voted / total_voters * 100
                    if total_voters
                    else 0.0
                )

                extra_context[
                    "election_summary"
                ] = {
                    "total_voters": total_voters,
                    "email_sent": email_sent,
                    "email_not_sent":
                        total_voters - email_sent,
                    "voted": voted,
                    "not_voted":
                        total_voters - voted,
                    "turnout": turnout,
                    "ballot_count": ballot_count,
                    "integrity":
                        voted == ballot_count,
                }

                extra_context[
                    "result_summary"
                ] = self.build_result_summary(
                    election
                )
                extra_context["result_csv_available"] = result_csv_available(election)

        return super().changeform_view(
            request,
            object_id,
            form_url,
            extra_context,
        )

    def build_result_summary(
        self,
        election,
    ):
        candidates = (
            Candidate.objects
            .filter(election=election)
            .select_related("member")
            .annotate(
                vote_count=Count(
                    "ballot_choices",
                    distinct=True,
                )
            )
        )

        summary = {
            "available": False,
            "office": election.office,
            "phase": election.phase,
        }

        if (
            election.phase
            != Election.Phase.FINAL
        ):
            return summary

        summary["available"] = True

        if (
            election.office
            == Election.Office.PRESIDENT
        ):
            elected = candidates.filter(
                status=Candidate.Status.ELECTED
            ).order_by(
                "-vote_count"
            )

            summary[
                "president_winner"
            ] = elected.first()

            summary[
                "president_candidate_count"
            ] = candidates.count()

            return summary

        if (
            election.office
            == Election.Office.REPRESENTATIVE
        ):
            general_elected = (
                candidates
                .filter(
                    status=Candidate.Status.ELECTED,
                    member__representative_category=(
                        MemberSnapshot
                        .RepresentativeCategory
                        .GENERAL
                    ),
                )
                .order_by(
                    "-vote_count",
                    "member__member_no",
                )
            )

            corporate_elected = (
                candidates
                .filter(
                    status=Candidate.Status.ELECTED,
                    member__representative_category=(
                        MemberSnapshot
                        .RepresentativeCategory
                        .CORPORATE
                    ),
                )
                .order_by(
                    "-vote_count",
                    "member__member_no",
                )
            )

            pending_lotteries = (
                LotteryDraw.objects
                .filter(
                    election=election,
                    executed_at__isnull=True,
                )
            )

            completed_lotteries = (
                LotteryDraw.objects
                .filter(
                    election=election,
                    executed_at__isnull=False,
                )
            )

            summary.update(
                {
                    "general_elected":
                        list(general_elected),
                    "corporate_elected":
                        list(corporate_elected),
                    "general_count":
                        general_elected.count(),
                    "corporate_count":
                        corporate_elected.count(),
                    "pending_lottery_count":
                        pending_lotteries.count(),
                    "completed_lottery_count":
                        completed_lotteries.count(),
                }
            )

        return summary

    @admin.display(
        description="選挙",
        ordering="office",
    )
    def office_display(self, obj):
        return obj.election_type_display

    @admin.display(
        description="区分",
        ordering="phase",
    )
    def phase_display(self, obj):
        return obj.get_phase_display()

    @admin.display(
        description="状態",
        ordering="status",
    )
    def status_display(self, obj):
        return obj.get_status_display()

    @admin.display(
        description="有権者",
    )
    def voter_count_display(self, obj):
        return obj._voter_count

    @admin.display(
        description="メール送信",
    )
    def email_sent_display(self, obj):
        return (
            f"{obj._email_sent_count}"
            f" / "
            f"{obj._voter_count}"
        )

    @admin.display(
        description="投票済",
    )
    def voted_display(self, obj):
        return (
            f"{obj._voted_count}"
            f" / "
            f"{obj._voter_count}"
        )

    @admin.display(
        description="投票率",
    )
    def turnout_display(self, obj):
        if obj._voter_count == 0:
            return "0.00 %"

        turnout = (
            obj._voted_count
            / obj._voter_count
            * 100
        )

        return f"{turnout:.2f} %"

    @admin.display(
        description="Ballot",
    )
    def ballot_count_display(self, obj):
        return obj._ballot_count

    @admin.display(
        description="整合性",
        boolean=True,
    )
    def integrity_display(self, obj):
        return (
            obj._voted_count
            == obj._ballot_count
        )


@admin.register(MemberSnapshot)
class MemberSnapshotAdmin(CycleScopedAdminMixin, admin.ModelAdmin):

    change_list_template = (
        "admin/election/membersnapshot/change_list.html"
    )

    list_display = (
        "member_no",
        "last_name",
        "first_name",
        "email",
        "employee_type",
        "business_category",
        "representative_category",
        "is_eligible_voter",
    )

    list_filter = (
        "cycle",
        "representative_category",
        "is_eligible_voter",
    )

    search_fields = (
        "member_no",
        "last_name",
        "first_name",
        "email",
        "affiliation",
    )

    ordering = (
        "member_no",
    )

    def get_urls(self):
        custom_urls = [
            path(
                "import-csv/",
                self.admin_site.admin_view(self.import_csv_view),
                name="election_membersnapshot_import_csv",
            ),
        ]
        return custom_urls + super().get_urls()

    def import_csv_view(self, request):
        if not self.has_change_permission(request):
            raise PermissionDenied

        form = MemberCsvImportForm(
            request.POST or None,
            request.FILES or None,
        )
        form.fields["cycle"].queryset = accessible_cycles(request.user)
        if request.method == "POST" and form.is_valid():
            cycle = form.cleaned_data["cycle"]
            if not can_access_cycle(request.user, cycle):
                raise PermissionDenied
            uploaded_file = form.cleaned_data["csv_file"]
            try:
                result = import_members(
                    uploaded_file.read(),
                    form.cleaned_data["cycle"],
                )
            except MemberImportError as exc:
                form.add_error("csv_file", str(exc))
            else:
                self.message_user(
                    request,
                    "CSV名簿を取り込みました。"
                    f" 新規: {result.created_count}件、"
                    f"更新: {result.updated_count}件、"
                    f"変更なし: {result.unchanged_count}件。",
                    messages.SUCCESS,
                )
                for warning in result.warnings:
                    self.message_user(request, warning, messages.WARNING)
                return redirect("admin:election_membersnapshot_changelist")

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "form": form,
            "title": "会員名簿CSV取り込み",
        }
        return render(
            request,
            "admin/election/membersnapshot/import_csv.html",
            context,
        )


@admin.register(Candidate)
class CandidateAdmin(CycleScopedAdminMixin, admin.ModelAdmin):
    cycle_lookup = "election__cycle"

    list_display = (
        "election",
        "member",
        "representative_category_display",
        "vote_count_display",
        "status",
    )

    list_filter = (
        "election__cycle",
        "election__office",
        "election__phase",
        "status",
        "member__representative_category",
    )

    search_fields = (
        "member__member_no",
        "member__last_name",
        "member__first_name",
        "member__email",
    )

    ordering = (
        "election",
        "member__member_no",
    )

    def is_manifesto_applicable(self, obj):
        return bool(
            obj
            and obj.election.office == Election.Office.PRESIDENT
            and obj.election.phase == Election.Phase.FINAL
        )

    def get_fields(self, request, obj=None):
        fields = list(super().get_fields(request, obj))
        if (
            not self.is_manifesto_applicable(obj)
            and "manifesto" in fields
        ):
            fields.remove("manifesto")
        return fields

    def get_queryset(self, request):
        qs = super().get_queryset(request)

        return (
            qs.select_related(
                "member",
                "election",
            )
            .annotate(
                _vote_count=Count(
                    "ballot_choices",
                    distinct=True,
                )
            )
        )

    @admin.display(
        description="枠",
    )
    def representative_category_display(
        self,
        obj,
    ):
        return (
            obj.member
            .get_representative_category_display()
        )

    @admin.display(
        description="得票",
    )
    def vote_count_display(self, obj):
        if obj.election.status != Election.Status.COUNTED:
            return "—"

        return obj._vote_count


@admin.register(CandidateStatusChange)
class CandidateStatusChangeAdmin(admin.ModelAdmin):
    list_display = (
        "changed_at", "election", "member", "candidate", "previous_status", "new_status", "changed_by",
    )
    list_filter = ("election__cycle", "previous_status", "new_status")
    readonly_fields = (
        "candidate", "election", "member", "previous_status", "new_status", "changed_by", "changed_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(VoterParticipation)
class VoterParticipationAdmin(CycleScopedAdminMixin, admin.ModelAdmin):
    cycle_lookup = "election__cycle"

    actions = ("accept_as_paper_vote", "resend_voting_emails")

    list_display = (
        "member",
        "election",
        "token_status",
        "email_status",
        "vote_status",
        "voting_method_display",
        "email_send_attempts",
    )

    list_filter = (
        "election__cycle",
        "election__office",
        "election__phase",
        "email_sent_at",
        "voted_at",
        "voting_method",
    )

    search_fields = (
        "member__member_no",
        "member__last_name",
        "member__first_name",
        "member__email",
    )

    readonly_fields = (
        "token_hash",
        "token_issued_at",
        "email_sent_at",
        "email_send_attempts",
        "voted_at",
        "voting_method",
        "created_at",
    )

    @admin.action(description="選択した有権者を書面投票受付済みにする")
    def accept_as_paper_vote(self, request, queryset):
        result = accept_paper_votes(
            queryset.values_list("pk", flat=True)
        )
        if result["accepted"]:
            self.message_user(
                request,
                f'{result["accepted"]}名を書面投票受付済みにしました。',
                messages.SUCCESS,
            )
        if result["already_voted"]:
            self.message_user(
                request,
                f'{result["already_voted"]}名は投票済みのため変更していません。',
                messages.WARNING,
            )
        if result["counted"]:
            self.message_user(
                request,
                f'{result["counted"]}名は開票済みの選挙のため変更していません。',
                messages.WARNING,
            )

    @admin.action(description="選択した有権者へ投票メールを再送する")
    def resend_voting_emails(self, request, queryset):
        token_marker = "EMAIL_TOKEN_MARKER"
        voting_url = request.build_absolute_uri(
            reverse("election:vote_entry", args=[token_marker])
        )
        base_url = voting_url.replace(token_marker + "/", "")
        sent_count = 0
        for voter in queryset.select_related("election__cycle", "member"):
            try:
                call_command(
                    "resend_voting_email",
                    cycle=voter.election.cycle.year,
                    office=voter.election.office,
                    phase=voter.election.phase,
                    category=voter.election.representative_category or None,
                    member=voter.member.member_no,
                    base_url=base_url,
                    stdout=StringIO(),
                    stderr=StringIO(),
                )
            except CommandError as exc:
                self.message_user(
                    request,
                    f"{voter.member}: {exc}",
                    level=messages.ERROR,
                )
            else:
                sent_count += 1
        if sent_count:
            self.message_user(
                request,
                f"投票メールを{sent_count}名に再送しました。",
                level=messages.SUCCESS,
            )

    @admin.display(
        description="Token",
        boolean=True,
    )
    def token_status(self, obj):
        return bool(obj.token_hash)

    @admin.display(
        description="メール",
        boolean=True,
    )
    def email_status(self, obj):
        return (
            obj.email_sent_at
            is not None
        )

    @admin.display(
        description="投票済",
        boolean=True,
    )
    def vote_status(self, obj):
        return (
            obj.voted_at
            is not None
        )

    @admin.display(description="投票方法")
    def voting_method_display(self, obj):
        return obj.get_voting_method_display() or "—"


class LotteryCandidateInline(admin.TabularInline):
    model = LotteryCandidate

    extra = 0

    readonly_fields = (
        "candidate",
        "score",
        "selected",
    )

    can_delete = False

    def has_add_permission(
        self,
        request,
        obj=None,
    ):
        return False



@admin.register(LotteryDraw)
class LotteryDrawAdmin(CycleScopedAdminMixin, admin.ModelAdmin):
    cycle_lookup = "election__cycle"

    change_form_template = (
        "admin/election/lotterydraw/change_form.html"
    )

    list_display = (
        "election",
        "category_display",
        "vote_count",
        "seats_remaining",
        "candidate_count_display",
        "executed_at",
    )

    list_filter = (
        "election__cycle",
        "category",
        "executed_at",
    )

    readonly_fields = (
        "election",
        "category",
        "vote_count",
        "seats_remaining",
        "seed",
        "algorithm",
        "executed_at",
        "result_hash",
        "created_at",
    )

    inlines = (
        LotteryCandidateInline,
    )

    def get_urls(self):
        urls = super().get_urls()

        custom_urls = [
            path(
                "<path:object_id>/lottery-preview/",
                self.admin_site.admin_view(
                    self.lottery_preview_view
                ),
                name="election_lotterydraw_preview",
            ),
            path(
                "<path:object_id>/lottery-execute/",
                self.admin_site.admin_view(
                    self.lottery_execute_view
                ),
                name="election_lotterydraw_execute",
            ),
        ]

        return custom_urls + urls

    def lottery_preview_view(
        self,
        request,
        object_id,
    ):
        lottery = get_object_or_404(
            self.get_queryset(request),
            pk=object_id,
        )
        if not self.has_change_permission(request, lottery):
            raise PermissionDenied

        try:
            preview = preview_lottery(
                lottery
            )

        except ValidationError as exc:
            self.message_user(
                request,
                str(exc),
                level=messages.ERROR,
            )

            return redirect(
                reverse(
                    "admin:election_lotterydraw_change",
                    args=[lottery.pk],
                )
            )

        context = {
            **self.admin_site.each_context(
                request
            ),
            "title": "抽選プレビュー",
            "lottery": lottery,
            "preview": preview,
            "opts": self.model._meta,
        }

        return render(
            request,
            "admin/election/lotterydraw/lottery_preview.html",
            context,
        )

    def lottery_execute_view(
        self,
        request,
        object_id,
    ):
        lottery = get_object_or_404(
            self.get_queryset(request),
            pk=object_id,
        )
        if not self.has_change_permission(request, lottery):
            raise PermissionDenied

        if request.method != "POST":
            return redirect(
                reverse(
                    "admin:election_lotterydraw_preview",
                    args=[lottery.pk],
                )
            )

        try:
            lottery = execute_lottery(
                lottery
            )

        except ValidationError as exc:
            self.message_user(
                request,
                str(exc),
                level=messages.ERROR,
            )

            return redirect(
                reverse(
                    "admin:election_lotterydraw_change",
                    args=[lottery.pk],
                )
            )

        self.message_user(
            request,
            "抽選を実行しました。",
            level=messages.SUCCESS,
        )

        return redirect(
            reverse(
                "admin:election_lotterydraw_change",
                args=[lottery.pk],
            )
        )

    @admin.display(
        description="区分",
    )
    def category_display(self, obj):
        return obj.get_category_display()

    @admin.display(
        description="抽選対象",
    )
    def candidate_count_display(self, obj):
        return obj.candidates.count()

    def has_add_permission(self, request):
        return False

    def has_delete_permission(
        self,
        request,
        obj=None,
    ):
        return False
