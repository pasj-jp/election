from django import forms
from django.db import models

from .models import Candidate, Election, ElectionCycle
from .views import get_valid_candidate_statuses, validate_vote


class ElectionCycleAdminForm(forms.ModelForm):
    class Meta:
        model = ElectionCycle
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in (
            "preliminary_start_at",
            "preliminary_end_at",
            "final_start_at",
            "final_end_at",
        ):
            self.fields[field_name].required = True


class ElectionCycleManagementForm(ElectionCycleAdminForm):
    """年度別管理画面で使用する選挙年度フォーム。"""

    class Meta(ElectionCycleAdminForm.Meta):
        fields = (
            "year", "name",
            "preliminary_start_at", "preliminary_end_at",
            "final_start_at", "final_end_at",
        )
        widgets = {
            field_name: forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            )
            for field_name in (
                "preliminary_start_at", "preliminary_end_at",
                "final_start_at", "final_end_at",
            )
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in self.Meta.widgets:
            self.fields[field_name].input_formats = ["%Y-%m-%dT%H:%M"]


class ElectionAdminForm(forms.ModelForm):
    class ElectionType(models.TextChoices):
        PRESIDENT = "president", "会長"
        REPRESENTATIVE_GENERAL = (
            "representative_general",
            "代議員（一般枠）",
        )
        REPRESENTATIVE_CORPORATE = (
            "representative_corporate",
            "代議員（企業枠）",
        )

    election_type = forms.ChoiceField(
        label="Office",
        choices=ElectionType.choices,
    )

    class Meta:
        model = Election
        fields = (
            "cycle",
            "election_type",
            "phase",
            "status",
            "start_at",
            "end_at",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            return
        if self.instance.office == Election.Office.PRESIDENT:
            self.initial["election_type"] = self.ElectionType.PRESIDENT
        elif (
            self.instance.representative_category
            == Election.RepresentativeCategory.GENERAL
        ):
            self.initial["election_type"] = (
                self.ElectionType.REPRESENTATIVE_GENERAL
            )
        elif (
            self.instance.representative_category
            == Election.RepresentativeCategory.CORPORATE
        ):
            self.initial["election_type"] = (
                self.ElectionType.REPRESENTATIVE_CORPORATE
            )

    def clean(self):
        cleaned_data = super().clean()
        election_type = cleaned_data.get("election_type")
        if election_type == self.ElectionType.PRESIDENT:
            self.instance.office = Election.Office.PRESIDENT
            self.instance.representative_category = ""
        elif election_type == self.ElectionType.REPRESENTATIVE_GENERAL:
            self.instance.office = Election.Office.REPRESENTATIVE
            self.instance.representative_category = (
                Election.RepresentativeCategory.GENERAL
            )
        elif election_type == self.ElectionType.REPRESENTATIVE_CORPORATE:
            self.instance.office = Election.Office.REPRESENTATIVE
            self.instance.representative_category = (
                Election.RepresentativeCategory.CORPORATE
            )
        return cleaned_data


class MemberCsvImportForm(forms.Form):
    cycle = forms.ModelChoiceField(
        queryset=ElectionCycle.objects.order_by("-year"), label="選挙年度"
    )
    csv_file = forms.FileField(
        label="会員名簿CSV",
        help_text="UTF-8またはCP932（ExcelのCSV）に対応しています。",
    )

    def clean_csv_file(self):
        uploaded_file = self.cleaned_data["csv_file"]
        if not uploaded_file.name.lower().endswith(".csv"):
            raise forms.ValidationError("CSVファイルを選択してください。")
        return uploaded_file


class CycleMemberCsvImportForm(forms.Form):
    csv_file = forms.FileField(
        label="会員リストCSV",
        help_text="UTF-8またはCP932（ExcelのCSV）に対応しています。",
    )

    def clean_csv_file(self):
        uploaded_file = self.cleaned_data["csv_file"]
        if not uploaded_file.name.lower().endswith(".csv"):
            raise forms.ValidationError("CSVファイルを選択してください。")
        return uploaded_file


class PaperBallotForm(forms.Form):
    candidates = forms.ModelMultipleChoiceField(
        label="投票先",
        queryset=Candidate.objects.none(),
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, election, **kwargs):
        super().__init__(*args, **kwargs)
        self.election = election
        self.fields["candidates"].queryset = (
            Candidate.objects
            .filter(
                election=election,
                status__in=get_valid_candidate_statuses(election),
            )
            .select_related("member")
            .order_by("member__member_no")
        )
        self.fields["candidates"].help_text = (
            f"1枚の書面票について、最大{election.vote_limit}名まで選択できます。"
        )

    def clean_candidates(self):
        candidates = self.cleaned_data["candidates"]
        error = validate_vote(
            self.election,
            [candidate.pk for candidate in candidates],
        )
        if error:
            raise forms.ValidationError(error)
        return candidates
