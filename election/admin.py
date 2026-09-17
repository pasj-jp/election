from io import StringIO

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse

from .forms import MemberCsvImportForm
from .models import (
    Ballot,
    Candidate,
    Election,
    ElectionCycle,
    LotteryCandidate,
    LotteryDraw,
    MemberSnapshot,
    VoterParticipation,
)

from .services.counting import (
    commit_election_count,
    preview_election_count,
)

from .services.lottery import (
    execute_lottery,
    preview_lottery,
)
from .services.member_import import MemberImportError, import_members


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


@admin.register(ElectionCycle)
class ElectionCycleAdmin(admin.ModelAdmin):
    list_display = (
        "year",
        "name",
        "created_at",
    )

    ordering = (
        "-year",
    )


@admin.register(Election)
class ElectionAdmin(admin.ModelAdmin):

    change_form_template = (
        "admin/election/election/change_form.html"
    )

    list_display = (
        "cycle",
        "office_display",
        "phase_display",
        "status_display",
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
        "phase",
        "status",
    )

    ordering = (
        "-cycle__year",
        "phase",
        "office",
    )

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
        qs = super().get_queryset(request)

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
        ]

        return custom_urls + urls

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
            Election,
            pk=object_id,
        )

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
            Election,
            pk=object_id,
        )

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
            lottery_count = sum(
                1
                for result in [
                    preview["general"],
                    preview["corporate"],
                ]
                if result["lottery_required"]
            )

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
        return obj.get_office_display()

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
class MemberSnapshotAdmin(admin.ModelAdmin):

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
        if request.method == "POST" and form.is_valid():
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
class CandidateAdmin(admin.ModelAdmin):

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


@admin.register(VoterParticipation)
class VoterParticipationAdmin(admin.ModelAdmin):

    list_display = (
        "member",
        "election",
        "token_status",
        "email_status",
        "vote_status",
        "email_send_attempts",
    )

    list_filter = (
        "election__cycle",
        "election__office",
        "election__phase",
        "email_sent_at",
        "voted_at",
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
        "created_at",
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
class LotteryDrawAdmin(admin.ModelAdmin):

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
            LotteryDraw,
            pk=object_id,
        )

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
            LotteryDraw,
            pk=object_id,
        )

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
